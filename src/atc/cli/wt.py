"""``atc wt``: add, list and remove the worktrees of the current project."""

import os
from collections.abc import Callable
from typing import cast

import click
import rich_click

from atc.cli.options import (
    current_project,
    load_machine_registry,
    worktrees_table,
)
from atc.cli.run import run
from atc.registry import Project
from atc.ui import Ui
from atc.worktree import list_worktrees, new_worktree, remove_worktree


@click.group(cls=rich_click.RichGroup, name="wt")
def wt() -> None:
    """Add, list and remove the worktrees of the current project."""


@wt.command(cls=rich_click.RichCommand, name="new")
@click.argument("branch", metavar="BRANCH")
@click.option(
    "--from",
    "from_ref",
    metavar="REF",
    help=(
        "The commit, branch or tag a new branch starts from. Without it, the "
        "project's branch-from applies. It plays no part when BRANCH exists."
    ),
)
@click.option(
    "--run",
    "run_session",
    is_flag=True,
    help=(
        "Once the worktree is made, start the agent in it, as atc run does "
        "without options."
    ),
)
@click.pass_context
def new(
    ctx: click.Context, branch: str, from_ref: str | None, run_session: bool
) -> None:
    """
    Add a worktree for BRANCH to the current project.

    The project is the one whose repository the current directory is in, whether
    as the main checkout or as a worktree, and it must name a worktrees
    directory in the registry. The worktree is made in that directory, in a
    subdirectory named after BRANCH: it is lowercased, each space becomes -
    and every character other than a-z, 0-9, _, . and - becomes --, so
    feature/x is feature--x.

    If BRANCH exists, it is checked out in the worktree. Otherwise it is created,
    at REF if given, or else at the project's branch-from, which is main unless
    the registry says otherwise. Git refuses a branch that is checked out
    elsewhere, and a directory that exists already.
    """
    ui = Ui.default()
    project = _current(ui)
    path = new_worktree(project, branch, from_ref)
    ui.line(f"{branch}: worktree added at {path}")
    if run_session:
        os.chdir(path)
        # click types `invoke` as returning Any. `run` returns nothing: it ends
        # in `execute`, which replaces the process.
        invoke = cast("Callable[[click.Command], None]", ctx.invoke)
        invoke(run)


@wt.command(cls=rich_click.RichCommand, name="ls")
def ls() -> None:
    """
    List the worktrees of the current project.

    Shows what git lists for the project's repository, the main checkout first:
    each worktree's path, its branch, which is (detached) when HEAD is not on a
    branch, and the start of its commit. That includes worktrees made without
    atc. Nothing is changed.
    """
    ui = Ui.default()
    trees = list_worktrees(_current(ui).repo)
    if not trees:
        ui.info("the project has no worktrees")
        return
    ui.table(worktrees_table(trees))


@wt.command(cls=rich_click.RichCommand, name="rm")
@click.argument("name", metavar="NAME")
@click.option(
    "--force",
    is_flag=True,
    help=(
        "Remove the worktree even if it has uncommitted changes or untracked "
        "files, a detached HEAD that no branch contains, or an operation in "
        "progress."
    ),
)
def rm(name: str, force: bool) -> None:
    """
    Remove a worktree of the current project.

    NAME is the name of the worktree's directory in the project's worktrees
    directory, such as feature--x. Unless --force is given, it is refused when
    removing it would lose work: git refuses one with uncommitted changes or
    untracked files, and atc one whose HEAD is detached at a commit that no
    branch contains, or where a rebase, merge, cherry-pick, revert or bisect is
    in progress. Files that git ignores, such as build output, are deleted with
    the worktree. The branch is never deleted, and the project's repository is
    never removed.
    """
    ui = Ui.default()
    remove_worktree(_current(ui), name, force=force)
    ui.line(f"{name}: worktree removed; its branch is kept")


def _current(ui: Ui) -> Project:
    """Find the project that the current directory is in."""
    return current_project(load_machine_registry(ui), ui)
