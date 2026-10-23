"""Expanding ``{{name}}`` references in a unit's settings."""

import re
from collections.abc import Mapping
from typing import final

from atc.errors import TemplateError
from atc.jsonval import JsonObject, JsonValue

type VarValue = str | tuple[str, ...]
"""A variable is one string, or a list of strings."""

REFERENCE = re.compile(r"\{\{([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*)\}\}")
"""A reference: dotted names in double braces, with no whitespace."""

_OPENING = re.compile(r"(?=\{\{)")

_SYNTAX = "write {{name}}, with lowercase letters, digits, '_' and '.', and no spaces"


def expand(
    settings: JsonObject, variables: Mapping[str, VarValue], *, origin: str
) -> JsonObject:
    """
    Replace the references in the strings of ``settings``.

    A string reference is replaced in place. A list reference expands the
    nearest enclosing array element once per item, in order, and an empty list
    removes the element. An element can use only one list reference. Object
    keys are never substituted, and substituted text is not scanned again.

    ``origin`` names the unit and the layer for the error messages, which also
    give the JSON pointer of the value at fault.

    Raises:
        TemplateError: a reference is unknown or malformed, a list reference is
            outside an array, an element uses two, or a key contains ``{{``.

    """
    return _Expander(variables, origin).mapping(settings, {}, "")


@final
class _Expander:
    def __init__(self, variables: Mapping[str, VarValue], origin: str) -> None:
        self._variables = variables
        self._origin = origin

    def value(
        self, value: JsonValue, bound: Mapping[str, str], pointer: str
    ) -> JsonValue:
        """Expand ``value``; ``bound`` holds the list items chosen so far."""
        match value:
            case str():
                return self._substitute(value, bound, pointer)
            case dict():
                return self.mapping(value, bound, pointer)
            case list():
                items: list[JsonValue] = []
                for index, element in enumerate(value):
                    items.extend(self._element(element, bound, f"{pointer}/{index}"))
                return items
            case _:
                return value

    def mapping(
        self, value: JsonObject, bound: Mapping[str, str], pointer: str
    ) -> JsonObject:
        """Expand the members of ``value``; its keys must not hold references."""
        result: JsonObject = {}
        for key, member in value.items():
            here = f"{pointer}/{_escape(key)}"
            if "{{" in key:
                raise self._error(here, "object keys are never substituted")
            result[key] = self.value(member, bound, here)
        return result

    def _element(
        self, element: JsonValue, bound: Mapping[str, str], pointer: str
    ) -> list[JsonValue]:
        """Expand an array element once for each item of the list reference in it."""
        lists = self._list_references(element, bound)
        if not lists:
            return [self.value(element, bound, pointer)]
        if len(lists) > 1:
            names = " and ".join(f"{{{{{name}}}}}" for name in lists)
            raise self._error(
                pointer, f"only one list reference per array element, found {names}"
            )
        ((name, items),) = lists.items()
        if not items:
            # The element goes, but it must be as valid as it is for an account
            # whose list has items.
            _ = self.value(element, {**bound, name: ""}, pointer)
        return [self.value(element, {**bound, name: item}, pointer) for item in items]

    def _list_references(
        self, node: JsonValue, bound: Mapping[str, str]
    ) -> dict[str, tuple[str, ...]]:
        """
        Find the unbound list references in ``node``, outside any nested array.

        Nested arrays are their own elements, and bind their own references.
        """
        found: dict[str, tuple[str, ...]] = {}
        match node:
            case str():
                for match in REFERENCE.finditer(node):
                    name = match[1]
                    variable = self._variables.get(name)
                    if isinstance(variable, tuple) and name not in bound:
                        found[name] = variable
            case dict():
                for member in node.values():
                    found.update(self._list_references(member, bound))
            case _:
                # An array is its own element, so nothing in it counts here.
                pass
        return found

    def _substitute(self, text: str, bound: Mapping[str, str], pointer: str) -> str:
        starts = {match.start() for match in REFERENCE.finditer(text)}
        for opening in _OPENING.finditer(text):
            if opening.start() not in starts:
                raise self._error(
                    pointer, f"malformed reference in '{text}'; {_SYNTAX}"
                )

        def replace(match: re.Match[str]) -> str:
            name = match[1]
            if name in bound:
                return bound[name]
            variable = self._variables.get(name)
            if variable is None:
                raise self._error(pointer, f"unknown reference {{{{{name}}}}}")
            if isinstance(variable, tuple):
                message = f"list reference {{{{{name}}}}} outside an array element"
                raise self._error(pointer, message)
            return variable

        return REFERENCE.sub(replace, text)

    def _error(self, pointer: str, message: str) -> TemplateError:
        return TemplateError(f"{self._origin}: {pointer}: {message}")


def _escape(key: str) -> str:
    """Escape ``key`` as a JSON pointer segment."""
    return key.replace("~", "~0").replace("/", "~1")
