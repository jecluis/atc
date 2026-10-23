"""References: how an import or a registry entry names a unit or asset in a source."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from atc.errors import PathError
from atc.sources import Source, Sources

UNIT_SUFFIX = ".json"

type RefKind = Literal["unit", "asset"]
"""What a reference names: a unit under ``conf``, or a file under ``assets``."""

_QUALIFIED = re.compile(r"@(?P<name>[^/]+)/(?P<rel>.+)", re.DOTALL)


@dataclass(frozen=True, slots=True)
class Located:
    """
    A unit or asset file in a config source.

    ``path`` is absolute with symbolic links resolved, so two references to one
    file give the same ``path``. ``display`` is how messages name it:
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


def resolve_registry(
    sources: Sources, text: str, kind: RefKind = "unit"
) -> Located | None:
    """
    Find the unit or asset that ``text``, written outside any unit, refers to.

    A unit is a ``.json`` file under a source's ``conf``, and an asset is any
    file under its ``assets``, as ``kind`` says. ``@name/path`` names the
    source. Without ``@``, exactly one source may contain the file. ``None``
    means no source does, which the caller may treat as an error or not.

    Raises:
        PathError: ``text`` is malformed or does not satisfy :func:`locate`
            (for an asset, apart from the extension), a qualified reference
            names no existing file, or several sources contain the file.

    """
    qualified = _qualified(sources, text, kind)
    if qualified is not None:
        found = _locate(*qualified, text, kind)
        if not found.path.is_file():
            raise PathError(f"'{text}': no such {kind}")
        return found

    matches: list[Located] = []
    for source in sources.items:
        candidate = _locate(source, text, text, kind)
        if candidate.path.is_file():
            matches.append(candidate)
    if len(matches) > 1:
        names = ", ".join(match.display for match in matches)
        raise PathError(
            f"'{text}' exists as {names}; add an @name/ prefix to say which"
        )
    return matches[0] if matches else None


def _qualified(
    sources: Sources, text: str, kind: RefKind = "unit"
) -> tuple[Source, str] | None:
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
        expected = f"unit{UNIT_SUFFIX}" if kind == "unit" else "file"
        raise PathError(f"'{text}': expected @name/path/to/{expected}")
    try:
        return sources.named(parts["name"]), parts["rel"]
    except PathError as exc:
        raise PathError(f"'{text}': {exc}") from exc


def _locate(
    source: Source, reference: str, text: str, kind: RefKind = "unit"
) -> Located:
    """Place ``reference`` in ``source``, naming ``text`` in any error."""
    if not reference:
        raise PathError(f"empty {kind} reference")
    if kind == "unit" and not reference.endswith(UNIT_SUFFIX):
        raise PathError(f"'{text}': unit references must end in {UNIT_SUFFIX}")
    candidate = Path(reference)
    if candidate.is_absolute():
        raise PathError(f"'{text}': {kind} references must be relative")

    anchor = (source.conf if kind == "unit" else source.assets).resolve()
    resolved = (anchor / candidate).resolve()
    if not resolved.is_relative_to(anchor):
        raise PathError(f"'{text}': resolves outside {anchor}")
    return Located(
        source, resolved, source.display(resolved.relative_to(anchor).as_posix())
    )
