"""References: how an import or a registry entry names a unit or asset in a source."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from atc.errors import PathError
from atc.sources import Source, Sources

UNIT_SUFFIX = ".json"
SKILL_FILE = "SKILL.md"
"""The file that makes a directory under ``skills`` a skill."""

type RefKind = Literal["unit", "asset", "skill"]
"""
What a reference names: a unit under ``conf``, a file under ``assets``, or a
skill, a directory under ``skills`` that holds a ``SKILL.md``.
"""

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
    Find the unit, asset or skill that ``text``, written outside any unit, names.

    A unit is a ``.json`` file under a source's ``conf``, an asset is any file
    under its ``assets``, and a skill is a directory under its ``skills`` that
    holds a ``SKILL.md``, as ``kind`` says. ``@name/path`` names the source.
    Without ``@``, exactly one source may contain it. ``None`` means no source
    does, which the caller may treat as an error or not.

    Raises:
        PathError: ``text`` is malformed or does not satisfy :func:`locate`
            (for an asset, apart from the extension; a skill is one directory
            name), a qualified reference names nothing that exists, or several
            sources contain it.

    """
    qualified = _qualified(sources, text, kind)
    if qualified is not None:
        found = _locate(*qualified, text, kind)
        if not _exists(found, kind):
            raise PathError(f"'{text}': no such {kind}")
        return found

    matches: list[Located] = []
    for source in sources.items:
        candidate = _locate(source, text, text, kind)
        if _exists(candidate, kind):
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
        match kind:
            case "unit":
                expected = f"path/to/unit{UNIT_SUFFIX}"
            case "asset":
                expected = "path/to/file"
            case "skill":
                expected = "<skill>"
        raise PathError(f"'{text}': expected @name/{expected}")
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
    if kind == "skill" and ("/" in reference or reference in {".", ".."}):
        raise PathError(f"'{text}': a skill reference is one directory name")

    match kind:
        case "unit":
            anchor = source.conf.resolve()
        case "asset":
            anchor = source.assets.resolve()
        case "skill":
            anchor = source.skills.resolve()
    resolved = (anchor / candidate).resolve()
    if not resolved.is_relative_to(anchor):
        raise PathError(f"'{text}': resolves outside {anchor}")
    return Located(
        source, resolved, source.display(resolved.relative_to(anchor).as_posix())
    )


def _exists(found: Located, kind: RefKind) -> bool:
    """Say whether what ``found`` places is there: a file, or a skill's directory."""
    if kind == "skill":
        return (found.path / SKILL_FILE).is_file()
    return found.path.is_file()
