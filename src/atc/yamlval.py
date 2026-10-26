"""Reading and editing YAML: the one place ruamel's untyped data enters."""

import io
import math
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast, final, override

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.constructor import ConstructorError, SafeConstructor
from ruamel.yaml.error import CommentMark, MarkedYAMLError, YAMLError
from ruamel.yaml.tokens import CommentToken

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
            mapping, has a key that is not a string, uses an alias for a list
            or a mapping or a merge key (``<<``), or holds a value JSON has no
            type for, such as a timestamp. The last message says to quote the
            value.

    """
    loader = YAML(typ="safe")
    loader.version = (1, 2)
    loader.Constructor = _NoMergeConstructor
    try:
        # The only place ruamel's result is taken. Its `load` is typed to
        # return `Any`; `_to_json` receives it as `object` and builds a
        # `JsonValue` out of the types it recognises, so nothing untyped
        # leaves this module.
        parsed: object = loader.load(text)  # pyright: ignore[reportAny, reportUnknownMemberType]
    except YAMLError as exc:
        raise RegistryError(f"{origin}: {_describe(exc)}") from exc
    return _to_json(parsed, origin, "", set())


_MERGE_TAG = "tag:yaml.org,2002:merge"


class _MergeKey(Protocol):
    """The parts of the node of a mapping key that are used to spot ``<<``."""

    tag: object
    start_mark: object


class _MappingNode(Protocol):
    value: list[tuple[_MergeKey, object]]


@final
class _NoMergeConstructor(SafeConstructor):
    """ruamel's safe constructor, refusing merge keys, which JSON has no form of."""

    @override
    def flatten_mapping(self, node: object) -> None:
        # ruamel calls this for every mapping, before building it, to expand
        # its merge keys; refusing them here means none is ever expanded.
        for key, _ in cast("_MappingNode", node).value:
            if key.tag == _MERGE_TAG:
                raise ConstructorError(
                    problem="merge keys (<<) are not supported; write the entries out",
                    problem_mark=key.start_mark,
                )


def set_in_text(text: str, keys: Sequence[str], value: JsonValue) -> str:
    """
    Set the entry at ``keys`` in the YAML document ``text``, and return the new text.

    ``text`` is edited in ruamel's round-trip mode, so comments, key order,
    quoting and blank lines stay as they were, and the comment lines that
    follow the entry stay after it. A mapping that is missing on the way is
    created, and so is the entry, at the end of its mapping. A mapping that was
    written as ``{}`` and gets its first entry is written in block style. A list
    is written in flow style, ``[a, b]``, and a list that was there is replaced.
    The comments inside a replaced block list or mapping go with it, but the
    comment on its key stays.

    Raises:
        RegistryError: ``text`` is not valid YAML, has no mapping at the root or
            where ``keys`` pass through, or the edit would lose a comment that is
            outside the entry it replaces.
        ValueError: ``keys`` is empty.

    """
    if not keys:
        raise ValueError("set_in_text needs at least one key")
    editor, root = _open(text)
    holder, depth = _walk(root, keys)
    # What is left of `keys` is missing, so it is built whole and put in once.
    entry = _rebuild(value)
    for name in reversed(keys[depth + 1 :]):
        wrapper = _new_map()
        wrapper[name] = entry
        entry = wrapper
    replaced = holder.get(keys[depth])
    after = _open_up(root, keys[:depth], holder)
    _put(holder, keys[depth], entry)
    if after:
        _tail(holder, keys[depth]).attach(after)
    edited = _render(editor, root)
    # Only what went with a replaced collection may be gone: the comments on
    # its own lines, not the lines that follow it.
    allowed: Counter[str] = Counter()
    if _as_map(replaced) is not None or _as_seq(replaced) is not None:
        allowed = _comments_in(_without_trailer(_render(editor, replaced)))
    _refuse_lost_comments(text, edited, keys, allowed)
    return edited


def delete_in_text(text: str, keys: Sequence[str]) -> str:
    """
    Remove the entry at ``keys`` from the YAML document ``text``, and return the text.

    ``text`` is edited as :func:`set_in_text` does it, so what else is in it
    stays as it was. The comments inside the entry, the one on its own line or
    its key's, and the block of comment lines directly above it, at the key's own
    indentation and with no blank line between, go with it. The other comment
    and blank lines around it stay: the ones that followed it now follow the
    entry before it, or come first in the mapping when it had none, and when it
    was the only entry, the mapping is written as ``{}`` with those lines after
    it.

    Raises:
        RegistryError: ``text`` is not valid YAML, has no mapping at the root or
            where ``keys`` pass through, has no such entry, or the edit would lose
            a comment that is outside the entry.
        ValueError: ``keys`` is empty.

    """
    if not keys:
        raise ValueError("delete_in_text needs at least one key")
    editor, root = _open(text)
    holder, depth = _walk(root, keys)
    name = keys[-1]
    if depth != len(keys) - 1 or name not in holder:
        raise RegistryError(f"/{'/'.join(keys)}: no such entry")
    removed, own = holder.get(name), holder.ca.items.get(name)
    ending = _ending(holder, name)
    lines = "" if ending is None else _detach_lines(ending)
    siblings = list(holder)
    index = siblings.index(name)
    before = siblings[index - 1] if index else None
    above = None if len(keys) < 2 else _Above(_walk(root, keys[:-1])[0], keys[-2])
    column = _key_column(holder, name)
    dropped = (
        _drop_above(_tail(holder, before).token(), column)
        if before is not None
        else _drop_first_above(holder, above, column)
    )
    del holder[name]
    _ = holder.ca.items.pop(name, None)
    if before is not None:
        if lines:
            _tail(holder, before).attach(lines)
    elif len(holder):
        if lines:
            _attach_first(holder, above, lines)
    else:
        _empty(holder, above, lines)
    edited = _render(editor, root)
    # The entry is rendered on its own to find the comments that go with it.
    alone = _new_map()
    alone[name] = removed
    if own is not None:
        alone.ca.items[name] = own
    allowed = _comments_in(_without_trailer(_render(editor, alone))) + dropped
    _refuse_lost_comments(text, edited, keys, allowed)
    return edited


def delete_item_in_text(text: str, keys: Sequence[str], item: str) -> str:
    """
    Remove ``item`` from the list at ``keys`` in the YAML document ``text``.

    Returns the new text. It is edited as :func:`set_in_text` does it, so what
    else is in it stays as it was, and the list keeps its style. The comment on
    the item's own line, and the block of comment lines directly above it, at
    its indentation and with no blank line between, go with it, as
    :func:`delete_in_text` removes an entry's; above the first item, the
    comments stay, as the list's. The lines that followed it now follow the
    item before it, or come first in the list.

    Raises:
        RegistryError: ``text`` is not valid YAML, has no list at ``keys``, the
            list does not have ``item`` or has nothing else, or the edit would
            lose a comment that is not the item's, as it would for comment lines
            that follow the first item.
        ValueError: ``keys`` is empty.

    """
    if not keys:
        raise ValueError("delete_item_in_text needs at least one key")
    editor, root = _open(text)
    holder, depth = _walk(root, keys)
    where = f"/{'/'.join(keys)}"
    sequence = _as_seq(holder.get(keys[-1])) if depth == len(keys) - 1 else None
    if sequence is None:
        raise RegistryError(f"{where}: is not a list")
    items = list(sequence)
    if item not in items:
        raise RegistryError(f"{where}: has no item '{item}'")
    if all(other == item for other in items):
        raise RegistryError(f"{where}: has no item but '{item}'")
    index = items.index(item)
    own = sequence.ca.items.get(index)
    ending = None if own is None else own[_ITEM_END]
    lines = "" if ending is None else _detach_lines(ending)
    allowed = Counter[str]() if ending is None else _comments_in(ending.value)
    if index and not sequence.fa.flow_style():
        before = _Tail(sequence, index - 1, _ITEM_END).token()
        allowed += _drop_above(before, _item_column(sequence, index))
    del sequence[index]
    if lines and index:
        _Tail(sequence, index - 1, _ITEM_END).attach(lines)
    elif lines:
        _attach_before_items(sequence, lines)
    edited = _render(editor, root)
    _refuse_lost_comments(text, edited, keys, allowed)
    return edited


def _refuse_lost_comments(
    text: str, edited: str, keys: Sequence[str], allowed: Counter[str]
) -> None:
    """
    Refuse an edit of ``keys`` that dropped a comment other than the ``allowed`` ones.

    This checks the text, not ruamel's bookkeeping, so a change in how ruamel
    holds comments cannot lose one without this noticing.
    """
    for comment in _comments_in(text) - _comments_in(edited) - allowed:
        message = f"/{'/'.join(keys)}: editing it would lose the comment: {comment}"
        raise RegistryError(message)


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
    """
    A comment as ruamel keeps it: its text, and the lines up to the next entry.

    ``column`` is where its first line starts.
    """

    value: str
    column: int


class _Comments(Protocol):
    """
    The comments of a mapping or a list, by key or index.

    ``comment`` is the one on the line of the mapping's own key, and the lines
    between that and its first entry.
    """

    items: dict[str | int, list[_Token | None]]
    comment: list[_Token | list[_Token] | None] | None


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


class _ItemLineCol(Protocol):
    """Where in the text the items of a list were."""

    def item(self, index: int) -> Sequence[int]: ...


class _Seq(_Node, Protocol):
    @property
    def lc(self) -> _ItemLineCol: ...

    def append(self, value: object) -> None: ...

    def __iter__(self) -> Iterator[object]: ...

    def __delitem__(self, index: int) -> None: ...


class _LineCol(Protocol):
    """Where in the text the keys of a mapping were."""

    def key(self, key: str) -> Sequence[int]: ...


class _Map(_Node, Protocol):
    @property
    def lc(self) -> _LineCol: ...

    def get(self, key: str) -> object: ...

    def __contains__(self, key: str) -> bool: ...

    def __setitem__(self, key: str, value: object) -> None: ...

    def __delitem__(self, key: str) -> None: ...

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


def _open(text: str) -> tuple[YAML, _Map]:
    """Load ``text`` for editing, with the editor to write it back with."""
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
    return editor, root


def _walk(root: _Map, keys: Sequence[str]) -> tuple[_Map, int]:
    """
    Follow ``keys`` down from ``root`` for as long as the mappings are there.

    Returns the mapping that holds the first key that is missing, or the last
    one, and that key's index in ``keys``.
    """
    holder, depth = root, 0
    while depth < len(keys) - 1 and holder.get(keys[depth]) is not None:
        child = _as_map(holder.get(keys[depth]))
        if child is None:
            raise RegistryError(f"/{'/'.join(keys[: depth + 1])}: is not a mapping")
        holder, depth = child, depth + 1
    return holder, depth


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
    lines = _detach_lines(ending)
    if lines:
        _tail(holder, key).attach(lines)


def _detach_lines(ending: _Token) -> str:
    """Cut the lines that follow an entry off its ``ending`` comment, return them."""
    head, newline, lines = ending.value.partition("\n")
    if lines:
        ending.value = head + newline
    return lines


# Ruamel keeps the comments of a mapping or a list in `ca.items`, by key or index,
# as a list of four slots. A list item's trailing comment is in the first, a
# mapping value's in the third, and the lines between a key and the first entry of
# its mapping value are in the fourth, as a list of comments.
_ITEM_END = 0
_LINE_END = 2
_PRE_COMMENT = 3


@dataclass(frozen=True, slots=True)
class _Tail:
    """
    Where the lines after an entry hang: the comment of its last scalar.

    That is ``slot`` of ruamel's list for ``key``, in the comments of ``owner``.
    """

    owner: _Node
    key: str | int
    slot: int

    def token(self) -> _Token | None:
        """Give the comment there now, if there is one."""
        owned = self.owner.ca.items.get(self.key)
        return None if owned is None else owned[self.slot]

    def attach(self, lines: str) -> None:
        """
        Put ``lines`` at the end of the entry.

        Blank lines that the entry already ends with are not doubled.
        """
        items = self.owner.ca.items
        owned = items.get(self.key)
        if owned is None:
            owned = items[self.key] = [None, None, None, None]
        kept = owned[self.slot]
        if kept is None:
            owned[self.slot] = _new_comment(f"\n{lines}")
            return
        kept.value = _joined(kept.value, lines)


@dataclass(frozen=True, slots=True)
class _Above:
    """The mapping that holds the key of another mapping, and that key."""

    holder: _Map
    key: str

    def entry(self) -> list[_Token | None] | None:
        """Give ruamel's list of comments for the key, if it has one."""
        found = self.holder.ca.items.get(self.key)
        return found if found is not None and len(found) > _PRE_COMMENT else None


def _attach_first(holder: _Map, above: _Above | None, lines: str) -> None:
    """
    Put ``lines`` before the first entry of ``holder``, after the comments there.

    ``above`` is the key that ``holder`` is the value of. It keeps its own list
    of the comments before the first entry too, which is the one ruamel writes
    when it has one.
    """
    first = _first_comments(holder)
    if first:
        first[-1].value = _joined(first[-1].value, lines)
        return
    # Nothing comes before them, so blank lines are left out.
    body = lines.lstrip("\n")
    if not body.strip():
        return
    added = [_new_comment(body)]
    own = None if holder.ca.comment is None else holder.ca.comment[0]
    holder.ca.comment = [own, added]
    entry = None if above is None else above.entry()
    if entry is not None:
        slots = cast("list[_Token | list[_Token] | None]", entry)
        slots[_PRE_COMMENT] = added


def _attach_before_items(sequence: _Seq, lines: str) -> None:
    """Put ``lines`` before the first item of ``sequence``, after the comments there."""
    body = lines.lstrip("\n")
    if not body.strip():
        return
    comment = sequence.ca.comment
    first = None if comment is None else comment[1]
    if isinstance(first, list) and first:
        first[-1].value = _joined(first[-1].value, lines)
        return
    own = None if comment is None else comment[0]
    sequence.ca.comment = [own, [_new_comment(body)]]


def _empty(holder: _Map, above: _Above | None, lines: str) -> None:
    """
    Write ``holder``, which has lost its last entry, as ``{}``, and keep its comments.

    The comments between its key and the entry it lost, and the ``lines`` that
    followed that entry, go after its key, which is ``above``'s.
    """
    holder.fa.set_flow_style()
    first = _first_comments(holder)
    if holder.ca.comment is not None:
        holder.ca.comment[1] = None
    entry = None if above is None else above.entry()
    if entry is not None:
        entry[_PRE_COMMENT] = None
    pre = "".join(token.value for token in first)
    kept = _joined(pre, lines) if pre else lines
    if above is not None and kept:
        _Tail(above.holder, above.key, _LINE_END).attach(kept)


def _open_up(root: _Map, keys: Sequence[str], holder: _Map) -> str:
    """
    Prepare ``holder``, the mapping at ``keys``, to get its first entry.

    An empty mapping was written ``{}``, and with an entry it is written in block
    style. The comment lines after the ``{}`` hang on its key, which would put
    them above the entry. They are cut off and returned, to be put after the
    entry instead.
    """
    if len(holder) or not keys:
        return ""
    above = _Tail(_walk(root, keys)[0], keys[-1], _LINE_END)
    ending = above.token()
    return "" if ending is None else _detach_lines(ending)


def _item_column(sequence: _Seq, index: int) -> int:
    """
    Give the column the ``-`` of block list item ``index`` is at, or -1 if not known.

    Ruamel knows where the item's value starts, two columns after it.
    """
    try:
        return sequence.lc.item(index)[1] - 2
    except KeyError:
        return -1


def _key_column(holder: _Map, key: str) -> int:
    """Give the column ``key`` starts at in the text, or -1 if it is not known."""
    try:
        return holder.lc.key(key)[1]
    except KeyError:
        return -1


def _split_above(
    value: str, column: int, *, own_line: bool, start: int
) -> tuple[str, str]:
    """
    Split the block of comment lines that ends ``value`` off it.

    The block is the comment lines that come right before an entry that starts
    at ``column``, with no blank line in between, and each starts at ``column``
    too: a deeper line belongs to the entry before, a shallower one to what
    holds both. Returns what stays and the block. With ``own_line``, ``value``
    starts with the rest of the previous entry's own line, which stays whole.
    ``start`` is where the first line of ``value`` starts, when that is not
    written in it.
    """
    head, newline, rest = value.partition("\n") if own_line else ("", "", value)
    lines = rest.splitlines(keepends=True)
    cut = len(lines)
    while cut:
        line = lines[cut - 1]
        indent = len(line) - len(line.lstrip(" "))
        if not own_line and cut == 1:
            indent += start
        if not line.lstrip().startswith("#") or indent != column:
            break
        cut -= 1
    return head + newline + "".join(lines[:cut]), "".join(lines[cut:])


def _drop_above(token: _Token | None, column: int) -> Counter[str]:
    """Cut the block of comments right above the next entry off the end of ``token``."""
    if token is None:
        return Counter()
    kept, gone = _split_above(token.value, column, own_line=True, start=0)
    token.value = kept
    return _comments_in(gone)


def _drop_first_above(holder: _Map, above: _Above | None, column: int) -> Counter[str]:
    """Cut the block of comments right above the first entry of ``holder`` off."""
    first = _first_comments(holder)
    gone: Counter[str] = Counter()
    while first:
        last = first[-1]
        kept, block = _split_above(
            last.value, column, own_line=False, start=last.column
        )
        gone += _comments_in(block)
        if kept:
            last.value = kept
            break
        _ = first.pop()
    if not first and holder.ca.comment is not None:
        holder.ca.comment[1] = None
        entry = None if above is None else above.entry()
        if entry is not None:
            entry[_PRE_COMMENT] = None
    return gone


def _first_comments(holder: _Map) -> list[_Token]:
    """Find the comments between the key of ``holder`` and its first entry."""
    comment = holder.ca.comment
    first = None if comment is None else comment[1]
    return first if isinstance(first, list) else []


def _joined(kept: str, lines: str) -> str:
    """Add ``lines`` after the comment text ``kept``, without doubling a blank line."""
    kept = kept if kept.endswith("\n") else f"{kept}\n"
    return kept + (lines.lstrip("\n") if kept.endswith("\n\n") else lines)


def _new_comment(value: str) -> _Token:
    """Make a comment with the text ``value``, which starts at the first column."""
    return cast("_Token", cast("object", CommentToken(value, CommentMark(0), None)))


def _tail(holder: _Map, key: str) -> _Tail:
    """Find where the lines after ``holder[key]`` hang: its last scalar's comment."""
    value = holder.get(key)
    inner = _as_map(value)
    if inner is not None and len(inner) and not inner.fa.flow_style():
        return _tail(inner, next(reversed(inner)))
    items = _as_seq(value)
    if items is not None and len(items) and not items.fa.flow_style():
        return _Tail(items, len(items) - 1, _ITEM_END)
    return _Tail(holder, key, _LINE_END)


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


def _to_json(value: object, origin: str, pointer: str, seen: set[int]) -> JsonValue:
    """
    Turn what ruamel built into a ``JsonValue``, refusing anything else.

    ``pointer`` is the JSON pointer to ``value``, for messages. ``seen`` holds
    the identities of the lists and mappings met so far. ruamel builds the node
    an alias names once and shares it, so meeting one again is an alias, which
    is refused: expanding aliases of aliases grows without bound.
    """
    here = pointer or "<root>"
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
            _first_time(items, seen, f"{origin}: {here}")
            return [
                _to_json(item, origin, f"{pointer}/{index}", seen)
                for index, item in enumerate(items)
            ]
        case dict():
            members = cast("dict[object, object]", value)
            _first_time(members, seen, f"{origin}: {here}")
            converted: dict[str, JsonValue] = {}
            for key, member in members.items():
                if not isinstance(key, str):
                    message = f"{origin}: {here}: key {key} is not a string; quote it"
                    raise RegistryError(message)
                converted[key] = _to_json(member, origin, f"{pointer}/{key}", seen)
            return converted
        case _:
            kind = type(value).__name__
            message = f"{origin}: {here}: {kind} is not a JSON value; quote it"
            raise RegistryError(message)


def _first_time(node: object, seen: set[int], where: str) -> None:
    """Refuse ``node`` if it was met before, and remember it."""
    if id(node) in seen:
        message = f"{where}: aliases are not supported; write the value out in full"
        raise RegistryError(message)
    seen.add(id(node))
