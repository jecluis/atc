"""What the commands that read config sources have in common."""

import os
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

import click
from rich import box
from rich.table import Table

from atc import exitcodes, git
from atc.context import BuildContext, load_context
from atc.launch import find_project
from atc.paths import Dirs, dirs_from_env
from atc.registry import REGISTRY_FILE, Account, Project, Registry, load_registry
from atc.sources import Sources, discover
from atc.ui import Ui, shown
from atc.worktree import Worktree

SHORT_HEAD = 7
"""How many characters of a commit are shown."""


def _directory(
    _ctx: click.Context, _param: click.Parameter, value: str | None
) -> Path | None:
    if value is not None and not value:
        raise click.UsageError("--conf-d requires a directory")
    return None if value is None else Path(value)


HELP_OPTIONS = {"help_option_names": ["-h", "--help"]}
"""The context settings that make ``-h`` mean ``--help``."""


conf_d_option = click.option(
    "--conf-d",
    "conf_d",
    metavar="DIR",
    type=click.Path(file_okay=False),
    callback=_directory,
    help=(
        "Read config sources from DIR instead of the default, which is the "
        "conf.d directory under $XDG_CONFIG_HOME/atc (~/.config/atc when unset)."
    ),
)
"""The ``--conf-d DIR`` option, passed to the command as ``conf_d: Path | None``."""


lax_option = click.option(
    "--lax",
    is_flag=True,
    help="Report settings keys Claude Code does not define as warnings, not errors.",
)
"""The ``--lax`` flag, passed to the command as ``lax: bool``."""


def current_dirs() -> Dirs:
    """Work out the directories atc uses from this process's environment."""
    return dirs_from_env(os.environ, Path.home(), os.getuid())


def load_sources(conf_d: Path | None) -> Sources:
    """Discover the sources in ``conf_d``, or in the default directory if ``None``."""
    directory = conf_d if conf_d is not None else current_dirs().conf_d
    return discover(directory, uid=os.getuid())


def load_build_context(conf_d: Path | None) -> BuildContext:
    """Load the registry and the sources in ``conf_d``, or the default directory."""
    return load_context(
        env=os.environ, home=Path.home(), uid=os.getuid(), conf_d=conf_d
    )


def chosen_accounts(ctx: BuildContext, names: tuple[str, ...]) -> list[Account]:
    """Find the accounts called ``names``, or every account when there are none."""
    if not names:
        return list(ctx.registry.accounts.values())
    return [ctx.registry.account(name) for name in dict.fromkeys(names)]


def cancelled(ui: Ui) -> NoReturn:
    """Say that a question was cancelled and nothing changed, and exit with 130."""
    ui.info("cancelled; nothing changed")
    raise click.exceptions.Exit(exitcodes.INTERRUPTED)


def load_machine_registry(ui: Ui) -> Registry:
    """Load the machine registry, and report its warnings."""
    registry = load_registry(current_dirs().config_dir / REGISTRY_FILE, Path.home())
    for warning in registry.warnings:
        ui.warning(warning)
    return registry


def registered_project(registry: Registry, directory: Path, ui: Ui) -> Project | None:
    """
    Find the project whose repository ``directory`` is in, if there is one.

    Raises:
        RegistryError: several projects are that repository.

    """
    return find_project(
        registry,
        git.common_dir(directory),
        git_common_dir=git.common_dir,
        warn=ui.warning,
    )


def current_project(
    registry: Registry, ui: Ui, *, advice: str | None = None
) -> Project:
    """
    Find the project that the current directory is in.

    ``advice`` is added to the error, to say what else can be done.

    Raises:
        click.UsageError: it is in none.

    """
    found = registered_project(registry, Path.cwd(), ui)
    if found is None:
        message = "the current directory is not in a registered project"
        raise click.UsageError(message if advice is None else f"{message}; {advice}")
    return found


def worktrees_table(trees: Sequence[Worktree]) -> Table:
    """Lay ``trees`` out as a table: path, branch and the start of the commit."""
    table = Table(box=box.SIMPLE_HEAD, pad_edge=False, padding=(0, 1, 0, 0))
    table.add_column("Worktree", overflow="fold", min_width=16)
    table.add_column("Branch", no_wrap=True)
    table.add_column("Commit", no_wrap=True)
    for tree in trees:
        table.add_row(
            shown(str(tree.path)),
            shown(tree.branch) if tree.branch else shown("(detached)", style="dim"),
            shown(tree.head[:SHORT_HEAD]),
        )
    return table
