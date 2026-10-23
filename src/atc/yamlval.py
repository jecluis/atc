"""Reading and editing YAML: the one place ruamel's untyped data enters."""

import copy
import io
import math
from collections import Counter
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Protocol, cast

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
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


def set_in_text(text: str, keys: Sequence[str], value: JsonValue) -> str:
    """
    Set the entry at ``keys`` in the YAML document ``text``, and return the new text.

    ``text`` is edited in ruamel's round-trip mode, so comments, key order,
    quoting and blank lines stay as they were, and the comment lines that
    follow the entry stay after it. A mapping that is missing on the way is
    created, and so is the entry, at the end of its mapping. A list is written
    in flow style, ``[a, b]``, and a list that was there is replaced. The
    comments inside a replaced block list or mapping go with it, but the
    comment on its key stays.

    Raises:
        RegistryError: ``text`` is not valid YAML, has no mapping at the root or
            where ``keys`` pass through, or the edit would lose a comment that is
            outside the entry it replaces.
        ValueError: ``keys`` is empty.

    """
    if not keys:
        raise ValueError("set_in_text needs at least one key")
    editor = YAML(typ="rt")
    editor.preserve_quotes = True
    editor.width = _NO_WRAP
    editor.indent(mapping=2, sequence=4, offset=2)  # pyright: ignore[reportAny]
    try:
        # The round trip's use of ruamel's untyped data: what it loads is taken
        # as `object`, and every node is viewed through the protocols below
        # once `isinstance` has said what it is.
        loaded: object = editor.load(text)  # pyright: ignore[reportAny, reportUnknownMemberType]
    except YAMLError as exc:
        raise RegistryError(_describe(exc)) from exc
    root = _new_map() if loaded is None else _as_map(loaded)
    if root is None:
        raise RegistryError("the document must be a mapping")
    holder, depth = root, 0
    while depth < len(keys) - 1 and holder.get(keys[depth]) is not None:
        child = _as_map(holder.get(keys[depth]))
        if child is None:
            raise RegistryError(f"/{'/'.join(keys[: depth + 1])}: is not a mapping")
        holder, depth = child, depth + 1
    # What is left of `keys` is missing, so it is built whole and put in once.
    entry = _rebuild(value)
    for name in reversed(keys[depth + 1 :]):
        wrapper = _new_map()
        wrapper[name] = entry
        entry = wrapper
    replaced = holder.get(keys[depth])
    _put(holder, keys[depth], entry)
    edited = _render(editor, root)
    # Only what went with a replaced collection may be gone: the comments on
    # its own lines, not the lines that follow it. This checks the text, not
    # ruamel's bookkeeping, so a change in how ruamel holds comments cannot lose
    # one without this noticing.
    lost = _comments_in(text) - _comments_in(edited)
    if _as_map(replaced) is not None or _as_seq(replaced) is not None:
        lost -= _comments_in(_without_trailer(_render(editor, replaced)))
    for comment in lost:
        message = f"/{'/'.join(keys)}: editing it would lose the comment: {comment}"
        raise RegistryError(message)
    return edited


_NO_WRAP = 1 << 20
"""A line width that no list reaches, so ruamel never wraps a flow sequence."""


def _render(editor: YAML, node: object) -> str:
    out = io.StringIO()
    editor.dump(node, out)  # pyright: ignore[reportUnknownMemberType]
    return out.getvalue()


def _without_trailer(text: str) -> str:
    """Cut the blank and comment-only lines off the end of ``text``."""
    lines = text.splitlines()
    while lines and (not lines[-1].strip() or lines[-1].lstrip().startswith("#")):
        _ = lines.pop()
    return "\n".join(lines)


def _comments_in(text: str) -> Counter[str]:
    """
    Count the comments in ``text``: a ``#`` that starts a line or follows a space.

    Quoted text is skipped, as a quote that opens where a scalar starts. Block
    scalars are not recognised, so a ``#`` line in one counts as a comment.
    """
    found: Counter[str] = Counter()
    for line in text.splitlines():
        quote, escaped = "", False
        for index, char in enumerate(line):
            if quote:
                if escaped:
                    escaped = False
                elif char == "\\" and quote == '"':
                    escaped = True
                elif char == quote:
                    quote = ""
            elif char in "'\"" and (index == 0 or not line[index - 1].isalnum()):
                quote = char
            elif char == "#" and (index == 0 or line[index - 1].isspace()):
                found[line[index:].rstrip()] += 1
                break
    return found


class _Token(Protocol):
    """A comment as ruamel keeps it: its text, and the lines up to the next entry."""

    value: str


class _Comments(Protocol):
    """The comments of a mapping or a list, by key or index."""

    items: dict[str | int, list[_Token | None]]


class _Flow(Protocol):
    def flow_style(self) -> bool | None: ...

    def set_flow_style(self) -> None: ...


class _Node(Protocol):
    """The part of a ruamel mapping or list that ``set_in_text`` uses."""

    @property
    def ca(self) -> _Comments: ...

    @property
    def fa(self) -> _Flow: ...

    def __len__(self) -> int: ...


class _Seq(_Node, Protocol):
    def append(self, value: object) -> None: ...


class _Map(_Node, Protocol):
    def get(self, key: str) -> object: ...

    def __contains__(self, key: str) -> bool: ...

    def __setitem__(self, key: str, value: object) -> None: ...

    def __iter__(self) -> Iterator[str]: ...

    def __reversed__(self) -> Iterator[str]: ...


def _new_map() -> _Map:
    return cast("_Map", CommentedMap())


def _as_map(node: object) -> _Map | None:
    return cast("_Map", node) if isinstance(node, CommentedMap) else None


def _new_seq() -> _Seq:
    return cast("_Seq", cast("object", CommentedSeq()))


def _as_seq(node: object) -> _Seq | None:
    if not isinstance(node, CommentedSeq):
        return None
    return cast("_Seq", cast("object", node))


def _rebuild(value: JsonValue) -> object:
    """Build what ruamel writes for ``value``: lists in flow style, maps in block."""
    match value:
        case list():
            sequence = _new_seq()
            for item in value:
                sequence.append(_rebuild(item))
            sequence.fa.set_flow_style()
            return sequence
        case dict():
            mapping = _new_map()
            for name, member in value.items():
                mapping[name] = _rebuild(member)
            return mapping
        case _:
            return value


def _put(holder: _Map, key: str, value: object) -> None:
    """
    Set ``holder[key]``, keeping the comment lines that follow the entry after it.

    Ruamel hangs the lines between two entries on the last scalar of the first,
    so a new last entry or a replaced block list would be left above them.
    """
    last = key if key in holder else next(reversed(holder), None)
    ending = None if last is None else _ending(holder, last)
    own = holder.ca.items.get(key)
    holder[key] = value
    if ending is None or (own is not None and own[2] is ending):
        return
    head, newline, lines = ending.value.partition("\n")
    if not lines:
        return
    moved = copy.copy(ending)
    ending.value = head + newline
    owner, name = _tail_owner(holder, key)
    owned = owner.ca.items.get(name)
    if owned is None:
        moved.value = f"\n{lines}"
        owner.ca.items[name] = [None, None, moved, None]
    elif owned[2] is None:
        moved.value = f"\n{lines}"
        owned[2] = moved
    else:
        kept = owned[2]
        kept.value = kept.value if kept.value.endswith("\n") else f"{kept.value}\n"
        kept.value += lines


def _tail_owner(holder: _Map, key: str) -> tuple[_Map, str]:
    """Find the entry that holds the lines after ``holder[key]``: its last scalar."""
    inner = _as_map(holder.get(key))
    if inner is not None and len(inner) and not inner.fa.flow_style():
        return _tail_owner(inner, next(reversed(inner)))
    return holder, key


def _ending(holder: _Map, key: str) -> _Token | None:
    """Find the comment that ends ``holder[key]``: the one on its last scalar."""
    value = holder.get(key)
    inner = _as_map(value)
    if inner is not None and len(inner) and not inner.fa.flow_style():
        return _ending(inner, next(reversed(inner)))
    items = _as_seq(value)
    if items is not None and len(items) and not items.fa.flow_style():
        entry = items.ca.items.get(len(items) - 1)
        return None if entry is None else entry[0]
    entry = holder.ca.items.get(key)
    return None if entry is None else entry[2]


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
