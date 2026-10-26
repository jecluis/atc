"""``atc account``: see and change the accounts of the machine registry."""

from pathlib import Path
from typing import NoReturn

import click
import rich_click
from rich import box
from rich.table import Table
from rich.text import Text

from atc import exitcodes
from atc.cli.options import current_dirs, load_machine_registry
from atc.errors import RegistryError
from atc.registry import REGISTRY_FILE, AccountRemoval, AccountUses
from atc.ui import Ui, need_terminal, shown


@click.group(cls=rich_click.RichGroup, name="account")
def account() -> None:
    """List and remove the accounts of the machine registry; atc acc for short."""


@account.command(cls=rich_click.RichCommand, name="ls")
def ls() -> None:
    """
    List the registered accounts.

    Shows one row per account in the machine registry, machine.yaml in atc's
    configuration directory, in the order of the file: its name, agent,
    directory and unit, with the default unit filled in. The default account is
    marked. Nothing is changed.
    """
    ui = Ui.default()
    path = current_dirs().config_dir / REGISTRY_FILE
    if not path.exists():
        ui.info(f"no accounts are registered: {path} does not exist")
        return
    registry = load_machine_registry(ui)
    table = Table(box=box.SIMPLE_HEAD, pad_edge=False, padding=(0, 1, 0, 0))
    table.add_column("Name", no_wrap=True)
    table.add_column("Agent", no_wrap=True)
    table.add_column("Directory", overflow="fold", min_width=16)
    table.add_column("Unit", overflow="fold", min_width=16)
    for found in registry.accounts.values():
        name = shown(found.name)
        if found.name == registry.default_account:
            name = Text.assemble(name, (" (default)", "dim"))
        table.add_row(
            name, shown(found.agent), shown(str(found.dir)), shown(found.unit)
        )
    ui.table(table)


@account.command(cls=rich_click.RichCommand, name="rm")
@click.argument("name", metavar="NAME")
@click.option(
    "--force",
    is_flag=True,
    help=(
        "Remove the account even when it is the default account or a project "
        "names it, and take it out of those places."
    ),
)
def rm(name: str, force: bool) -> None:
    """
    Remove an account from the machine registry.

    Deletes the entry of the account NAME from machine.yaml, with the comment
    lines directly above it, and asks first, no being the answer that enter
    alone gives. The rest of the file stays as it was, comments included.
    Nothing else is touched: not the account's directory, not its units and not
    the copies atc rendered for it. An unknown NAME is an error that lists the
    accounts there are.

    The default account, and an account that a project uses by default or
    allows, is removed only with --force. Then a question picks the new default
    account if NAME was the default, a project that used NAME by default falls
    back to the default account, and NAME leaves every project's accounts. The
    only account, and the only account a project allows, are never removed.
    Needs a terminal, to ask. Ctrl-C cancels: nothing is changed, and the status
    is 130.
    """
    ui = Ui.default()
    path = current_dirs().config_dir / REGISTRY_FILE
    removal = AccountRemoval.plan(path, Path.home(), name)
    for warning in removal.registry.warnings:
        ui.warning(warning)
    uses = removal.uses
    if uses and not force:
        message = (
            f"{path}: account '{name}' is in use: "
            f"{'; '.join(_uses(uses))}; give --force to take it out of there too"
        )
        raise RegistryError(message)
    # Whatever refuses the removal does so before anything is asked.
    candidates = removal.defaults() if uses.default else []
    if not uses.default:
        _ = removal.edited(None)
    need_terminal("atc account rm")
    default = None
    if candidates:
        default = ui.choose(f"The default account in place of {name}:", candidates)
        if default is None:
            _cancelled(ui)
    edited, after = removal.edited(default)
    for change in _changes(uses, after.default_account):
        ui.info(change)
    answer = ui.confirm(f"Remove account {name}?", default=False)
    if answer is None:
        _cancelled(ui)
    if not answer:
        ui.info("nothing changed")
        return
    updated = removal.write(edited)
    ui.line(f"{name}: removed from {updated.path}")
    directory = removal.registry.accounts[name].dir
    if directory.exists():
        message = (
            f"{directory} is still there, credentials included, and once the "
            "other accounts are synced again their sessions are no longer kept "
            "out of it; delete it if it is no longer needed"
        )
        ui.warning(message)


def _uses(uses: AccountUses) -> list[str]:
    """Say where an account is in use, one phrase for each place."""
    phrases = ["it is the default account"] if uses.default else []
    phrases.extend(f"project '{p}' uses it by default" for p in uses.defaulted)
    phrases.extend(
        f"project '{p}' allows it" for p in uses.allowed if p not in uses.defaulted
    )
    return phrases


def _changes(uses: AccountUses, default: str) -> list[str]:
    """Say what else removing an account with ``uses`` changes, to ``default``."""
    changes = [f"the default account becomes '{default}'"] if uses.default else []
    changes.extend(
        f"project '{p}' falls back to the default account '{default}'"
        for p in uses.defaulted
    )
    changes.extend(f"project '{p}' no longer allows it" for p in uses.allowed)
    return changes


def _cancelled(ui: Ui) -> NoReturn:
    ui.info("cancelled; nothing changed")
    raise click.exceptions.Exit(exitcodes.INTERRUPTED)
