"""``atc src``: look at the config sources atc reads."""

from dataclasses import dataclass
from pathlib import Path

import click
import rich_click
from rich import box
from rich.table import Table
from rich.text import Text

from atc import exitcodes
from atc.cli.options import conf_d_option, load_sources
from atc.git import git_or_none
from atc.sources import SOURCE_DIRS, Source, Sources
from atc.ui import Ui
from atc.validate import validate_sources


@click.group(cls=rich_click.RichGroup, name="src")
def src() -> None:
    """Work with the config sources atc reads."""


@src.command(cls=rich_click.RichCommand, name="ls")
@conf_d_option
def ls(conf_d: Path | None) -> None:
    """
    List the config sources.

    Shows each source's name, path and layout, how many units and asset files it
    has, and, for git clones, the branch, the commit, whether conf, assets or
    skills have uncommitted changes, and how many commits it is ahead of and
    behind its upstream at the last fetch.
    """
    found = load_sources(conf_d)
    ui = Ui.default()
    for warning in found.warnings:
        ui.warning(warning)
    ui.table(_sources_table(found))


@src.command(cls=rich_click.RichCommand, name="check")
@conf_d_option
def check(conf_d: Path | None) -> None:
    """
    Check every unit in every config source.

    Loads each unit and follows its imports, including those that lead into
    other sources, and reports every problem found rather than only the first:
    malformed units, unknown settings keys, missing or circular imports, and
    values of conflicting types. Prints a summary line when all is well and
    exits with status 1 otherwise.
    """
    found = load_sources(conf_d)
    result = validate_sources(found, lax=False)
    ui = Ui.default()
    for warning in result.warnings:
        ui.warning(warning)
    for error in result.errors:
        ui.error(error)
    if result.errors:
        raise click.exceptions.Exit(exitcodes.CONFIG)
    ui.info(f"ok: {result.units} units in {len(found.items)} sources")


@dataclass(frozen=True, slots=True)
class _Clone:
    """What git says about a source. A field is empty when git could not tell."""

    branch: str
    commit: str
    dirty: bool
    ahead_behind: str


def _sources_table(found: Sources) -> Table:
    table = Table(box=box.SIMPLE_HEAD, pad_edge=False, padding=(0, 1, 0, 0))
    table.add_column("Name", no_wrap=True)
    # In a narrow terminal the path folds, down to a width that is still
    # readable, and the last column is cropped rather than the path vanishing.
    table.add_column("Path", overflow="fold", min_width=16)
    table.add_column("Layout", no_wrap=True)
    table.add_column("Units", justify="right", no_wrap=True)
    table.add_column("Assets", justify="right", no_wrap=True)
    table.add_column("Branch", no_wrap=True)
    table.add_column("Commit", no_wrap=True)
    table.add_column("Changes", no_wrap=True)
    table.add_column("Ahead/Behind", no_wrap=True)
    for source in found.items:
        clone = _clone(source)
        table.add_row(
            Text(source.name) if source.name else Text("-", style="dim"),
            Text(str(source.root)),
            Text(found.layout),
            Text(str(_count(source.conf, "*.json"))),
            Text(str(_count(source.assets, "*"))),
            Text(clone.branch if clone else ""),
            Text(clone.commit if clone else ""),
            Text("dirty", style="yellow") if clone and clone.dirty else Text(),
            Text(clone.ahead_behind if clone else ""),
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
