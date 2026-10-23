"""The rules that combine two config values into one."""

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
