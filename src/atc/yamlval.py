"""Reading YAML as JSON values: the one place ruamel's untyped data enters."""

import math
from pathlib import Path
from typing import Protocol, cast

from ruamel.yaml import YAML
from ruamel.yaml.error import MarkedYAMLError, YAMLError

from atc.errors import RegistryError
from atc.jsonval import JsonValue


def load_yaml(path: Path) -> JsonValue:
    """
    Read the YAML document stored at ``path``, as :func:`parse_yaml` does.

    Raises:
        RegistryError: the file cannot be read, or :func:`parse_yaml` refuses it.

    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RegistryError(f"{path}: cannot be read: {exc}") from exc
    return parse_yaml(text, str(path))


def parse_yaml(text: str, origin: str) -> JsonValue:
    """
    Parse one YAML 1.2 document, accepting only what JSON can hold.

    YAML 1.2 keeps ``no``, ``on`` and ``off`` as strings. ``origin`` names the
    text in error messages.

    Raises:
        RegistryError: the text is not valid YAML, repeats a key within a
            mapping, has a key that is not a string, or holds a value JSON has
            no type for, such as a timestamp. The last message says to quote
            the value.

    """
    loader = YAML(typ="safe")
    loader.version = (1, 2)
    try:
        # The only place ruamel's result is taken. Its `load` is typed to
        # return `Any`; `_to_json` receives it as `object` and builds a
        # `JsonValue` out of the types it recognises, so nothing untyped
        # leaves this module.
        parsed: object = loader.load(text)  # pyright: ignore[reportAny, reportUnknownMemberType]
    except YAMLError as exc:
        raise RegistryError(f"{origin}: {_describe(exc)}") from exc
    return _to_json(parsed, origin, "", ())


class _Mark(Protocol):
    line: int


class _Marked(Protocol):
    """The parts of ruamel's ``MarkedYAMLError`` that are used, typed."""

    problem: str | None
    problem_mark: _Mark | None
    context: str | None
    context_mark: _Mark | None


def _describe(exc: YAMLError) -> str:
    """Say what and where on one line; ruamel's own message takes several."""
    if not isinstance(exc, MarkedYAMLError):
        return " ".join(str(exc).split())
    # ruamel's error classes are not annotated, so this gives their fields the
    # types they have.
    marked = cast("_Marked", exc)
    if marked.problem is None:
        return " ".join(str(exc).split())
    where = ""
    if marked.problem_mark is not None:
        where = f"line {marked.problem_mark.line + 1}: "
    context = ""
    if marked.context is not None:
        opened = marked.context_mark
        at = "" if opened is None else f", line {opened.line + 1}"
        context = f" ({marked.context}{at})"
    return f"{where}{marked.problem}{context}"


def _to_json(
    value: object, origin: str, pointer: str, active: tuple[int, ...]
) -> JsonValue:
    """
    Turn what ruamel built into a ``JsonValue``, refusing anything else.

    ``pointer`` is the JSON pointer to ``value``, for messages. ``active``
    holds the identities of the containers being converted, so that an alias
    that contains itself is caught.
    """
    here = pointer or "<root>"
    identity = id(value)
    if identity in active:
        raise RegistryError(f"{origin}: {here}: an alias refers to itself")
    inside = (*active, identity)
    match value:
        case None | bool() | int() | str():
            return value
        case float():
            if not math.isfinite(value):
                message = f"{origin}: {here}: {value} is not a JSON number; quote it"
                raise RegistryError(message)
            return value
        case list():
            items = cast("list[object]", value)
            return [
                _to_json(item, origin, f"{pointer}/{index}", inside)
                for index, item in enumerate(items)
            ]
        case dict():
            members = cast("dict[object, object]", value)
            converted: dict[str, JsonValue] = {}
            for key, member in members.items():
                if not isinstance(key, str):
                    message = f"{origin}: {here}: key {key} is not a string; quote it"
                    raise RegistryError(message)
                converted[key] = _to_json(member, origin, f"{pointer}/{key}", inside)
            return converted
        case _:
            kind = type(value).__name__
            message = f"{origin}: {here}: {kind} is not a JSON value; quote it"
            raise RegistryError(message)
