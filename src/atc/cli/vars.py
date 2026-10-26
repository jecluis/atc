"""``atc vars``: see the variables of the machine registry."""

from collections.abc import Sequence

import click
import rich_click
from rich import box
from rich.table import Table

from atc.cli.options import current_dirs, load_build_context
from atc.inventory import take_inventory
from atc.registry import REGISTRY_FILE
from atc.ui import Ui, shown

_USERS_SHOWN = 3
"""How many of a variable's users are named before the rest are counted."""


@click.group(cls=rich_click.RichGroup, name="vars")
def vars_() -> None:
    """List the variables of the machine registry."""


@vars_.command(cls=rich_click.RichCommand, name="ls")
def ls() -> None:
    """
    List every variable that machine.yaml defines or that a unit needs.

    A unit names a variable with its level: {{vars.NAME}} is the machine's,
    {{account.vars.NAME}} an account's and {{project.vars.NAME}} a project's,
    and the registry defines it in the vars of that owner. Shows one row per
    variable and owner: the reference, the owner, the value or that it is
    undefined, and the units and templated assets that use it.

    The references are looked for in each account's unit, in every templated
    asset and in the unit of each project the account may work on, and in
    each project's unit, all with what they import. Variables the registry
    defines and nothing uses are listed too. Rows come by owner, the machine's
    first, then the accounts' and the projects' in the order of the file.

    If an account's or a project's units cannot be read, a warning says so and
    the rest is still listed. When a variable is undefined, a line says how
    many are. Nothing is changed.
    """
    ui = Ui.default()
    path = current_dirs().config_dir / REGISTRY_FILE
    if not path.exists():
        message = f"no variables: {path} does not exist; atc account add creates it"
        ui.info(message)
        return
    ctx = load_build_context(None)
    for warning in (*ctx.sources.warnings, *ctx.registry.warnings):
        ui.warning(warning)
    inventory = take_inventory(ctx)
    for warning in inventory.warnings:
        ui.warning(warning)
    if not inventory.variables:
        ui.info("no variables are defined or used")
        return
    table = Table(box=box.SIMPLE_HEAD, pad_edge=False, padding=(0, 1, 0, 0))
    table.add_column("Variable", no_wrap=True)
    table.add_column("Owner", no_wrap=True)
    table.add_column("Value", overflow="fold", min_width=12)
    table.add_column("Used by", overflow="fold", min_width=16)
    for variable in inventory.variables:
        value = (
            shown("undefined", style="bold red")
            if variable.value is None
            else shown(variable.value)
        )
        users = (
            shown(_users_text(variable.users))
            if variable.users
            else shown("-", style="dim")
        )
        table.add_row(
            shown(variable.reference), shown(str(variable.owner)), value, users
        )
    ui.table(table)
    undefined = len(inventory.undefined())
    if undefined:
        what = "variable is" if undefined == 1 else "variables are"
        ui.info(f"{undefined} {what} undefined")


def _users_text(users: Sequence[str]) -> str:
    """Name the first of ``users``, and count the rest."""
    text = ", ".join(users[:_USERS_SHOWN])
    if len(users) > _USERS_SHOWN:
        text += f" and {len(users) - _USERS_SHOWN} more"
    return text
