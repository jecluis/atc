"""The JSON value type, and the single boundary where untyped data enters."""

import json
from pathlib import Path
from typing import NoReturn, cast

from atc.errors import UnitError

type JsonValue = str | int | float | bool | list[JsonValue] | JsonObject | None
"""Any value ``json.loads`` can produce."""

type JsonObject = dict[str, JsonValue]
"""A JSON object, the shape of both a unit envelope and a settings payload."""


def load_json(path: Path) -> JsonValue:
    """
    Parse the JSON document stored at ``path``, strictly.

    Python's JSON parser is more permissive than JSON itself. Two of its
    extensions are rejected here, because both would otherwise travel silently
    into a generated ``settings.local.json``: the constants ``NaN``,
    ``Infinity`` and ``-Infinity``, which no strict JSON reader accepts, and
    duplicate keys within one object, where the parser would keep the last
    occurrence and discard the rest without a word.

    Raises:
        UnitError: the file cannot be read, or does not contain valid JSON.

    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise UnitError(f"{path}: cannot be read: {exc}") from exc
    try:
        # The only place untyped data crosses into the program. `json.loads` is
        # declared as returning `Any`; JsonValue describes exactly the set of
        # values it can return, so the cast below is sound rather than merely
        # convenient.
        parsed: object = json.loads(  # pyright: ignore[reportAny]
            text,
            parse_constant=_reject_constant,
            object_pairs_hook=_object_from_pairs,
        )
    except ValueError as exc:
        raise UnitError(f"{path}: invalid JSON: {exc}") from exc
    return cast(JsonValue, parsed)


def _reject_constant(name: str) -> NoReturn:
    """Refuse the non-standard constants Python's JSON parser would accept."""
    raise ValueError(f"{name} is not valid JSON")


def _object_from_pairs(pairs: list[tuple[str, JsonValue]]) -> JsonObject:
    """Build an object from its parsed members, refusing repeated keys."""
    seen: set[str] = set()
    for key, _ in pairs:
        if key in seen:
            raise ValueError(f"duplicate key {key!r}")
        seen.add(key)
    return dict(pairs)


def json_kind(value: JsonValue) -> str:
    """
    Name the JSON type of ``value``.

    ``int`` and ``float`` are both reported as ``number``; ``bool`` is
    reported as ``boolean``, matching JSON rather than Python's treatment of
    ``bool`` as a subtype of ``int``.
    """
    match value:
        case bool():
            return "boolean"
        case int() | float():
            return "number"
        case str():
            return "string"
        case None:
            return "null"
        case list():
            return "array"
        case dict():
            return "object"


def canonical(value: JsonValue) -> str:
    """
    Serialize ``value`` so that equal values yield equal strings.

    Used to compare array elements for identity when merging, so that two
    structurally identical objects collapse into one.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def dumps(value: JsonValue) -> str:
    """Render ``value`` as the tool's output: sorted keys, two-space indent."""
    rendered = json.dumps(
        value,
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
    )
    return rendered + "\n"
