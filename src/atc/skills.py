"""Skills: the directories under a source's ``skills`` that accounts can link."""

import re
from dataclasses import dataclass
from pathlib import Path

from atc.backends import backend_for
from atc.context import BuildContext
from atc.errors import ConfigError, PathError, SourceError, UnitError
from atc.refs import SKILL_FILE, resolve_registry
from atc.registry import Account
from atc.sources import Source, Sources
from atc.yamlval import parse_yaml

_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*")
_FENCE = "---"


@dataclass(frozen=True, slots=True)
class Skill:
    """
    One skill: the directory ``name`` in ``source``'s ``skills``.

    ``path`` is the directory, absolute with symbolic links resolved, and
    ``description`` is what the front matter of its ``SKILL.md`` says, or ``""``.
    """

    name: str
    source: Source
    path: Path
    description: str

    @property
    def reference(self) -> str:
        """How a registry reference names it with its source: ``@source/name``."""
        return self.source.display(self.name)


@dataclass(frozen=True, slots=True)
class Catalog:
    """
    The skills found in the sources, ordered by source and then by name.

    ``warnings`` describe what was skipped, and what looks wrong in a skill that
    counts anyway.
    """

    skills: tuple[Skill, ...]
    warnings: tuple[str, ...]

    def resolve(self, ref: str, sources: Sources) -> Skill:
        """
        Find the skill that the registry's reference ``ref`` names.

        ``ref`` is a directory name under ``skills``, with an ``@source/``
        prefix when several sources have it.

        Raises:
            PathError: ``ref`` is malformed, names no skill in the catalog, is
                in several sources, or names a directory whose name cannot be a
                skill's.

        """
        found = resolve_registry(sources, ref, "skill")
        if found is None:
            raise PathError(f"'{ref}': no such skill")
        for skill in self.skills:
            if skill.source == found.source and skill.name == found.path.name:
                return skill
        if not _NAME.fullmatch(found.path.name):
            raise PathError(f"'{ref}': {_invalid_name(found.path.name)}")
        raise PathError(f"'{ref}': no such skill")


def discover_skills(sources: Sources) -> Catalog:
    """
    Find the skills in every source.

    A skill is a directory directly under a source's ``skills`` that holds a
    ``SKILL.md`` and is named with lowercase letters, digits, ``_`` and ``-``,
    starting with a letter or a digit. A directory with another name is skipped,
    with a warning. The front matter of ``SKILL.md`` only supplies the
    description, so a skill whose front matter cannot be read, or whose ``name``
    is not its directory's, still counts, with a warning.

    Raises:
        SourceError: a ``skills`` directory cannot be read.

    """
    skills: list[Skill] = []
    warnings: list[str] = []
    for source in sources.items:
        for entry in _subdirectories(source.skills):
            skill_file = entry / SKILL_FILE
            if not skill_file.is_file():
                continue
            if not _NAME.fullmatch(entry.name):
                warnings.append(f"{entry}: {_invalid_name(entry.name)}; skipped")
                continue
            description, notes = _read(skill_file, entry.name)
            warnings.extend(notes)
            skills.append(Skill(entry.name, source, entry.resolve(), description))
    return Catalog(tuple(skills), tuple(warnings))


def wanted_skill_links(
    ctx: BuildContext, account: Account, catalog: Catalog
) -> dict[Path, Path]:
    """
    Say where ``account``'s skills are to be linked, and to what.

    Each entry is a link in the account's skills directory, named for a skill
    the account selects, and its value is the skill's directory. They come in the
    order of the account's ``skills``.

    Raises:
        PathError: a selected skill is missing or ambiguous, as
            :meth:`Catalog.resolve` raises.
        ConfigError: two selected skills have the same name.

    """
    where = f"{ctx.registry.path}: account '{account.name}'"
    directory = backend_for(account.agent, ctx.home).skills_dir(account)
    chosen: dict[Path, Skill] = {}
    for reference in account.skills:
        try:
            skill = catalog.resolve(reference, ctx.sources)
        except PathError as exc:
            raise PathError(f"{where}: skills: {exc}") from exc
        other = chosen.setdefault(directory / skill.name, skill)
        if other.path != skill.path:
            message = (
                f"{where}: skills: {other.reference} and {skill.reference} are "
                f"both named '{skill.name}'; select one of them"
            )
            raise ConfigError(message)
    return {entry: skill.path for entry, skill in chosen.items()}


def _invalid_name(name: str) -> str:
    return (
        f"'{name}' is not a valid skill name; names are lowercase letters, "
        "digits, '_' and '-', and start with a letter or a digit"
    )


def _subdirectories(directory: Path) -> list[Path]:
    """List the directories in ``directory``, sorted, or none if it is missing."""
    try:
        return sorted(entry for entry in directory.iterdir() if entry.is_dir())
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise SourceError(f"{directory}: cannot be read: {exc}") from exc


def _read(skill_file: Path, name: str) -> tuple[str, tuple[str, ...]]:
    """Read ``skill_file``'s description, and what is wrong with its front matter."""
    try:
        text = skill_file.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        return "", (f"{skill_file}: cannot be read: {exc}",)
    origin = f"{skill_file}: front matter"
    try:
        block = _front_matter(text, origin)
        front = None if block is None else parse_yaml(block, origin)
    except ConfigError as exc:
        return "", (str(exc),)
    if front is None:
        return "", ()
    if not isinstance(front, dict):
        return "", (f"{origin}: expected a mapping",)
    notes: list[str] = []
    if "name" in front and front["name"] != name:
        message = (
            f"{skill_file}: the front matter's name {front['name']!r} differs "
            f"from the directory name '{name}'"
        )
        notes.append(message)
    description = front.get("description")
    return description if isinstance(description, str) else "", tuple(notes)


def _front_matter(text: str, origin: str) -> str | None:
    """
    Cut the front matter out of ``text``: what lies between two ``---`` lines.

    The first line must be one. ``None`` means there is no front matter.

    Raises:
        UnitError: the front matter is never closed.

    """
    lines = text.splitlines()
    if not lines or lines[0].rstrip() != _FENCE:
        return None
    for end, line in enumerate(lines[1:], start=1):
        if line.rstrip() == _FENCE:
            return "\n".join(lines[1:end])
    raise UnitError(f"{origin}: not closed by a '{_FENCE}' line")
