"""The config unit envelope, and how a unit file is read and validated."""

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from atc.errors import UnitError
from atc.jsonval import JsonObject, load_json
from atc.settings_keys import KNOWN_SETTINGS_KEYS

UNIT_VERSION = 2


class UnitEnvelope(BaseModel):
    """
    The shape every config unit must have.

    ``imports`` lists other units: ``path/to/unit.json`` is relative to the
    ``conf`` directory of the unit's own source, and ``@name/path/to/unit.json``
    to that of the source called ``name``. ``settings`` holds the fragment of
    Claude Code settings this unit contributes. Both are optional:
    a unit with only ``imports`` acts as a profile that groups other units
    together.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    version: Literal[2]
    description: str | None = None
    imports: tuple[str, ...] = ()
    settings: JsonObject = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Unit:
    """
    A validated unit, ready to be resolved and merged.

    ``settings`` is the mapping exactly as it was parsed from disk. The
    envelope model validates it but never supplies it, so no default value
    invented by validation can reach the merged output.
    """

    path: Path
    imports: tuple[str, ...]
    settings: JsonObject


def load_unit(path: Path) -> Unit:
    """
    Read and validate the config unit stored at ``path``.

    Raises:
        UnitError: the file is not valid JSON, is not a JSON object, or does
            not satisfy the unit envelope.

    """
    document = load_json(path)
    if not isinstance(document, dict):
        raise UnitError(f"{path}: a unit must be a JSON object")

    try:
        envelope = UnitEnvelope.model_validate(document)
    except ValidationError as exc:
        raise UnitError(f"{path}: {_describe(exc)}") from exc

    parsed_settings = document.get("settings")
    settings: JsonObject = parsed_settings if isinstance(parsed_settings, dict) else {}
    return Unit(path=path, imports=envelope.imports, settings=settings)


def unknown_settings_keys(settings: JsonObject) -> tuple[str, ...]:
    """Return the top-level keys of ``settings`` Claude Code does not define."""
    return tuple(sorted(key for key in settings if key not in KNOWN_SETTINGS_KEYS))


def _describe(exc: ValidationError) -> str:
    """Flatten a pydantic validation failure into one readable line."""
    parts = [
        f"{'.'.join(str(item) for item in error['loc']) or '<root>'}: {error['msg']}"
        for error in exc.errors()
    ]
    return "; ".join(parts)
