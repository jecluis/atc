"""Checking and writing the values typed for variables, and when changes apply."""

from collections.abc import Sequence
from pathlib import Path

from atc.inventory import Effect
from atc.ui import Ui

USERS_SHOWN = 3
"""How many of a variable's users are named before the rest are counted."""
SYNC_NOTE = (
    "account units and templated assets that use these variables get the change "
    "once atc sync runs"
)
"""When a change reaches an account's units and the templated assets."""
RUN_NOTE = (
    "project units that use these variables get the change at their project's "
    "next atc run"
)
"""When a change reaches a project's units."""


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


def say_when(ui: Ui, effects: frozenset[Effect] | None) -> None:
    """
    Say when a change to variables reaches what uses them.

    ``effects`` are where it takes effect, or ``None`` when what uses them
    cannot all be known, which says both. Nothing is said when nothing uses
    them.
    """
    if effects is None or "sync" in effects:
        ui.info(SYNC_NOTE)
    if effects is None or "run" in effects:
        ui.info(RUN_NOTE)
