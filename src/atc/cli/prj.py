"""``atc prj``: see and change the projects of the machine registry."""

from pathlib import Path

import click
import rich_click
from rich import box
from rich.table import Table
from rich.text import Text

from atc import git
from atc.cli.options import (
    cancelled,
    current_dirs,
    current_project,
    load_machine_registry,
    load_sources,
    registered_project,
    worktrees_table,
)
from atc.errors import ConfigError, GitError
from atc.jsonval import JsonObject, JsonValue
from atc.refs import resolve_registry
from atc.registry import (
    DEFAULT_BRANCH_FROM,
    REGISTRY_FILE,
    Project,
    ProjectRemoval,
    Registry,
    add_project,
)
from atc.ui import Ui, need_terminal, shown
from atc.worktree import list_worktrees

LABEL_WIDTH = 13


@click.group(cls=rich_click.RichGroup, name="prj")
def prj() -> None:
    """List, show, add and remove the projects of the machine registry."""


@prj.command(cls=rich_click.RichCommand, name="ls")
def ls() -> None:
    """
    List the registered projects.

    Shows one row per project in the machine registry, machine.yaml in atc's
    configuration directory: its name, repository, account and worktrees
    directory, which is - when it has none. The account is the one the project
    uses by default: its own, or else the registry's default account. Nothing is
    changed.
    """
    ui = Ui.default()
    registry = load_machine_registry(ui)
    if not registry.projects:
        ui.info("no projects are registered")
        return
    table = Table(box=box.SIMPLE_HEAD, pad_edge=False, padding=(0, 1, 0, 0))
    table.add_column("Name", no_wrap=True)
    table.add_column("Repository", overflow="fold", min_width=16)
    table.add_column("Account", no_wrap=True)
    table.add_column("Worktrees", overflow="fold", min_width=16)
    for project in registry.projects.values():
        table.add_row(
            shown(project.name),
            shown(str(project.repo)),
            shown(project.account or registry.default_account),
            shown(str(project.worktrees)) if project.worktrees else _dash(),
        )
    ui.table(table)


@prj.command(cls=rich_click.RichCommand, name="show")
@click.argument("name", required=False, metavar="[NAME]")
def show(name: str | None) -> None:
    """
    Show one project, with the worktrees git has for it.

    NAME is a project of the machine registry. Without it, the project is the
    one whose repository the current directory is in, whether as the main
    checkout or as a worktree, as "atc run" finds it. Shows the project's
    entry in the registry, with the defaults filled in, and then a table of its
    worktrees: path, branch, and the start of the commit. If git cannot list
    the worktrees, a warning says why and the rest is still shown. Nothing is
    changed.
    """
    ui = Ui.default()
    registry = load_machine_registry(ui)
    found = (
        registry.project(name)
        if name is not None
        else current_project(registry, ui, advice="give the NAME of one")
    )
    for label, value in _fields(registry, found):
        ui.line(f"{label:<{LABEL_WIDTH}}{value}")
    try:
        trees = list_worktrees(found.repo)
    except GitError as exc:
        ui.warning(f"cannot list the worktrees of '{found.name}': {exc}")
        return
    ui.line("")
    ui.table(worktrees_table(trees))


@prj.command(cls=rich_click.RichCommand, name="add")
@click.argument("name", metavar="NAME")
@click.argument(
    "repo", required=False, metavar="[REPO]", type=click.Path(file_okay=False)
)
@click.option(
    "--worktrees",
    "worktrees",
    metavar="DIR",
    type=click.Path(file_okay=False),
    help="The directory the project's worktrees are made in.",
)
@click.option(
    "--from",
    "branch_from",
    metavar="BRANCH",
    help=(
        "The branch new branches start from, which must exist in REPO. Without "
        f"it, {DEFAULT_BRANCH_FROM} applies, and must exist."
    ),
)
@click.option(
    "-a",
    "--account",
    "account_names",
    metavar="ACCOUNT",
    multiple=True,
    help=(
        "An account allowed to work on the project, which must exist; give it "
        "once for each. The first is the one the project uses by default. "
        "Without it, every account is allowed, and the registry's default "
        "account applies."
    ),
)
def add(
    name: str,
    repo: str | None,
    worktrees: str | None,
    branch_from: str | None,
    account_names: tuple[str, ...],
) -> None:
    """
    Add a project to the machine registry.

    NAME is the project's name, the one its settings go by: projects/NAME.json
    in a config source. REPO is a checkout of its repository, whether the main
    one, a worktree or a bare repository, and defaults to the current directory.
    What is registered is the repository's main worktree, whichever of its
    worktrees REPO is, or for a bare repository the repository itself, so that
    removing a worktree never leaves the project without its repository. When
    the git directory is kept apart from the checkout, as a submodule's is, the
    top level of REPO is registered instead. REPO must be a git repository that
    no project has yet, and the branch that new branches start from must exist
    in it.

    Each ACCOUNT is written to the project's accounts, the list of those
    allowed to work on it, and the first as its account, the one it uses by
    default. The list is written even for one, so more can be added to it.

    The entry is added after the others in machine.yaml, with paths made
    absolute, and the rest of the file stays as it was, comments included.
    Nothing is written if the result would not be a valid registry, for example
    because NAME is registered already or an ACCOUNT does not exist.

    If no config source has projects/NAME.json, a warning says that the project
    has no settings of its own until one does.
    """
    repeated = sorted({a for a in account_names if account_names.count(a) > 1})
    if repeated:
        message = f"given more than once: {', '.join(repeated)}"
        raise click.BadParameter(message, param_hint="'-a' / '--account'")
    ui = Ui.default()
    registry = load_machine_registry(ui)
    checkout = _checkout(repo)
    existing = registered_project(registry, checkout, ui)
    if existing is not None:
        raise ConfigError(
            f"{checkout}: already registered as project '{existing.name}'"
        )
    _check_branch(checkout, DEFAULT_BRANCH_FROM if branch_from is None else branch_from)
    fields: JsonObject = {"repo": str(checkout)}
    if worktrees is not None:
        fields["worktrees"] = str(Path(worktrees).expanduser().resolve())
    if branch_from is not None:
        fields["branch-from"] = branch_from
    if account_names:
        fields["account"] = account_names[0]
        fields["accounts"] = list[JsonValue](account_names)
    updated = add_project(registry.path, Path.home(), name, fields)
    ui.line(f"{name}: added to {updated.path}")
    warning = _unit_warning(name)
    if warning is not None:
        ui.warning(warning)


@prj.command(cls=rich_click.RichCommand, name="rm")
@click.argument("name", metavar="NAME")
def rm(name: str) -> None:
    """
    Remove a project from the machine registry.

    Deletes the entry of the project NAME from machine.yaml, its vars included,
    and the comment lines directly above it at its indentation, with no blank
    line between. The rest of the file stays as it was, other comments and blank
    lines included. Nothing else is touched: not the repository, not its
    worktrees and not its unit in the config sources. An unknown NAME is an
    error that lists the projects there are.

    It says what goes and what stays, and asks first, no being the answer that
    enter alone gives. Needs a terminal, to ask. Ctrl-C cancels: nothing is
    changed, and the status is 130. If machine.yaml changes while it waits for
    the answer, nothing is written.
    """
    ui = Ui.default()
    path = current_dirs().config_dir / REGISTRY_FILE
    removal = ProjectRemoval.plan(path, Path.home(), name)
    for warning in removal.registry.warnings:
        ui.warning(warning)
    need_terminal("atc prj rm")
    project = removal.project
    variables = f", with its vars {', '.join(project.vars)}" if project.vars else ""
    ui.info(f"this removes project '{name}' ({project.repo}) from {path}{variables}")
    ui.info(
        f"the repository, its worktrees and its unit {project.unit} stay as they are"
    )
    answer = ui.confirm(f"Remove project {name}?", default=False)
    if answer is None:
        cancelled(ui)
    if not answer:
        ui.info("nothing changed")
        return
    updated = removal.write()
    ui.line(f"{name}: removed from {updated.path}")


def _fields(registry: Registry, project: Project) -> list[tuple[str, str]]:
    """Describe ``project`` as labelled lines, with the defaults filled in."""
    account = project.account or f"{registry.default_account} (the default account)"
    accounts = (
        "every account" if project.accounts is None else ", ".join(project.accounts)
    )
    unit = f"{project.unit} (the default)" if project.unit_is_default else project.unit
    variables = ", ".join(f"{key}={value}" for key, value in project.vars.items())
    return [
        ("name", project.name),
        ("repository", str(project.repo)),
        ("account", account),
        ("accounts", accounts),
        ("unit", unit),
        ("worktrees", str(project.worktrees) if project.worktrees else "-"),
        ("branch from", project.branch_from),
        ("snapshot", project.snapshot or "-"),
        ("vars", variables or "-"),
    ]


def _dash() -> Text:
    return shown("-", style="dim")


def _checkout(repo: str | None) -> Path:
    """
    Find what to register: the main worktree of the repository of ``repo``.

    That is the repository ``repo``, else the current directory, is in, from
    whichever of its worktrees. Its main worktree is the directory that holds
    its git directory, or for a bare repository the git directory itself. When
    the git directory is kept apart from the checkout, as a submodule's is, the
    directory above it is no checkout, and the top level of ``repo`` is taken.

    Raises:
        ConfigError: it is not a git repository.
        GitError: git cannot inspect it.

    """
    if repo is None:
        start = Path.cwd()
        common = git.common_dir(start)
        if common is None:
            raise ConfigError(
                "the current directory is not in a git repository; name the REPO"
            )
    else:
        start = Path(repo).expanduser().resolve()
        if not start.is_dir():
            raise ConfigError(f"{start}: not a directory")
        common = git.common_dir(start)
        if common is None:
            raise ConfigError(f"{start}: not a git repository")
    bare = git.run_git(
        ["rev-parse", "--is-bare-repository"], cwd=common, env={"GIT_DIR": str(common)}
    )
    if bare == "true":
        return common
    if git.common_dir(common.parent) == common:
        return common.parent
    return Path(git.run_git(["rev-parse", "--show-toplevel"], cwd=start))


def _check_branch(checkout: Path, branch: str) -> None:
    """
    Check that the repository of ``checkout`` has a branch called ``branch``.

    A revision that merely reaches a commit, such as ``main~1``, is not one.

    Raises:
        ConfigError: it has none.

    """
    found = git.git_or_none(
        ["show-ref", "--verify", f"refs/heads/{branch}"], cwd=checkout
    )
    if found is None:
        message = (
            f"{checkout}: no branch named '{branch}'; name the one new branches "
            "start from with --from"
        )
        raise ConfigError(message)


def _unit_warning(name: str) -> str | None:
    """Say what is wrong with the project ``name`` having no unit, if it has none."""
    reference = f"projects/{name}.json"
    try:
        found = resolve_registry(load_sources(None), reference)
    except ConfigError as exc:
        return f"cannot look for {reference} in the config sources: {exc}"
    if found is not None:
        return None
    return f"no config source has {reference}, so project '{name}' has no project layer"
