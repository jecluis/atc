"""Checking every unit in every config source."""

from dataclasses import dataclass

from atc.context import BuildContext
from atc.errors import ConfigError
from atc.layers import build_account_layer, build_project_layer
from atc.refs import locate
from atc.resolve import resolve
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

    Building an account layer finds the account's unit, which must exist, and
    checks each unit it merges against the policy for the layer. A project's
    layer is built once for each account the project allows, or for every
    account when it allows none in particular. A project without a unit has no
    layer to build. All problems are collected, once each. Set ``lax`` to report
    unknown settings keys as warnings instead of errors.
    """
    checked = validate_sources(ctx.sources, lax=lax)
    errors = dict.fromkeys(checked.errors)
    warnings = dict.fromkeys(checked.warnings)
    warnings.update(dict.fromkeys(ctx.registry.warnings))
    for account in ctx.registry.accounts.values():
        try:
            layer = build_account_layer(ctx, account, lax=lax)
        except ConfigError as exc:
            errors[str(exc)] = None
        else:
            warnings.update(dict.fromkeys(layer.warnings))
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
    return Validation(
        units=checked.units, errors=tuple(errors), warnings=tuple(warnings)
    )
