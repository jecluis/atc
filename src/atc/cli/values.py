"""Checking and writing the values typed for the registry's variables."""

from pathlib import Path


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
