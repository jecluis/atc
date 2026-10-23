"""Unit references: how an import or a registry entry names a unit in a source."""

import re
from dataclasses import dataclass
from pathlib import Path

from atc.errors import PathError
from atc.sources import Source, Sources

UNIT_SUFFIX = ".json"

_QUALIFIED = re.compile(r"@(?P<name>[^/]+)/(?P<rel>.+)", re.DOTALL)


@dataclass(frozen=True, slots=True)
class Located:
    """
    A unit file in a config source.

    ``path`` is absolute with symbolic links resolved, so two references to one
    unit give the same ``path``. ``display`` is how messages name the unit:
    ``projects/x.json``, or ``@bravo/projects/x.json`` when the source has a
    name.
    """

    source: Source
    path: Path
    display: str


def locate(source: Source, reference: str) -> Located:
    """
    Place ``reference``, a path relative to ``source``'s ``conf`` directory.

    The file need not exist. ``reference`` is taken as a plain path, so an
    ``@`` in it means nothing special.

    Raises:
        PathError: the reference is empty or absolute, does not end in
            ``.json``, or resolves outside ``conf``, whether through ``..`` or
            through a symbolic link.

    """
    return _locate(source, reference, reference)


def resolve_import(sources: Sources, origin: Source, text: str) -> Located:
    """
    Turn the import ``text``, written in a unit of ``origin``, into a unit.

    ``path/to/unit.json`` is in ``origin``. ``@name/path/to/unit.json`` is in
    the source called ``name``, which only exists in the multi layout. Whether
    the unit exists is not checked.

    Raises:
        PathError: ``text`` is malformed, names a source that does not exist,
            or does not satisfy :func:`locate`.

    """
    qualified = _qualified(sources, text)
    source, reference = qualified if qualified is not None else (origin, text)
    return _locate(source, reference, text)


def resolve_registry(sources: Sources, text: str) -> Located | None:
    """
    Find the unit that ``text``, written outside any unit, refers to.

    ``@name/path/to/unit.json`` names the source. Without ``@``, exactly one
    source may contain the unit. ``None`` means no source does, which the
    caller may treat as an error or not.

    Raises:
        PathError: ``text`` is malformed or does not satisfy :func:`locate`, a
            qualified reference names no existing unit, or several sources
            contain the unit.

    """
    qualified = _qualified(sources, text)
    if qualified is not None:
        found = _locate(*qualified, text)
        if not found.path.is_file():
            raise PathError(f"'{text}': no such unit")
        return found

    matches: list[Located] = []
    for source in sources.items:
        candidate = _locate(source, text, text)
        if candidate.path.is_file():
            matches.append(candidate)
    if len(matches) > 1:
        names = ", ".join(match.display for match in matches)
        raise PathError(
            f"'{text}' exists as {names}; add an @name/ prefix to say which"
        )
    return matches[0] if matches else None


def _qualified(sources: Sources, text: str) -> tuple[Source, str] | None:
    """Split ``@name/path`` into its source and path, or ``None`` without ``@``."""
    if not text.startswith("@"):
        return None
    if sources.layout == "single":
        message = (
            f"'{text}': an @name/ reference names one of several config "
            f"sources, and {sources.conf_d} is a single source"
        )
        raise PathError(message)
    parts = _QUALIFIED.fullmatch(text)
    if parts is None:
        raise PathError(f"'{text}': expected @name/path/to/unit{UNIT_SUFFIX}")
    try:
        return sources.named(parts["name"]), parts["rel"]
    except PathError as exc:
        raise PathError(f"'{text}': {exc}") from exc


def _locate(source: Source, reference: str, text: str) -> Located:
    """Place ``reference`` in ``source``, naming ``text`` in any error."""
    if not reference:
        raise PathError("empty unit reference")
    if not reference.endswith(UNIT_SUFFIX):
        raise PathError(f"'{text}': unit references must end in {UNIT_SUFFIX}")
    candidate = Path(reference)
    if candidate.is_absolute():
        raise PathError(f"'{text}': unit references must be relative")

    anchor = source.conf.resolve()
    resolved = (anchor / candidate).resolve()
    if not resolved.is_relative_to(anchor):
        raise PathError(f"'{text}': resolves outside {anchor}")
    return Located(
        source, resolved, source.display(resolved.relative_to(anchor).as_posix())
    )
