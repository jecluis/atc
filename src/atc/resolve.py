"""Resolution of an import graph into a single settings mapping."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import final

from atc.errors import CycleError, MergeError, PathError, UnitError
from atc.jsonval import JsonObject
from atc.merge import merge_objects
from atc.paths import display, resolve_unit
from atc.unit import load_unit, unknown_settings_keys


@dataclass(frozen=True, slots=True)
class Resolution:
    """
    The outcome of resolving a set of units.

    ``settings`` is the merged ``settings.local.json`` payload. ``warnings``
    holds diagnostics that did not stop the resolution, such as unknown
    settings keys tolerated under ``lax``.
    """

    settings: JsonObject
    warnings: tuple[str, ...]


def resolve(root: Path, references: Sequence[str], *, lax: bool = False) -> Resolution:
    """
    Merge the units named by ``references``, following their imports.

    Each unit contributes exactly once, at the first point the depth-first walk
    reaches it. A unit's imports are walked, in order, before the unit's own
    settings, so an importing unit overrides what it imports; references are
    walked left to right, so a later one overrides an earlier one.

    Those two rules meet when a reference has already been pulled in as an
    import of an earlier one. Contributing once wins — the unit keeps the
    position it already had — and a warning records that its later position had
    no effect.

    Set ``lax`` to report unknown settings keys as warnings instead of errors.

    Raises:
        PathError: a reference is malformed or names a unit that is missing.
        UnitError: a unit is malformed, or carries an unknown settings key.
        CycleError: the imports form a cycle.
        MergeError: two units give the same setting values of differing types.

    """
    walker = _Walker(root, lax=lax)
    for reference in references:
        walker.request(resolve_unit(root, reference), reference)
    return Resolution(settings=walker.settings, warnings=tuple(walker.warnings))


@final
class _Walker:
    """Depth-first traversal of the import graph, accumulating merged settings."""

    def __init__(self, root: Path, *, lax: bool) -> None:
        self._root = root
        self._lax = lax
        self._done: set[Path] = set()
        self._active: list[Path] = []
        self.settings: JsonObject = {}
        self.warnings: list[str] = []

    def request(self, path: Path, reference: str) -> None:
        """
        Merge a unit the user named directly.

        A unit an earlier reference already pulled in keeps the position it
        had, so naming it again cannot move it later in the merge order. That
        is worth saying out loud, because the user asked for something the
        merge-once rule cannot give them.
        """
        if path in self._done:
            cause = "already merged as an import of an earlier unit"
            effect = "naming it here does not change the merge order"
            self.warnings.append(f"'{reference}': {cause}, so {effect}")
            return
        self.visit(path, reference, origin=None)

    def visit(self, path: Path, reference: str, origin: Path | None) -> None:
        """Merge the unit at ``path``, and everything it imports, exactly once."""
        if path in self._done:
            return
        if path in self._active:
            raise CycleError(f"import cycle: {self._cycle(path)}")
        if not path.is_file():
            raise PathError(self._missing(reference, origin))

        self._active.append(path)
        try:
            self._merge(path)
        finally:
            del self._active[-1]
        self._done.add(path)

    def _merge(self, path: Path) -> None:
        """Merge one unit's imports, then the unit's own settings."""
        unit = load_unit(path)
        for imported in unit.imports:
            try:
                target = resolve_unit(self._root, imported)
            except PathError as exc:
                raise PathError(f"{self._name(path)}: {exc}") from exc
            self.visit(target, imported, origin=path)

        self._check_keys(unit.path, unit.settings)
        try:
            self.settings = merge_objects(self.settings, unit.settings)
        except MergeError as exc:
            raise MergeError(f"{self._name(path)}: {exc}") from exc

    def _check_keys(self, path: Path, settings: JsonObject) -> None:
        """Reject, or under ``lax`` merely report, settings keys we do not know."""
        unknown = unknown_settings_keys(settings)
        if not unknown:
            return
        message = f"{self._name(path)}: unknown settings key(s): {', '.join(unknown)}"
        if not self._lax:
            raise UnitError(message)
        self.warnings.append(message)

    def _cycle(self, path: Path) -> str:
        """Render the cycle that ``path`` closes, in the order it was walked."""
        start = self._active.index(path)
        return " -> ".join(self._name(entry) for entry in (*self._active[start:], path))

    def _missing(self, reference: str, origin: Path | None) -> str:
        """Explain a reference that names no unit, naming the importer if any."""
        if origin is None:
            return f"'{reference}': no such unit under {self._root}"
        return f"{self._name(origin)}: imports '{reference}', which does not exist"

    def _name(self, path: Path) -> str:
        """Render a unit path relative to the root, for error messages."""
        return display(self._root, path)
