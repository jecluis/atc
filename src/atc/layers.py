"""Settings layers, and the policy on which keys each layer may set."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from atc.context import BuildContext
from atc.errors import PolicyError
from atc.jsonval import JsonObject, JsonValue
from atc.refs import Located
from atc.registry import Account, Project, account_unit, project_unit
from atc.resolve import UnitTransform, resolve
from atc.template import VarValue, expand
from atc.variables import layer_variables, with_source

type LayerKind = Literal["account", "project", "session"]

POLICY: Mapping[str, frozenset[LayerKind]] = {
    "/autoMemoryDirectory": frozenset({"account"}),
    "/sandbox/allowUnsandboxedCommands": frozenset({"project", "session"}),
}
"""JSON pointers to settings, each with the layers that must not set it."""


@dataclass(frozen=True, slots=True)
class Layer:
    """One layer of settings, merged. ``warnings`` did not stop the build."""

    kind: LayerKind
    settings: JsonObject
    warnings: tuple[str, ...]


def check_policy(settings: JsonObject, kind: LayerKind, origin: str) -> None:
    """
    Refuse ``settings`` if it sets a key that layer ``kind`` must not.

    ``origin`` names where the settings are from, for the message.

    Raises:
        PolicyError: a key that :data:`POLICY` refuses in this layer is set.

    """
    for pointer, refused in POLICY.items():
        if kind in refused and _is_set(settings, pointer):
            message = f"{origin}: {pointer} is not allowed in the {kind} layer"
            raise PolicyError(message)


def build_account_layer(ctx: BuildContext, account: Account, *, lax: bool) -> Layer:
    """
    Merge the unit of ``account`` and everything it imports.

    The references in each unit are expanded for ``account``, and the unit is
    then checked against the policy for the account layer. Set ``lax`` to
    report unknown settings keys as warnings instead of errors.

    Raises:
        PathError: the account's unit does not exist, or several sources have it.
        TemplateError: a unit has a reference that cannot be expanded.
        PolicyError: a unit sets a key the account layer must not.
        ConfigError: as :func:`atc.resolve.resolve`, for the units themselves.

    """
    root = account_unit(ctx.sources, ctx.registry, account)
    transform = _unit_transform(
        "account", layer_variables(ctx, account), f"account layer for {account.name}"
    )
    resolution = resolve(ctx.sources, [root], lax=lax, transform=transform)
    return Layer("account", resolution.settings, resolution.warnings)


def build_project_layer(
    ctx: BuildContext, project: Project, account: Account, *, lax: bool
) -> Layer | None:
    """
    Merge the unit of ``project`` and everything it imports, for ``account``.

    Every unit is expanded with the project's variables as well as the
    account's, and then checked against the policy for the project layer. The
    result is ``None`` when the project has no unit: the registry left the unit
    to the default, and no source has it. Set ``lax`` to report unknown
    settings keys as warnings instead of errors.

    Raises:
        PathError: a unit the registry names does not exist, or several sources
            have the project's unit.
        TemplateError: a unit has a reference that cannot be expanded.
        PolicyError: a unit sets a key the project layer must not.
        ConfigError: as :func:`atc.resolve.resolve`, for the units themselves.

    """
    root = project_unit(ctx.sources, ctx.registry, project)
    if root is None:
        return None
    transform = _unit_transform(
        "project",
        layer_variables(ctx, account, project),
        f"project layer for {project.name} with account {account.name}",
    )
    resolution = resolve(ctx.sources, [root], lax=lax, transform=transform)
    return Layer("project", resolution.settings, resolution.warnings)


def _unit_transform(
    kind: LayerKind, variables: Mapping[str, VarValue], layer: str
) -> UnitTransform:
    """
    Make the transform that prepares a unit for layer ``kind``.

    It expands the unit's references, with ``variables`` and the unit's own
    source, then applies the policy. ``layer`` names the layer in error messages.
    """

    def prepare(unit: Located, settings: JsonObject) -> JsonObject:
        expanded = expand(
            settings,
            with_source(variables, unit.source),
            origin=f"{unit.display} ({layer})",
        )
        check_policy(expanded, kind, unit.display)
        return expanded

    return prepare


def _is_set(settings: JsonObject, pointer: str) -> bool:
    """Say whether ``pointer``, a JSON pointer, leads to a value in ``settings``."""
    node: JsonValue = settings
    for key in pointer.removeprefix("/").split("/"):
        if not isinstance(node, dict) or key not in node:
            return False
        node = node[key]
    return True
