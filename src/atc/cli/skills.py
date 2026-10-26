"""``atc skills``: look at the skills the config sources offer, and choose them."""

import stat
from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self

import click
import rich_click
from rich import box
from rich.table import Table

from atc import exitcodes
from atc.backends import backend_for
from atc.cli.options import chosen_accounts, conf_d_option, load_build_context
from atc.cli.sync import link_status
from atc.context import BuildContext
from atc.errors import ConfigError, PathError
from atc.links import LinkPlan
from atc.registry import Account, set_skills
from atc.skills import Catalog, Skill, discover_skills
from atc.sync import AccountPlan, apply_account, plan_account
from atc.ui import PickChoice, Ui, shown

DESCRIPTION_WIDTH = 60

type _State = Literal["linked", "selected", "conflict"]

_STYLES: dict[_State, str] = {"linked": "green", "selected": "", "conflict": "yellow"}


@click.group(cls=rich_click.RichGroup, name="skills")
def skills() -> None:
    """List the skills the config sources offer, and choose them for accounts."""


take_over_option = click.option(
    "--take-over",
    is_flag=True,
    help=(
        "Replace a link or file that is where a selected skill's link is to go. "
        "A directory is never replaced."
    ),
)


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


@skills.command(cls=rich_click.RichCommand, name="pick")
@click.argument("account")
@take_over_option
def pick(account: str, take_over: bool) -> None:
    """
    Choose the skills of ACCOUNT from a checklist.

    Shows every skill the config sources offer, with the ones the account selects
    ticked. Move with the arrow keys, tick with the space bar and confirm with
    enter. Confirming writes the new selection to machine.yaml, keeping its
    comments, and links and unlinks the account's skills as "atc sync" would,
    without touching its settings or its instructions file. Ctrl-C cancels:
    nothing is changed, and the status is 130.

    Entries in the account's selection that name no skill are dropped. Needs a
    terminal: in a script, use "atc skills add" and "atc skills rm". Exits with
    status 3 when a link is left alone because something atc did not make is in
    its way.
    """
    target = _Target.open(account)
    if not target.catalog.skills:
        raise ConfigError("the config sources have no skills to pick from")
    ticked = {found.path for found in target.named(target.account.skills)}
    answer = target.ui.pick(
        f"Skills for {target.account.name}", skill_choices(target.catalog, ticked)
    )
    if answer is None:
        target.ui.info("cancelled; nothing changed")
        raise click.exceptions.Exit(exitcodes.INTERRUPTED)
    wanted = {skill.path: skill for skill in target.resolve_all(answer)}
    entries: list[str] = []
    for ref in target.account.skills:
        skill = target.skill_of(ref)
        if skill is not None and wanted.pop(skill.path, None) is not None:
            entries.append(ref)
    entries.extend(target.entry(skill) for skill in wanted.values())
    target.write(entries, take_over=take_over)


@skills.command(cls=rich_click.RichCommand, name="add")
@click.argument("account")
@click.argument("names", nargs=-1, required=True, metavar="SKILL...")
@take_over_option
def add(account: str, names: tuple[str, ...], take_over: bool) -> None:
    """
    Add skills to the selection of ACCOUNT, without the checklist.

    Each SKILL is a skill's name, or @source/name when several sources have a
    skill of that name. It is written to machine.yaml, keeping its comments,
    as the bare name unless that is ambiguous, and then linked as "atc sync"
    would. A skill that is selected already is left as it is. An unknown SKILL
    is an error, and nothing is changed.

    Exits with status 3 when a link is left alone because something atc did not
    make is in its way.
    """
    target = _Target.open(account)
    have = {found.path for found in target.named(target.account.skills)}
    entries = list(target.account.skills)
    for skill in target.resolve_all(names):
        if skill.path not in have:
            have.add(skill.path)
            entries.append(target.entry(skill))
    target.write(entries, take_over=take_over)


@skills.command(cls=rich_click.RichCommand, name="rm")
@click.argument("account")
@click.argument("names", nargs=-1, required=True, metavar="SKILL...")
def rm(account: str, names: tuple[str, ...]) -> None:
    """
    Remove skills from the selection of ACCOUNT, without the checklist.

    Each SKILL is a skill's name, or @source/name when several sources have a
    skill of that name, or an entry of the selection as written. It is removed
    from machine.yaml, keeping its comments, and its link is removed as "atc
    sync" would. A skill that is not selected is left as it is. A SKILL that is
    neither a skill nor selected is an error, and nothing is changed.
    """
    target = _Target.open(account)
    doomed: set[Path] = set()
    for name in names:
        try:
            doomed.add(target.resolve(name).path)
        except PathError:
            if name not in target.account.skills:
                raise
    entries: list[str] = []
    for ref in target.account.skills:
        skill = target.skill_of(ref)
        if ref not in names and (skill is None or skill.path not in doomed):
            entries.append(ref)
    target.write(entries, take_over=False)


@dataclass(frozen=True, slots=True)
class _Target:
    """What a command that changes an account's selection works on."""

    ctx: BuildContext
    catalog: Catalog
    account: Account
    ui: Ui

    @classmethod
    def open(cls, name: str) -> Self:
        """
        Load the configuration, and find the account ``name``.

        Raises:
            ConfigError: the configuration cannot be loaded, or has no such
                account.

        """
        ctx = load_build_context(None)
        catalog = discover_skills(ctx.sources)
        ui = Ui.default()
        for warning in (*ctx.sources.warnings, *catalog.warnings):
            ui.warning(warning)
        return cls(ctx, catalog, ctx.registry.account(name), ui)

    def resolve(self, ref: str) -> Skill:
        """
        Find the skill that ``ref`` names.

        Raises:
            PathError: ``ref`` names no skill, or ambiguously.

        """
        return self.catalog.resolve(ref, self.ctx.sources)

    def resolve_all(self, refs: Sequence[str]) -> list[Skill]:
        """Find the skill that each of ``refs`` names, as :meth:`resolve` does."""
        return [self.resolve(ref) for ref in refs]

    def skill_of(self, ref: str) -> Skill | None:
        """Find the skill that the registry entry ``ref`` names, or ``None``."""
        try:
            return self.resolve(ref)
        except PathError:
            return None

    def named(self, refs: Sequence[str]) -> list[Skill]:
        """Find the skills that the entries ``refs`` name, leaving out the rest."""
        found = (self.skill_of(ref) for ref in refs)
        return [skill for skill in found if skill is not None]

    def entry(self, skill: Skill) -> str:
        """Write ``skill`` as an entry: its name, with its source if ambiguous."""
        return _skill_entry(self.catalog, skill)

    def write(self, entries: Sequence[str], *, take_over: bool) -> None:
        """
        Make ``entries`` the account's selection, and link the skills as it says.

        The links are planned for the new selection first, so that a selection
        that cannot be linked is not written. It prints what changed, and exits
        with status 3 when a link is left alone.
        """
        account, name = self.account, self.account.name
        updated = replace(account, skills=tuple(entries))
        accounts = {**self.ctx.registry.accounts, name: updated}
        trial = replace(
            self.ctx, registry=replace(self.ctx.registry, accounts=accounts)
        )
        plan = plan_account(
            trial,
            updated,
            take_over=take_over,
            discard_live=False,
            lax=False,
            parts=frozenset({"skills"}),
            catalog=self.catalog,
        )
        changed = updated.skills != account.skills
        if changed:
            path = self.ctx.registry.path
            _ = set_skills(path, self.ctx.home, name, entries)
            self.ui.line(f"{name}: skills written to {path}")
        apply_account(plan, self.ctx, datetime.now(UTC))
        for link in plan.links:
            if link.pending or link.blocked:
                status = link_status(link, dry=False)
                self.ui.line(f"{name}: {link.describe()} {status}")
        if not changed and not plan.pending and not plan.blocked:
            self.ui.line(f"{name}: skills unchanged")
        if plan.blocked:
            raise click.exceptions.Exit(exitcodes.CHECK)


def _skill_entry(catalog: Catalog, skill: Skill) -> str:
    """Write ``skill`` as a registry entry: its name, with its source if ambiguous."""
    homonyms = sum(1 for other in catalog.skills if other.name == skill.name)
    return skill.reference if homonyms > 1 else skill.name


def skill_choices(catalog: Catalog, ticked: Collection[Path]) -> list[PickChoice]:
    """
    Make the picker's lines for the skills of ``catalog``, sorted by name.

    Each gives back the skill's registry entry, and starts ticked when its path
    is one of ``ticked``.
    """
    ordered = sorted(catalog.skills, key=lambda found: (found.name, found.source.name))
    return [
        PickChoice(_skill_entry(catalog, skill), label, skill.path in ticked)
        for skill, label in zip(ordered, _labels(ordered), strict=True)
    ]


def _labels(ordered: Sequence[Skill]) -> list[str]:
    """Write a picker line for each skill: ``name  source  description``, in columns."""
    name_width = max(len(skill.name) for skill in ordered)
    source_width = max(len(skill.source.name or "-") for skill in ordered)
    labels: list[str] = []
    for skill in ordered:
        name = f"{skill.name:<{name_width}}"
        source = f"{skill.source.name or '-':<{source_width}}"
        labels.append(f"{name}  {source}  {_shortened(skill.description)}".rstrip())
    return labels


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
        states = [shown(*_state(plan, skill)) for plan in plans]
        table.add_row(
            shown(skill.name),
            shown(skill.source.name) if skill.source.name else shown("-", style="dim"),
            shown(_shortened(skill.description)),
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
