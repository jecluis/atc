"""``atc account``: see and change the accounts of the machine registry."""

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import click
import rich_click
from rich import box
from rich.table import Table
from rich.text import Text

from atc.cli.options import (
    cancelled,
    current_dirs,
    load_machine_registry,
    load_sources,
)
from atc.cli.skills import skill_choices
from atc.cli.values import as_value, users_text, value_fault
from atc.context import BuildContext, current_install_dir, current_python_dir
from atc.errors import PathError, RegistryError, WriteError
from atc.jsonval import JsonObject, JsonValue
from atc.needed import Needed, needed_variables
from atc.refs import resolve_registry
from atc.registry import (
    AGENTS,
    DEFAULT_AGENTS_MD,
    REGISTRY_FILE,
    Account,
    AccountRemoval,
    AccountUses,
    Registry,
    account_name_problem,
    add_account,
    normalize_path,
)
from atc.skills import discover_skills
from atc.sources import Sources
from atc.ui import Ui, need_terminal, shown


@click.group(cls=rich_click.RichGroup, name="account")
def account() -> None:
    """List, add and remove the accounts of the machine registry; atc acc for short."""


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
        message = (
            f"no accounts are registered: {path} does not exist; "
            "atc account add creates it"
        )
        ui.info(message)
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


@account.command(cls=rich_click.RichCommand, name="add")
@click.argument("name", metavar="NAME")
@click.option(
    "-u",
    "--unit",
    "unit",
    metavar="UNIT",
    help=(
        "The unit the account's settings are built from, as a unit reference: "
        "a path under a config source's conf, with @source/ in front when "
        "several sources have it. Defaults to accounts/NAME.json."
    ),
)
@click.option(
    "--agent",
    type=click.Choice(AGENTS),
    default=AGENTS[0],
    show_default=True,
    help="The coding agent the account is for.",
)
def add(name: str, unit: str | None, agent: str) -> None:
    """
    Add an account to the machine registry, asking for what it needs.

    NAME is the account's name. Its unit must be in a config source; when it
    is not, the error lists the account units the sources have. atc then asks
    for the account's directory, with path completion, which no other account
    may have and which need not exist yet. When the sources have the default
    instructions file it asks whether to link it, and when they have skills,
    whether to pick some now, from the checklist of "atc skills pick". Last, it
    asks for each variable that nothing defines yet and that the account's
    units, the templated assets or the units of the projects it may work on use:
    the account's own, or machine-wide ones; a leading ~ in a value is written
    as your home directory. What else would stop the account from being built
    is reported before anything is asked, but for settings keys Claude Code
    does not define, which "atc src check" reports.

    The entry goes after the others in machine.yaml, and the rest of the file
    stays as it was, comments included. When there is no machine.yaml, it is
    created, with the account as its default account. Needs a terminal, to ask.
    Ctrl-C cancels: nothing is changed, and the status is 130.
    """
    ui = Ui.default()
    need_terminal("atc account add")
    problem = account_name_problem(name)
    if problem is not None:
        raise RegistryError(problem)
    path = current_dirs().config_dir / REGISTRY_FILE
    registry = load_machine_registry(ui) if path.exists() else None
    if registry is not None and name in registry.accounts:
        message = f"{path}: /accounts/{name}: an account of that name exists already"
        raise RegistryError(message)
    _check_creatable(path)
    sources = load_sources(None)
    catalog = discover_skills(sources)
    for warning in (*sources.warnings, *catalog.warnings):
        ui.warning(warning)
    reference = f"accounts/{name}.json" if unit is None else unit
    _find_unit(sources, reference)
    instructions = _instructions(sources, registry, ui)
    needed = _needed(
        path, registry, sources, Account(name, agent, Path(), reference, {})
    )
    # Everything that can refuse the account has; what is left is to ask.
    taken = (
        {} if registry is None else {a.dir: a.name for a in registry.accounts.values()}
    )
    directory = ui.ask_path(
        f"The directory of account {name}:",
        default=_suggested_dir(name, taken),
        check=lambda text: _dir_fault(text, taken),
        directories=True,
    )
    if directory is None:
        cancelled(ui)
    fields: JsonObject = {"agent": agent, "dir": _as_written(directory)}
    if reference != f"accounts/{name}.json":
        fields["unit"] = reference
    if instructions is not None:
        link = ui.confirm(
            f"Link {instructions} as the account's instructions file?", default=True
        )
        if link is None:
            cancelled(ui)
        if link:
            fields["use-default-agents-md"] = True
    if catalog.skills:
        now = ui.confirm("Pick skills for the account now?", default=False)
        if now is None:
            cancelled(ui)
        if now:
            picked = ui.pick(f"Skills for {name}", skill_choices(catalog, ()))
            if picked is None:
                cancelled(ui)
            if picked:
                fields["skills"] = list[JsonValue](picked)
    own: JsonObject = {}
    machine: dict[str, str] = {}
    for need in needed:
        value = ui.ask_path(_variable_question(need), check=value_fault)
        if value is None:
            cancelled(ui)
        if need.machine_wide:
            machine[need.name] = as_value(value)
        else:
            own[need.name] = as_value(value)
    if own:
        fields["vars"] = own
    updated = add_account(path, Path.home(), name, fields, machine)
    added = f"{name}: added to {updated.path}"
    ui.line(added if registry is not None else f"{added}, as the default account")
    for step in _next_steps(updated, name):
        ui.info(step)


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
            cancelled(ui)
    edited, after = removal.edited(default)
    for change in _changes(uses, after.default_account):
        ui.info(change)
    answer = ui.confirm(f"Remove account {name}?", default=False)
    if answer is None:
        cancelled(ui)
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


def _find_unit(sources: Sources, reference: str) -> None:
    """
    Make sure that the unit ``reference`` is in exactly one config source.

    Raises:
        PathError: it is malformed, or several sources have it, or none does. In
            that last case, the message lists the account units there are.

    """
    units = [
        source.display(path.relative_to(source.conf).as_posix())
        for source in sources.items
        for path in sorted((source.conf / "accounts").rglob("*.json"))
        if path.is_file()
    ]
    there = (
        f"the account units there are {', '.join(units)}"
        if units
        else "no source has an account unit, under conf/accounts/"
    )
    try:
        found = resolve_registry(sources, reference)
    except PathError as exc:
        # One that names its source fails here when that source lacks it.
        if not reference.startswith("@"):
            raise
        raise PathError(f"{exc}; {there}") from exc
    if found is None:
        raise PathError(f"'{reference}': no config source has it; {there}")


def _needed(
    path: Path, registry: Registry | None, sources: Sources, account: Account
) -> list[Needed]:
    """
    Find the variables that the new ``account`` needs, with it in the registry.

    ``registry`` is ``None`` when there is no file at ``path`` yet. The account's
    directory is not known yet, and nothing found here depends on it.

    Raises:
        ConfigError: as :func:`atc.needed.needed_variables`.

    """
    if registry is None:
        holding = Registry(path, {}, account.name, {account.name: account}, {}, ())
    else:
        holding = replace(
            registry, accounts={**registry.accounts, account.name: account}
        )
    ctx = BuildContext(
        registry=holding,
        sources=sources,
        dirs=current_dirs(),
        home=Path.home(),
        install_dir=current_install_dir(),
        python_dir=current_python_dir(),
    )
    return needed_variables(ctx, account)


def _variable_question(need: Needed) -> str:
    """Ask for the value of the variable ``need``, naming what uses it."""
    kind = "machine-wide variable" if need.machine_wide else "account variable"
    return f"The {kind} {need.name}, used by {users_text(need.users)}:"


def _next_steps(registry: Registry, name: str) -> list[str]:
    """Say what is left to do once the account ``name`` is in ``registry``."""
    directory = registry.accounts[name].dir
    sync = (
        "run atc sync"
        if len(registry.accounts) == 1
        else (
            "run atc sync, for every account: each account's settings keep its "
            "sessions out of the other accounts' directories"
        )
    )
    if directory.exists():
        return [f"next, {sync}"]
    missing = (
        f"{directory} does not exist yet, and atc sync needs it: log in to the "
        "account first, which makes the agent create it"
    )
    return [missing, f"then {sync}"]


def _check_creatable(path: Path) -> None:
    """
    Make sure that the registry file ``path`` can be created where it is missing.

    A symbolic link that leads nowhere is written through, so the directory it
    leads into must be there.

    Raises:
        WriteError: that directory is not.

    """
    if path.exists():
        return
    place = path.resolve().parent
    if not place.is_dir():
        raise WriteError(f"{path}: cannot be created: {place} is not a directory")


def _instructions(sources: Sources, registry: Registry | None, ui: Ui) -> str | None:
    """
    Find the default instructions file to offer, if the sources have it.

    That is the asset that the registry's ``default-agents-md`` names. One that
    is ambiguous, or that the registry names wrongly, is not offered: a warning
    says why.
    """
    asset = DEFAULT_AGENTS_MD if registry is None else registry.default_agents_md
    try:
        found = resolve_registry(sources, asset, "asset")
    except PathError as exc:
        ui.warning(f"not offering {asset} as the instructions file: {exc}")
        return None
    return None if found is None else asset


def _suggested_dir(name: str, taken: Mapping[Path, str]) -> str:
    """Suggest a directory for the account ``name``: ``~/.claude`` while it is free."""
    first = "~/.claude"
    if normalize_path(first, Path.home()) not in taken:
        return first
    return f"~/.config/{name}/claude"


def _dir_fault(text: str, taken: Mapping[Path, str]) -> str | None:
    """Say what is wrong with ``text`` as a new account's directory, if anything."""
    try:
        directory = normalize_path(text.strip(), Path.home())
    except RegistryError as exc:
        return str(exc)
    owner = taken.get(directory)
    return None if owner is None else f"account '{owner}' has that directory"


def _as_written(text: str) -> str:
    """Write a directory as typed, but with no trailing ``/``."""
    return text.strip().rstrip("/") or "/"
