"""The JSON value type, and the single boundary where untyped data enters."""

import json
import math
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

    Raises:
        UnitError: the file cannot be read, is not UTF-8, or does not contain
            valid JSON. See :func:`parse_json` for what counts as valid.

    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise UnitError(f"{path}: cannot be read: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise UnitError(f"{path}: not UTF-8: {exc}") from exc
    return parse_json(text, str(path))


def parse_json(text: str, origin: str) -> JsonValue:
    """
    Parse the JSON document ``text``, strictly. ``origin`` names it in errors.

    Python's JSON parser is more permissive than JSON itself, and what it
    lets through would otherwise travel silently into generated settings, or
    fail later in a way that is no configuration error. So these are refused:
    the constants ``NaN``, ``Infinity`` and ``-Infinity``, which no strict JSON
    reader accepts; numbers too large for a float, which it would read as
    infinite; duplicate keys within one object, where it would keep the last
    occurrence and discard the rest without a word; strings and keys with an
    unpaired surrogate, which are not text; and documents nested too deeply
    for it to read.

    Raises:
        UnitError: ``text`` is not valid JSON.

    """
    try:
        # The only place untyped data crosses into the program. `json.loads` is
        # declared as returning `Any`; JsonValue describes exactly the set of
        # values it can return, so the cast below is sound rather than merely
        # convenient.
        parsed: object = json.loads(  # pyright: ignore[reportAny]
            text,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
            object_pairs_hook=_object_from_pairs,
        )
    except RecursionError as exc:
        raise UnitError(f"{origin}: invalid JSON: nested too deeply") from exc
    except ValueError as exc:
        raise UnitError(f"{origin}: invalid JSON: {exc}") from exc
    value = cast(JsonValue, parsed)
    _refuse_surrogates(value, origin)
    return value


def _reject_constant(name: str) -> NoReturn:
    """Refuse the non-standard constants Python's JSON parser would accept."""
    raise ValueError(f"{name} is not valid JSON")


def _finite_float(text: str) -> float:
    """Read a JSON number as a float, refusing one too large to be finite."""
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"{text} is too large for a number")
    return value


def _refuse_surrogates(value: JsonValue, origin: str) -> None:
    r"""
    Refuse a string or key of ``value`` that cannot be written as UTF-8.

    That is one with an unpaired surrogate, which an escape such as ``\ud800``
    alone makes. The walk keeps its own stack, so depth is no problem.
    """
    pending: list[JsonValue] = [value]
    while pending:
        match pending.pop():
            case str() as text:
                _encodable(text, origin)
            case list() as items:
                pending.extend(items)
            case dict() as members:
                for key, member in members.items():
                    _encodable(key, origin)
                    pending.append(member)
            case _:
                pass


def _encodable(text: str, origin: str) -> None:
    try:
        _ = text.encode("utf-8")
    except UnicodeEncodeError as exc:
        message = f"{origin}: invalid JSON: {text!r} has an unpaired surrogate"
        raise UnitError(message) from exc


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
