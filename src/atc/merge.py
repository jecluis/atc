"""The rules that combine two config values into one."""

from dataclasses import dataclass

from atc.errors import MergeError
from atc.jsonval import JsonObject, JsonValue, canonical, json_kind


def merge_objects(
    base: JsonObject,
    overlay: JsonObject,
    pointer: tuple[str, ...] = (),
) -> JsonObject:
    """
    Merge ``overlay`` onto ``base``, returning a new mapping.

    Keys present in only one side are carried through untouched. Keys present
    in both are combined by :func:`merge_values`, so ``overlay`` wins.

    Raises:
        MergeError: the two sides give a key values of differing JSON types.

    """
    merged: JsonObject = dict(base)
    for key, value in overlay.items():
        if key in merged:
            merged[key] = merge_values(merged[key], value, (*pointer, key))
        else:
            merged[key] = value
    return merged


def merge_values(
    base: JsonValue,
    overlay: JsonValue,
    pointer: tuple[str, ...] = (),
) -> JsonValue:
    """
    Combine two values found at the same key.

    Objects merge recursively, arrays concatenate and de-duplicate, and any
    other pair of same-typed values resolves to ``overlay``.

    Raises:
        MergeError: the two values have differing JSON types. Nothing sensible
            can be done with, say, an array meeting an object, and silently
            picking one would hide a mistake in one of the two units.

    """
    match base, overlay:
        case dict(), dict():
            return merge_objects(base, overlay, pointer)
        case list(), list():
            return _merge_arrays(base, overlay)
        case _:
            base_kind = json_kind(base)
            overlay_kind = json_kind(overlay)
            if base_kind != overlay_kind:
                where = _render(pointer)
                detail = f"{base_kind} in one unit, {overlay_kind} in another"
                raise MergeError(f"conflicting types at {where}: {detail}")
            return overlay


def _merge_arrays(base: list[JsonValue], overlay: list[JsonValue]) -> list[JsonValue]:
    """Concatenate two arrays, dropping later duplicates of earlier elements."""
    merged: list[JsonValue] = []
    seen: set[str] = set()
    for item in (*base, *overlay):
        identity = canonical(item)
        if identity in seen:
            continue
        seen.add(identity)
        merged.append(item)
    return merged


def _render(pointer: tuple[str, ...]) -> str:
    """Render a key path as a JSON pointer."""
    if not pointer:
        return "/"
    return "".join(f"/{segment}" for segment in pointer)


@dataclass(frozen=True, slots=True)
class Kept:
    """
    What :func:`keep_additions` made of a live settings file.

    ``settings`` is what to write. ``kept`` are the JSON pointers of what came
    from the live file alone, and ``reset`` those where the live file's own
    change gave way to the units, each sorted.
    """

    settings: JsonObject
    kept: tuple[str, ...]
    reset: tuple[str, ...]


def keep_additions(base: JsonObject, live: JsonObject, new: JsonObject) -> Kept:
    """
    Bring the live file ``live`` in line with ``new``, keeping only its additions.

    ``base`` is what was last generated. Wherever ``new`` sets a value it wins,
    but for an object that ``live`` also has, which is merged the same way, and
    a list that ``live`` also has, which gets the entries of ``live`` that
    neither ``new`` nor ``base`` has, after its own. A key that ``new`` does not
    set goes when ``base`` had it, and keeps the value of ``live`` otherwise.
    """
    kept: list[str] = []
    reset: list[str] = []
    settings = _keep(base, live, new, (), kept, reset)
    return Kept(settings, tuple(sorted(kept)), tuple(sorted(reset)))


def _keep(
    base: JsonObject,
    live: JsonObject,
    new: JsonObject,
    pointer: tuple[str, ...],
    kept: list[str],
    reset: list[str],
) -> JsonObject:
    result: JsonObject = {}
    for key, value in new.items():
        where = (*pointer, key)
        was = base.get(key)
        if key not in live:
            result[key] = value
            continue
        found = live[key]
        match value, found:
            case dict(), dict():
                below = was if isinstance(was, dict) else {}
                result[key] = _keep(below, found, value, where, kept, reset)
            case list(), list():
                before = was if isinstance(was, list) else []
                result[key] = _keep_list(before, found, value, where, kept, reset)
            case _:
                result[key] = value
                if _changed(found, value) and (key not in base or _changed(found, was)):
                    reset.append(_render(where))
    for key, found in live.items():
        if key in new:
            continue
        if key in base:
            if _changed(found, base[key]):
                reset.append(_render((*pointer, key)))
        else:
            result[key] = found
            kept.append(_render((*pointer, key)))
    return result


def _keep_list(
    base: list[JsonValue],
    live: list[JsonValue],
    new: list[JsonValue],
    pointer: tuple[str, ...],
    kept: list[str],
    reset: list[str],
) -> list[JsonValue]:
    """Give ``new``, then the entries ``live`` added, noting what changed."""
    known = {canonical(item) for item in (*new, *base)}
    added = [item for item in live if canonical(item) not in known]
    there = {canonical(item) for item in live}
    generated = {canonical(item) for item in base}
    if any(canonical(i) in generated and canonical(i) not in there for i in new):
        reset.append(_render(pointer))
    if added:
        kept.append(_render(pointer))
    return _merge_arrays(new, added)


def _changed(one: JsonValue, other: JsonValue) -> bool:
    return canonical(one) != canonical(other)
