"""Settings layers, and the policy on which keys each layer may set."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from atc.context import BuildContext
from atc.errors import PolicyError
from atc.jsonval import JsonObject, JsonValue
from atc.refs import Located
from atc.registry import Account, account_unit
from atc.resolve import UnitTransform, resolve

type LayerKind = Literal["account", "project", "session"]

POLICY: Mapping[str, frozenset[LayerKind]] = {
    "/autoMemoryDirectory": frozenset({"account"}),
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

    Each unit is checked against the policy for the account layer. Set ``lax``
    to report unknown settings keys as warnings instead of errors.

    Raises:
        PathError: the account's unit does not exist, or several sources have it.
        PolicyError: a unit sets a key the account layer must not.
        ConfigError: as :func:`atc.resolve.resolve`, for the units themselves.

    """
    root = account_unit(ctx.sources, ctx.registry, account)
    resolution = resolve(
        ctx.sources, [root], lax=lax, transform=_policy_transform("account")
    )
    return Layer("account", resolution.settings, resolution.warnings)


def _policy_transform(kind: LayerKind) -> UnitTransform:
    """Make the transform that applies the policy of layer ``kind`` to a unit."""

    def check(unit: Located, settings: JsonObject) -> JsonObject:
        check_policy(settings, kind, unit.display)
        return settings

    return check


def _is_set(settings: JsonObject, pointer: str) -> bool:
    """Say whether ``pointer``, a JSON pointer, leads to a value in ``settings``."""
    node: JsonValue = settings
    for key in pointer.removeprefix("/").split("/"):
        if not isinstance(node, dict) or key not in node:
            return False
        node = node[key]
    return True
