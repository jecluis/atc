"""Resolution of an import graph into a single settings mapping."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import final

from atc.errors import CycleError, MergeError, PathError, UnitError
from atc.jsonval import JsonObject
from atc.merge import merge_objects
from atc.refs import Located, resolve_import
from atc.sources import Sources
from atc.unit import load_unit, unknown_settings_keys

type UnitTransform = Callable[[Located, JsonObject], JsonObject]
"""
Rewrites or checks one unit's settings as it is merged.

It gets the unit and its ``settings`` and returns the settings to merge, or
raises to refuse the unit.
"""


@dataclass(frozen=True, slots=True)
class Resolution:
    """
    The outcome of resolving a set of units.

    ``settings`` is the merged settings of the units. ``warnings``
    holds diagnostics that did not stop the resolution, such as unknown
    settings keys tolerated under ``lax``.
    """

    settings: JsonObject
    warnings: tuple[str, ...]


def resolve(
    sources: Sources,
    roots: Sequence[Located],
    *,
    lax: bool,
    transform: UnitTransform | None = None,
) -> Resolution:
    """
    Merge the units ``roots``, following their imports.

    Imports may lead into other sources of ``sources``. Each unit contributes
    exactly once, at the first point the depth-first walk reaches it. A unit's
    imports are walked, in order, before the unit's own settings, so an
    importing unit overrides what it imports; ``roots`` are walked left to
    right, so a later one overrides an earlier one.

    Those two rules meet when a root has already been pulled in as an import of
    an earlier one. Contributing once wins — the unit keeps the position it
    already had — and a warning records that its later position had no effect.

    Set ``lax`` to report unknown settings keys as warnings instead of errors.
    ``transform``, if given, is applied to each unit's settings before they are
    checked for unknown keys and merged.

    Raises:
        PathError: an import is malformed or names a unit that is missing.
        UnitError: a unit is malformed, or carries an unknown settings key.
        CycleError: the imports form a cycle.
        MergeError: two units give the same setting values of differing types.

    """
    walker = _Walker(sources, lax=lax, transform=transform)
    for root in roots:
        walker.request(root)
    return Resolution(settings=walker.settings, warnings=tuple(walker.warnings))


@final
class _Walker:
    """Depth-first traversal of the import graph, accumulating merged settings."""

    def __init__(
        self, sources: Sources, *, lax: bool, transform: UnitTransform | None
    ) -> None:
        self._sources = sources
        self._lax = lax
        self._transform = transform
        self._done: set[Path] = set()
        self._active: dict[Path, Located] = {}
        self.settings: JsonObject = {}
        self.warnings: list[str] = []

    def request(self, unit: Located) -> None:
        """
        Merge a unit the user named directly.

        A unit an earlier root already pulled in keeps the position it had, so
        naming it again cannot move it later in the merge order. That is worth
        saying out loud, because the user asked for something the merge-once
        rule cannot give them.
        """
        if unit.path in self._done:
            cause = "already merged as an import of an earlier unit"
            effect = "naming it here does not change the merge order"
            self.warnings.append(f"'{unit.display}': {cause}, so {effect}")
            return
        self.visit(unit, None, importer=None)

    def visit(
        self, unit: Located, reference: str | None, importer: Located | None
    ) -> None:
        """
        Merge ``unit``, and everything it imports, exactly once.

        ``reference`` is the import text that led here, and ``importer`` the
        unit it was written in. Both are ``None`` for a root.
        """
        if unit.path in self._done:
            return
        if unit.path in self._active:
            raise CycleError(f"import cycle: {self._cycle(unit)}")
        if not unit.path.is_file():
            raise PathError(self._missing(unit, reference, importer))

        self._active[unit.path] = unit
        try:
            self._merge(unit)
        finally:
            del self._active[unit.path]
        self._done.add(unit.path)

    def _merge(self, unit: Located) -> None:
        """Merge one unit's imports, then the unit's own settings."""
        loaded = load_unit(unit.path)
        for reference in loaded.imports:
            try:
                target = resolve_import(self._sources, unit.source, reference)
            except PathError as exc:
                raise PathError(f"{unit.display}: {exc}") from exc
            self.visit(target, reference, importer=unit)

        settings = loaded.settings
        if self._transform is not None:
            settings = self._transform(unit, settings)
        self._check_keys(unit, settings)
        try:
            self.settings = merge_objects(self.settings, settings)
        except MergeError as exc:
            raise MergeError(f"{unit.display}: {exc}") from exc

    def _check_keys(self, unit: Located, settings: JsonObject) -> None:
        """Reject, or under ``lax`` merely report, settings keys we do not know."""
        unknown = unknown_settings_keys(settings)
        if not unknown:
            return
        message = f"{unit.display}: unknown settings key(s): {', '.join(unknown)}"
        if not self._lax:
            raise UnitError(message)
        self.warnings.append(message)

    def _cycle(self, unit: Located) -> str:
        """Render the cycle that ``unit`` closes, in the order it was walked."""
        walked = list(self._active.values())
        start = list(self._active).index(unit.path)
        return " -> ".join(entry.display for entry in (*walked[start:], unit))

    @staticmethod
    def _missing(unit: Located, reference: str | None, importer: Located | None) -> str:
        """Explain a unit that does not exist, naming the importer if any."""
        if importer is None or reference is None:
            return f"'{unit.display}': no such unit"
        return f"{importer.display}: imports '{reference}', which does not exist"
