"""``atc skills``: look at the skills the config sources offer."""

import stat
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import click
import rich_click
from rich import box
from rich.table import Table
from rich.text import Text

from atc import exitcodes
from atc.backends import backend_for
from atc.cli.options import chosen_accounts, conf_d_option, load_build_context
from atc.errors import ConfigError
from atc.links import LinkPlan
from atc.skills import Catalog, Skill, discover_skills
from atc.sync import AccountPlan, plan_account
from atc.ui import Ui

DESCRIPTION_WIDTH = 60

type _State = Literal["linked", "selected", "conflict"]

_STYLES: dict[_State, str] = {"linked": "green", "selected": "", "conflict": "yellow"}


@click.group(cls=rich_click.RichGroup, name="skills")
def skills() -> None:
    """Work with the skills the config sources offer."""


@skills.command(cls=rich_click.RichCommand, name="ls")
@click.argument("names", nargs=-1, metavar="[ACCOUNT]...")
@conf_d_option
def ls(names: tuple[str, ...], conf_d: Path | None) -> None:
    """
    List the skills in the config sources, and where each account has them.

    Shows one row per skill: its name, its source and the start of its
    description. There is then one column for each ACCOUNT, or for every
    account: "linked" when the account's skills directory links the skill,
    "selected" when the account selects it but "atc sync" has not linked it yet,
    "conflict" when something atc did not make is where the link is to go, and
    blank when the account does not select it. Below the table, each account's
    skills directory is searched for entries atc does not manage, such as skills
    installed by hand, and they are listed dimmed.

    The accounts' directories must exist. Nothing is changed.
    """
    ctx = load_build_context(conf_d)
    catalog = discover_skills(ctx.sources)
    ui = Ui.default()
    for warning in (*ctx.sources.warnings, *catalog.warnings):
        ui.warning(warning)
    accounts = chosen_accounts(ctx, names)
    plans: list[AccountPlan] = []
    for account in accounts:
        try:
            plans.append(
                plan_account(
                    ctx,
                    account,
                    take_over=False,
                    discard_live=False,
                    lax=False,
                    parts=frozenset({"skills"}),
                    catalog=catalog,
                )
            )
        except ConfigError as exc:
            ui.error(str(exc))
    if len(plans) != len(accounts):
        raise click.exceptions.Exit(exitcodes.CONFIG)

    ui.table(_skills_table(catalog, plans))
    for plan in plans:
        directory = backend_for(plan.account.agent, ctx.home).skills_dir(plan.account)
        entries = _unmanaged(directory, plan.links)
        if entries:
            ui.line("")
            ui.line(f"{plan.account.name}: not managed by atc")
            for entry in entries:
                ui.dim(f"  {entry}")


def _skills_table(catalog: Catalog, plans: Sequence[AccountPlan]) -> Table:
    table = Table(box=box.SIMPLE_HEAD, pad_edge=False, padding=(0, 1, 0, 0))
    table.add_column("Name", no_wrap=True)
    table.add_column("Source", no_wrap=True)
    table.add_column("Description", no_wrap=True, overflow="ellipsis")
    for plan in plans:
        table.add_column(plan.account.name, no_wrap=True)
    for skill in sorted(
        catalog.skills, key=lambda found: (found.name, found.source.name)
    ):
        states = [Text(*_state(plan, skill)) for plan in plans]
        table.add_row(
            Text(skill.name),
            Text(skill.source.name) if skill.source.name else Text("-", style="dim"),
            Text(_shortened(skill.description)),
            *states,
        )
    return table


def _state(plan: AccountPlan, skill: Skill) -> tuple[str, str]:
    """Say what ``plan``'s account has of ``skill``, and the style to show it in."""
    for link in plan.links:
        if link.wanted == skill.path:
            state = _link_state(link)
            return state, _STYLES[state]
    return "", ""


def _link_state(link: LinkPlan) -> _State:
    match link.action:
        case "none":
            return "linked"
        case "conflict":
            return "conflict"
        case _:
            return "selected"


def _shortened(description: str) -> str:
    """Put ``description`` on one line, and cut it to :data:`DESCRIPTION_WIDTH`."""
    text = " ".join(description.split())
    if len(text) <= DESCRIPTION_WIDTH:
        return text
    return text[: DESCRIPTION_WIDTH - 1] + "…"


def _unmanaged(directory: Path, links: Sequence[LinkPlan]) -> list[str]:
    """
    Describe the entries of ``directory`` that atc does not manage, by name.

    Atc manages the links that are current, or that it made and would change or
    remove. A directory is shown with a ``/`` after it, and a link with its
    target.

    Raises:
        ConfigError: ``directory`` cannot be read.

    """
    managed = {link.entry for link in links if link.owned}
    try:
        entries = sorted(directory.iterdir())
    except (FileNotFoundError, NotADirectoryError):
        return []
    except OSError as exc:
        raise ConfigError(f"{directory}: cannot be read: {exc}") from exc
    return [_described(entry) for entry in entries if entry not in managed]


def _described(entry: Path) -> str:
    """Name ``entry``, adding its target if it is a link, or a ``/`` for a directory."""
    try:
        mode = entry.lstat().st_mode
        if stat.S_ISLNK(mode):
            return f"{entry.name} -> {entry.readlink()}"
    except OSError:
        return entry.name
    return f"{entry.name}/" if stat.S_ISDIR(mode) else entry.name
