"""``atc vars``: see and define the variables of the machine registry."""

from pathlib import Path

import click
import rich_click
from rich import box
from rich.table import Table

from atc.cli.options import (
    cancelled,
    current_dirs,
    load_build_context,
    load_machine_registry,
)
from atc.cli.values import as_value, say_when, users_text, value_fault
from atc.errors import ConfigError, RegistryError
from atc.inventory import (
    MACHINE,
    Effect,
    Inventory,
    Owner,
    Variable,
    take_inventory,
)
from atc.registry import (
    REGISTRY_FILE,
    Registry,
    VariableRemoval,
    set_variables,
    variable_name_problem,
    variables_at,
)
from atc.ui import Ui, need_terminal, shown


@click.group(cls=rich_click.RichGroup, name="vars")
def vars_() -> None:
    """List, define and remove the variables of the machine registry."""


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
            shown(users_text(variable.users))
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
        ui.info(f"{undefined} {what} undefined; atc vars set asks for them")


@vars_.command(cls=rich_click.RichCommand, name="set")
@click.argument("name", required=False, metavar="[NAME]")
@click.argument("value", required=False, metavar="[VALUE]")
@click.option(
    "-a",
    "--account",
    "account_name",
    metavar="ACCOUNT",
    help="Define it in the vars of ACCOUNT, for {{account.vars.NAME}}; not with -p.",
)
@click.option(
    "-p",
    "--project",
    "project_name",
    metavar="PROJECT",
    help="Define it in the vars of PROJECT, for {{project.vars.NAME}}; not with -a.",
)
def set_(
    name: str | None,
    value: str | None,
    account_name: str | None,
    project_name: str | None,
) -> None:
    """
    Define a variable, or each undefined one in turn.

    NAME goes in the vars of ACCOUNT with -a, of PROJECT with -p, and in the
    machine-wide vars with neither, which is where {{account.vars.NAME}},
    {{project.vars.NAME}} and {{vars.NAME}} find it. A value that was there is
    replaced, and the line says what it was and what it is now. A value must
    not be empty or contain {{, and a leading ~ is written as your home
    directory. When nothing uses NAME there, a warning says so, since it may be
    misspelt, and it is written anyway. A new machine-wide vars goes before the
    accounts.

    Without VALUE, it asks for it, starting from the current value. Without
    NAME, it asks for every variable that atc vars ls lists as undefined, or
    only those of ACCOUNT or PROJECT with -a or -p, and an empty answer leaves
    that one undefined. What is answered is written at the end, in one edit.
    Asking needs a terminal. Ctrl-C cancels: nothing is changed, and the status
    is 130.

    It ends by saying when the change reaches what uses the variable, by where
    that is built: an account's units and the templated assets once atc sync
    runs, and a project's units at the project's next atc run.
    """
    ui = Ui.default()
    owner = _owner(account_name, project_name)
    if name is None:
        only = None if account_name is None and project_name is None else owner
        _set_undefined(ui, only)
        return
    problem = variable_name_problem(name)
    if problem is not None:
        raise click.BadParameter(problem, param_hint="NAME")
    if value is not None:
        fault = value_fault(value)
        if fault is not None:
            raise click.BadParameter(fault, param_hint="VALUE")
    registry = load_machine_registry(ui)
    reference = owner.reference(name)
    old = variables_at(registry, owner.keys).get(name)
    if value is None:
        need_terminal("atc vars set without a VALUE", "give the VALUE")
        answer = ui.ask_path(
            f"{reference} for {owner}:", default=old or "", check=value_fault
        )
        if answer is None:
            cancelled(ui)
        value = answer
    written = as_value(value)
    updated = set_variables(registry.path, Path.home(), [(owner.keys, name, written)])
    was = "" if old is None else f", was {old}"
    ui.line(f"{reference}: set to {written} for {owner} in {updated.path}{was}")
    inventory = _inventory(ui)
    if inventory is not None and not _users(inventory, owner, name):
        ui.warning(f"nothing uses {reference} for {owner}; is the name right?")
    say_when(ui, _effects(inventory, [(owner, name)]))


@vars_.command(cls=rich_click.RichCommand, name="rm")
@click.argument("name", metavar="NAME")
@click.option(
    "-a",
    "--account",
    "account_name",
    metavar="ACCOUNT",
    help="Remove it from the vars of ACCOUNT; not with -p.",
)
@click.option(
    "-p",
    "--project",
    "project_name",
    metavar="PROJECT",
    help="Remove it from the vars of PROJECT; not with -a.",
)
@click.option(
    "--force",
    is_flag=True,
    help=(
        "Remove it even when something uses it, which leaves that undefined, or "
        "when what uses it cannot all be known."
    ),
)
def rm(
    name: str, account_name: str | None, project_name: str | None, force: bool
) -> None:
    """
    Remove a variable from the machine registry.

    NAME is taken from the vars of ACCOUNT with -a, of PROJECT with -p, and from
    the machine-wide vars with neither. A variable not defined there is an error
    that lists the ones that are. One that a unit or a templated asset uses is
    refused unless --force is given, since it would then be undefined, and so
    is any variable when what uses it cannot all be known: when the config
    sources do not load, or the units of an account or project that could use
    it cannot be read.
    A vars left empty goes with it, with the comment lines directly above it,
    and the rest of the file stays as it was.

    It asks first, no being the answer that enter alone gives. Needs a terminal,
    to ask. Ctrl-C cancels: nothing is changed, and the status is 130. If
    machine.yaml changes while it waits for the answer, nothing is written.
    """
    ui = Ui.default()
    owner = _owner(account_name, project_name)
    path = current_dirs().config_dir / REGISTRY_FILE
    removal = VariableRemoval.plan(path, Path.home(), owner.keys, name)
    for warning in removal.registry.warnings:
        ui.warning(warning)
    reference = owner.reference(name)
    inventory = _inventory(ui)
    users = _users(inventory, owner, name)
    unread = _unread_users(inventory, removal.registry, owner)
    if users and not force:
        message = (
            f"{path}: {reference} for {owner} is used by {users_text(users)}; "
            "give --force to remove it anyway"
        )
        raise RegistryError(message)
    if (unread is None or unread) and not force:
        why = (
            "the config sources do not load"
            if unread is None
            else f"the units of {', '.join(map(str, unread))} cannot be read"
        )
        message = (
            f"{path}: what uses {reference} for {owner} cannot all be known, as "
            f"{why}; give --force to remove it anyway"
        )
        raise RegistryError(message)
    need_terminal("atc vars rm")
    if users:
        ui.info(f"{users_text(users)} will find {reference} undefined")
    answer = ui.confirm(f"Remove {name} from {owner}?", default=False)
    if answer is None:
        cancelled(ui)
    if not answer:
        ui.info("nothing changed")
        return
    updated = removal.write()
    ui.line(f"{reference}: removed from {owner} in {updated.path}")
    say_when(ui, _effects(inventory, [(owner, name)]))


def _owner(account: str | None, project: str | None) -> Owner:
    """Give the owner that ``-a`` or ``-p`` names, or the machine for neither."""
    if account is not None and project is not None:
        raise click.UsageError("give -a or -p, not both")
    if account is not None:
        return Owner("account", account)
    if project is not None:
        return Owner("project", project)
    return MACHINE


def _set_undefined(ui: Ui, only: Owner | None) -> None:
    """Ask for each undefined variable, or each of ``only``, and write the answers."""
    need_terminal("atc vars set without a NAME", "give the NAME and the VALUE")
    ctx = load_build_context(None)
    for warning in (*ctx.sources.warnings, *ctx.registry.warnings):
        ui.warning(warning)
    if only is not None:
        _ = only.defined(ctx.registry)
    inventory = take_inventory(ctx)
    for warning in inventory.warnings:
        ui.warning(warning)
    asked = [v for v in inventory.undefined() if only is None or v.owner == only]
    if not asked:
        ui.info("no variable is undefined" + ("" if only is None else f" for {only}"))
        return
    answers: list[tuple[Variable, str]] = []
    for variable in asked:
        answer = ui.ask_path(_question(variable), check=_optional_value_fault)
        if answer is None:
            cancelled(ui)
        if answer.strip():
            answers.append((variable, as_value(answer)))
    if answers:
        values = [(v.owner.keys, v.name, value) for v, value in answers]
        updated = set_variables(ctx.registry.path, Path.home(), values)
        for variable, value in answers:
            where = f"for {variable.owner} in {updated.path}"
            ui.line(f"{variable.reference}: set to {value} {where}")
    left = len(asked) - len(answers)
    if left:
        what = "variable is" if left == 1 else "variables are"
        ui.info(f"{left} {what} still undefined")
    if answers:
        say_when(ui, inventory.effects_of((v.owner, v.name) for v, _ in answers))
    else:
        ui.info("nothing changed")


def _question(variable: Variable) -> str:
    """Ask for the value of ``variable``, naming its owner and what uses it."""
    return (
        f"{variable.reference} for {variable.owner}, used by "
        f"{users_text(variable.users)} (empty to skip):"
    )


def _optional_value_fault(text: str) -> str | None:
    """Say what is wrong with ``text`` as a value, where no value at all is fine."""
    return None if not text.strip() else value_fault(text)


def _inventory(ui: Ui) -> Inventory | None:
    """
    Take the inventory, for what uses a variable, or ``None`` when it cannot be.

    The sources not loading is no reason to refuse a change to the registry, so
    a warning says that what uses the variable is unknown.
    """
    try:
        inventory = take_inventory(load_build_context(None))
    except ConfigError as exc:
        ui.warning(f"cannot tell what uses the variable: {exc}")
        return None
    for warning in inventory.warnings:
        ui.warning(warning)
    return inventory


def _unread_users(
    inventory: Inventory | None, registry: Registry, owner: Owner
) -> list[Owner] | None:
    """
    Give the owners whose units could use a variable of ``owner``, but were unread.

    Any owner could use a machine-wide variable, only the project its own, and
    an account's only the account and the projects that allow it. ``None``
    means the inventory could not be taken, so that no unit was read.
    """
    if inventory is None:
        return None
    could: list[Owner] | None
    match owner.level:
        case "machine":
            could = None
        case "project":
            could = [owner]
        case "account":
            could = [owner] + [
                Owner("project", project.name)
                for project in registry.projects.values()
                if owner.name in registry.allowed_accounts(project)
            ]
    return [o for o in inventory.unreadable if could is None or o in could]


def _effects(
    inventory: Inventory | None, changed: list[tuple[Owner, str]]
) -> frozenset[Effect] | None:
    """Give where changes to ``changed`` take effect, ``None`` if not all known."""
    return None if inventory is None else inventory.effects_of(changed)


def _users(inventory: Inventory | None, owner: Owner, name: str) -> tuple[str, ...]:
    """Give what uses the variable ``name`` of ``owner``, as far as is known."""
    if inventory is None:
        return ()
    found = (v for v in inventory.variables if v.owner == owner and v.name == name)
    return next((v.users for v in found), ())
