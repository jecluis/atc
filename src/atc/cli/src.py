"""``atc src``: look at the config sources atc reads."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

import click
import rich_click
from rich import box
from rich.table import Table

from atc import exitcodes
from atc.cli.options import (
    conf_d_option,
    current_dirs,
    load_build_context,
    load_sources,
)
from atc.git import git_or_none
from atc.pull import pull
from atc.registry import REGISTRY_FILE
from atc.skills import Catalog, discover_skills
from atc.sources import SOURCE_DIRS, Source, Sources
from atc.ui import Ui, shown
from atc.validate import Validation, validate_config, validate_sources


@click.group(cls=rich_click.RichGroup, name="src")
def src() -> None:
    """Work with the config sources atc reads."""


@src.command(cls=rich_click.RichCommand, name="ls")
@conf_d_option
def ls(conf_d: Path | None) -> None:
    """
    List the config sources.

    Shows each source's name, path and layout, how many units, asset files and
    skills it has, and, for git clones, the branch, the commit, whether conf,
    assets or skills have uncommitted changes, and how many commits it is ahead
    of and behind its upstream at the last fetch.
    """
    found = load_sources(conf_d)
    catalog = discover_skills(found)
    ui = Ui.default()
    for warning in (*found.warnings, *catalog.warnings):
        ui.warning(warning)
    ui.table(_sources_table(found, catalog))


@src.command(cls=rich_click.RichCommand, name="check")
@conf_d_option
def check(conf_d: Path | None) -> None:
    """
    Check every unit in every config source.

    Loads each unit and follows its imports, including those that lead into
    other sources, and reports every problem found rather than only the first:
    malformed units, unknown settings keys, missing or circular imports, and
    values of conflicting types. When the machine registry (machine.yaml in
    atc's configuration directory) exists, it also builds the settings of every
    account, which finds accounts whose unit is missing and units that set a
    key their layer does not allow. It also finds accounts that select a skill,
    or ask for an instructions file, that does not exist. Prints a summary line
    when all is well and exits with status 1 otherwise.
    """
    ui = Ui.default()
    found, validate = _checker(conf_d, ui)
    result = validate(found)
    for warning in result.warnings:
        ui.warning(warning)
    for error in result.errors:
        ui.error(error)
    if result.errors:
        raise click.exceptions.Exit(exitcodes.CONFIG)
    ui.info(f"ok: {result.units} units in {len(found.items)} sources")


@src.command(cls=rich_click.RichCommand, name="pull")
@click.argument("names", nargs=-1, metavar="[NAME]...")
def pull_command(names: tuple[str, ...]) -> None:
    """
    Update config sources that are git clones, after checking what they get.

    Updates the sources called NAME, or all of them. For each one it refuses if
    conf, assets or skills have uncommitted changes, fetches, and stops unless
    the upstream can be reached by fast-forward. It then runs the checks of
    "atc src check" on the fetched commit, and only if they pass fast-forwards
    to it. Afterwards it removes group and world write from conf, assets and
    skills, as git applies the umask to what it writes.

    Prints one line per source. A source that fails or is refused is left as it
    was, its problems are shown, and the exit status is 1. Sources that are not
    git clones, or have no upstream, are skipped.
    """
    ui = Ui.default()
    found, validate = _checker(None, ui)
    results = pull(found, names, validate)
    problems = False
    shown: set[str] = set()
    for result in results:
        ui.line(f"{result.name or found.conf_d}: {result.status} ({result.detail})")
        problems = problems or result.status in {"failed", "refused"}
        if result.validation is None:
            continue
        for warning in result.validation.warnings:
            if warning not in shown:
                shown.add(warning)
                ui.warning(warning)
        for error in result.validation.errors:
            ui.error(error)
    if problems:
        raise click.exceptions.Exit(exitcodes.CONFIG)


def _checker(
    conf_d: Path | None, ui: Ui
) -> tuple[Sources, Callable[[Sources], Validation]]:
    """
    Load the sources, and the check that applies to them and to a changed copy.

    The check covers the accounts and projects when the registry exists.
    Otherwise it covers the sources only, and a warning says so.
    """
    registry_file = current_dirs().config_dir / REGISTRY_FILE
    if registry_file.exists():
        ctx = load_build_context(conf_d)

        def validate_all(candidate: Sources) -> Validation:
            return validate_config(replace(ctx, sources=candidate), lax=False)

        return ctx.sources, validate_all
    found = load_sources(conf_d)
    ui.warning(f"{registry_file}: not found, so the accounts were not checked")

    def validate_units(candidate: Sources) -> Validation:
        return validate_sources(candidate, lax=False)

    return found, validate_units


@dataclass(frozen=True, slots=True)
class _Clone:
    """What git says about a source. A field is empty when git could not tell."""

    branch: str
    commit: str
    dirty: bool
    ahead_behind: str


def _sources_table(found: Sources, catalog: Catalog) -> Table:
    table = Table(box=box.SIMPLE_HEAD, pad_edge=False, padding=(0, 1, 0, 0))
    table.add_column("Name", no_wrap=True)
    # In a narrow terminal the path folds, down to a width that is still
    # readable, and the last column is cropped rather than the path vanishing.
    table.add_column("Path", overflow="fold", min_width=16)
    table.add_column("Layout", no_wrap=True)
    table.add_column("Units", justify="right", no_wrap=True)
    table.add_column("Assets", justify="right", no_wrap=True)
    table.add_column("Skills", justify="right", no_wrap=True)
    table.add_column("Branch", no_wrap=True)
    table.add_column("Commit", no_wrap=True)
    table.add_column("Changes", no_wrap=True)
    table.add_column("Ahead/Behind", no_wrap=True)
    for source in found.items:
        clone = _clone(source)
        table.add_row(
            shown(source.name) if source.name else shown("-", style="dim"),
            shown(str(source.root)),
            shown(found.layout),
            shown(str(_count(source.conf, "*.json"))),
            shown(str(_count(source.assets, "*"))),
            shown(str(sum(1 for skill in catalog.skills if skill.source == source))),
            shown(clone.branch if clone else ""),
            shown(clone.commit if clone else ""),
            shown("dirty", style="yellow") if clone and clone.dirty else shown(""),
            shown(clone.ahead_behind if clone else ""),
        )
    return table


def _count(directory: Path, pattern: str) -> int:
    """Count the files under ``directory`` that match ``pattern``."""
    return sum(1 for path in directory.rglob(pattern) if path.is_file())


def _clone(source: Source) -> _Clone | None:
    """Describe ``source`` as a git clone, or give ``None`` if it is not one."""
    if not (source.root / ".git").exists():
        return None
    root = source.root
    branch = git_or_none(["branch", "--show-current"], cwd=root)
    changes = git_or_none(["status", "--porcelain", "--", *SOURCE_DIRS], cwd=root)
    counts = git_or_none(
        ["rev-list", "--left-right", "--count", "HEAD...@{u}"], cwd=root
    )
    return _Clone(
        branch="" if branch is None else branch or "(detached)",
        commit=git_or_none(["rev-parse", "--short", "HEAD"], cwd=root) or "",
        dirty=bool(changes),
        ahead_behind="/".join(counts.split()) if counts else "",
    )
