"""Checking every unit in every config source."""

from dataclasses import dataclass

from atc.errors import ConfigError
from atc.refs import locate
from atc.registry import Registry, account_unit
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


def validate_config(sources: Sources, registry: Registry, *, lax: bool) -> Validation:
    """
    Check every unit in the sources, then that every account has its unit.

    An account's unit is missing when no source has it, and ambiguous when
    several do. All problems are collected, once each. Set ``lax`` to report
    unknown settings keys as warnings instead of errors.
    """
    checked = validate_sources(sources, lax=lax)
    errors = dict.fromkeys(checked.errors)
    warnings = dict.fromkeys((*checked.warnings, *registry.warnings))
    for account in registry.accounts.values():
        try:
            _ = account_unit(sources, registry, account)
        except ConfigError as exc:
            errors[str(exc)] = None
    return Validation(
        units=checked.units, errors=tuple(errors), warnings=tuple(warnings)
    )
