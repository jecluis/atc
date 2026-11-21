"""Finding the units that ``atc account units`` and ``atc prj units`` add."""

from collections.abc import Iterable
from pathlib import Path

from atc.errors import PathError
from atc.refs import Located, resolve_registry
from atc.sources import Sources


def extra_unit(sources: Sources, reference: str) -> Located:
    """
    Find the unit ``reference``, to add it as an extra unit.

    Raises:
        PathError: it is malformed, or several sources have it, or none does.

    """
    found = resolve_registry(sources, reference)
    if found is None:
        message = (
            f"'{reference}': no config source has it; a unit reference is a path "
            "under a source's conf/, such as hosts/NAME.json"
        )
        raise PathError(message)
    return found


def unit_paths(sources: Sources, references: Iterable[str]) -> set[Path]:
    """Give the files that ``references`` name, leaving out those that name none."""
    paths: set[Path] = set()
    for reference in references:
        try:
            found = resolve_registry(sources, reference)
        except PathError:
            continue
        if found is not None:
            paths.add(found.path)
    return paths


def new_units(
    sources: Sources, have: Iterable[str], references: Iterable[str]
) -> list[str]:
    """
    Give those of ``references`` that name a unit none of ``have`` names.

    Each is given once, as it was written, in the order given.

    Raises:
        PathError: one of ``references`` is malformed, or several sources have
            it, or none does.

    """
    known = unit_paths(sources, have)
    added: list[str] = []
    for reference in references:
        unit = extra_unit(sources, reference)
        if unit.path not in known:
            known.add(unit.path)
            added.append(reference)
    return added
