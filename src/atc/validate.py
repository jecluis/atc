"""Checking every unit in every config source."""

from collections.abc import Sequence
from dataclasses import dataclass

from atc.context import BuildContext
from atc.errors import ConfigError
from atc.layers import Layer, build_account_layer, build_project_layer
from atc.links import wanted_instructions
from atc.refs import locate
from atc.resolve import resolve
from atc.rules import dead_rules
from atc.sources import Sources


@dataclass(frozen=True, slots=True)
class Validation:
    """
    What checking the sources found.

    ``units`` counts the unit files examined. ``errors`` and ``warnings`` are
    messages, each given once however many units run into the same problem.
    """

    units: int
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


def validate_sources(sources: Sources, *, lax: bool) -> Validation:
    """
    Load every ``*.json`` under every source's ``conf`` and resolve each as a root.

    Resolving a unit follows its imports, across sources, so a unit with a
    broken import is reported along with every unit that reaches it. All
    problems are collected, not just the first. Set ``lax`` to report unknown
    settings keys as warnings instead of errors.
    """
    # Dicts as ordered sets: the first report of a message is the one kept.
    errors: dict[str, None] = {}
    warnings = dict.fromkeys(sources.warnings)
    units = 0
    for source in sources.items:
        for path in sorted(source.conf.rglob("*.json")):
            if not path.is_file():
                continue
            units += 1
            try:
                unit = locate(source, path.relative_to(source.conf).as_posix())
                resolution = resolve(sources, [unit], lax=lax)
            except ConfigError as exc:
                errors[str(exc)] = None
            else:
                warnings.update(dict.fromkeys(resolution.warnings))
    return Validation(units=units, errors=tuple(errors), warnings=tuple(warnings))


def validate_config(ctx: BuildContext, *, lax: bool) -> Validation:
    """
    Check every unit in the sources, then every account's and project's layer.

    Each account's instructions file, if it asks for one, must exist. Building an
    account layer finds the account's unit, which must exist, and
    checks each unit it merges against the policy for the layer. A project's
    layer is built once for each account the project allows, or for every
    account when it allows none in particular. A project without a unit has no
    layer to build. Permission rules that can never apply are reported as
    warnings, for each account layer and for each project layer together with
    its account's. All problems are collected, once each. Set ``lax`` to report
    unknown settings keys as warnings instead of errors.
    """
    checked = validate_sources(ctx.sources, lax=lax)
    errors = dict.fromkeys(checked.errors)
    warnings = dict.fromkeys(checked.warnings)
    warnings.update(dict.fromkeys(ctx.registry.warnings))
    account_layers: dict[str, Layer] = {}
    for account in ctx.registry.accounts.values():
        try:
            _ = wanted_instructions(ctx, account)
        except ConfigError as exc:
            errors[str(exc)] = None
        try:
            layer = build_account_layer(ctx, account, lax=lax)
        except ConfigError as exc:
            errors[str(exc)] = None
        else:
            account_layers[account.name] = layer
            warnings.update(dict.fromkeys(layer.warnings))
            warnings.update(_rule_warnings([layer]))
    for project in ctx.registry.projects.values():
        names = (
            project.accounts
            if project.accounts is not None
            else tuple(ctx.registry.accounts)
        )
        for name in names:
            try:
                built = build_project_layer(
                    ctx, project, ctx.registry.accounts[name], lax=lax
                )
            except ConfigError as exc:
                errors[str(exc)] = None
            else:
                if built is not None:
                    warnings.update(dict.fromkeys(built.warnings))
                    account_layer = account_layers.get(name)
                    layers = (
                        [built] if account_layer is None else [account_layer, built]
                    )
                    warnings.update(_rule_warnings(layers))
    return Validation(
        units=checked.units, errors=tuple(errors), warnings=tuple(warnings)
    )


def _rule_warnings(layers: Sequence[Layer]) -> dict[str, None]:
    """List the messages about rules that can never apply in ``layers``."""
    return dict.fromkeys(warning.message() for warning in dead_rules(layers))
