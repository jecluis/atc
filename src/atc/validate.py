"""Checking every unit in every config source."""

from collections.abc import Sequence
from dataclasses import dataclass

from atc.context import BuildContext
from atc.errors import ConfigError
from atc.jsonval import JsonObject
from atc.layers import Layer, build_account_layer, build_project_layer
from atc.links import wanted_instructions
from atc.protection import unprotected
from atc.refs import Located, locate
from atc.rendered import render_templates
from atc.resolve import resolve
from atc.rules import dead_rules, rule_shape_warnings
from atc.skills import discover_skills, wanted_skill_links
from atc.sources import Sources
from atc.template import check_syntax


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
    broken import is reported along with every unit that reaches it. The
    references in each unit's settings are checked for their form, and its
    permission rules for ones better written another way, whether or not a
    layer uses the unit. All problems are collected, not just the first.
    Set ``lax`` to report unknown settings keys as warnings instead of errors.
    """
    # Dicts as ordered sets: the first report of a message is the one kept.
    errors: dict[str, None] = {}
    warnings = dict.fromkeys(sources.warnings)
    shapes: dict[str, None] = {}

    def check(unit: Located, settings: JsonObject) -> JsonObject:
        check_syntax(settings, origin=unit.display)
        shapes.update(dict.fromkeys(rule_shape_warnings(settings, unit.display)))
        return settings

    units = 0
    for source in sources.items:
        for path in sorted(source.conf.rglob("*.json")):
            if not path.is_file():
                continue
            units += 1
            try:
                unit = locate(source, path.relative_to(source.conf).as_posix())
                resolution = resolve(sources, [unit], lax=lax, transform=check)
            except ConfigError as exc:
                errors[str(exc)] = None
            else:
                warnings.update(dict.fromkeys(resolution.warnings))
    warnings.update(shapes)
    return Validation(units=units, errors=tuple(errors), warnings=tuple(warnings))


def validate_config(ctx: BuildContext, *, lax: bool) -> Validation:
    """
    Check every unit in the sources, then every account's and project's layer.

    Each account's instructions file, if it asks for one, must exist, and so must
    each skill it selects, with no two sharing a name. Every templated asset must
    render for every account. Skills that are skipped,
    or whose front matter looks wrong, are reported as warnings. Building an
    account layer finds the account's unit, which must exist, and
    checks each unit it merges against the policy for the layer. A project's
    layer is built once for each account the project allows, or for every
    account when it allows none in particular. A project without a unit has no
    layer to build. Permission rules that can never apply are reported as
    warnings, for each account layer and for each project layer together with
    its account's, and so is each of atc's own directories that an account's
    settings leave writable. All problems are collected, once each. Set ``lax``
    to report unknown settings keys as warnings instead of errors.
    """
    checked = validate_sources(ctx.sources, lax=lax)
    errors = dict.fromkeys(checked.errors)
    warnings = dict.fromkeys(checked.warnings)
    warnings.update(dict.fromkeys(ctx.registry.warnings))
    catalog = discover_skills(ctx.sources)
    warnings.update(dict.fromkeys(catalog.warnings))
    account_layers: dict[str, Layer] = {}
    for account in ctx.registry.accounts.values():
        try:
            _ = wanted_instructions(ctx, account)
        except ConfigError as exc:
            errors[str(exc)] = None
        try:
            _ = wanted_skill_links(ctx, account, catalog)
        except ConfigError as exc:
            errors[str(exc)] = None
        try:
            _ = render_templates(ctx, account)
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
            exposed = unprotected(ctx, account.name, layer.settings)
            warnings.update(dict.fromkeys(exposed))
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
