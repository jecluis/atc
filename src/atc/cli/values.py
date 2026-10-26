"""Checking and writing the values typed for the registry's variables."""

from collections.abc import Sequence
from pathlib import Path

USERS_SHOWN = 3
"""How many of a variable's users are named before the rest are counted."""


def value_fault(text: str) -> str | None:
    """Say what is wrong with ``text`` as a variable's value, if anything."""
    value = text.strip()
    if not value:
        return "a value is needed"
    if "{{" in value:
        return "the value must not contain '{{'"
    return None


def as_value(text: str) -> str:
    """
    Write ``text`` as a variable's value, with a leading ``~`` expanded.

    A variable is used as written, so a ``~`` left in it would not be your home
    directory wherever the value goes.
    """
    value = text.strip()
    if value == "~" or value.startswith("~/"):
        return str(Path.home()) + value[1:]
    return value


def users_text(users: Sequence[str]) -> str:
    """Name the first of ``users``, and count the rest."""
    text = ", ".join(users[:USERS_SHOWN])
    if len(users) > USERS_SHOWN:
        text += f" and {len(users) - USERS_SHOWN} more"
    return text
