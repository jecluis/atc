"""``atc usage``: show what each account's sessions last recorded."""

from datetime import UTC, datetime

import click
import rich_click
from rich import box
from rich.table import Table
from rich.text import Text

from atc.cli.options import HELP_OPTIONS, current_dirs, load_machine_registry
from atc.ui import Ui, shown
from atc.usage import UsageRecord, format_age, read_usage

RED_FROM = 70
"""The whole percentage from which usage is red, as in the statusline."""


@click.command(cls=rich_click.RichCommand, name="usage", context_settings=HELP_OPTIONS)
def usage() -> None:
    """
    Show each account's recorded five-hour usage.

    Shows one row per account in the machine registry, in the order of
    machine.yaml: how much of the five-hour limit the account's last record says
    was used, and how long ago that was recorded. The percentage is rounded, and
    on a terminal it is yellow below 70 % and red from 70 %, as in the
    statusline. An account whose record has no five-hour figure shows - for it.

    The statusline of a session that "atc run" starts records the account's
    usage in $XDG_STATE_HOME/atc/usage/ACCOUNT.json (~/.local/state/atc/usage
    when unset). An account with no record shows "no data", and so does one
    whose record cannot be used, which also gets a warning on standard error.
    A percentage outside 0 to 100 is shown clamped to 0 or 100, with a warning.
    Usage from other machines, or from claude.ai, is never recorded. "atc -a
    auto" starts the least used account of a project from these records.
    Nothing is changed.
    """
    ui = Ui.default()
    registry = load_machine_registry(ui)
    dirs = current_dirs()
    table = Table(box=box.SIMPLE_HEAD, pad_edge=False, padding=(0, 1, 0, 0))
    table.add_column("Account", no_wrap=True)
    table.add_column("5-hour", no_wrap=True)
    table.add_column("Recorded", no_wrap=True)
    for name in registry.accounts:
        record, warning = read_usage(dirs, name)
        if warning is not None:
            ui.warning(warning)
        table.add_row(shown(name), *_cells(record))
    ui.table(table)


def _cells(record: UsageRecord | None) -> tuple[Text, Text]:
    """Describe ``record`` as the percentage and the age, or as no data."""
    if record is None:
        return shown("no data", style="dim"), _dash()
    age = format_age(datetime.now(UTC) - record.recorded_at)
    if record.five_hour_pct is None:
        return _dash(), shown(age)
    # Rounded first, as the statusline does, so the colour agrees with the number.
    whole = round(record.five_hour_pct)
    return shown(f"{whole}%", style="red" if whole >= RED_FROM else "yellow"), shown(
        age
    )


def _dash() -> Text:
    return shown("-", style="dim")
