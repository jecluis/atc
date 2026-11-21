"""Where a project with its own configuration directories keeps them."""

from dataclasses import replace
from pathlib import Path

from atc.paths import Dirs
from atc.registry import Account, Project, Registry, own_config_projects

PROJECTS = "projects"
"""The directory, in atc's state and data directories, that holds the projects'."""
COMMON = "_common"
"""
The directory of a project that its accounts share. No account can be called
this, since an account's name starts with a letter or a digit.
"""
SHARED = ("plugins",)
"""The entries of a configuration directory that link into the shared one."""


def project_root(dirs: Dirs, project: Project) -> Path:
    """Give the directory that holds the configuration directories of ``project``."""
    return dirs.state_dir / PROJECTS / project.name


def config_dir(dirs: Dirs, account: Account, project: Project) -> Path:
    """Give the configuration directory of ``project`` for ``account``."""
    return project_root(dirs, project) / account.name


def common_dir(dirs: Dirs, project: Project) -> Path:
    """Give the directory that the accounts of ``project`` share."""
    return project_root(dirs, project) / COMMON


def memory_dir(dirs: Dirs, project: Project) -> Path:
    """
    Give the auto memory directory that every account of ``project`` uses.

    It is in atc's data directory, not its state directory, since sessions write
    it with Claude Code's file tools.
    """
    return dirs.data_dir / PROJECTS / project.name / "memory"


def config_dir_account(dirs: Dirs, account: Account, project: Project) -> Account:
    """Give ``account`` as it is in the configuration directory of ``project``."""
    return replace(account, dir=config_dir(dirs, account, project))


def config_dir_link(dirs: Dirs, account: Account, project: Project) -> Path | None:
    """
    Find a symbolic link on the way to the configuration directory of ``project``.

    That covers the directories the project's accounts share, and those its auto
    memory directory is in. atc makes and writes them only as directories, so
    that what it writes stays where it says. ``None`` means none of them is a
    link.
    """
    root = dirs.state_dir / PROJECTS
    common = common_dir(dirs, project)
    memory = memory_dir(dirs, project)
    candidates = (
        root,
        root / project.name,
        config_dir(dirs, account, project),
        common,
        *(common / name for name in SHARED),
        memory.parent.parent,
        memory.parent,
        memory,
    )
    return next((path for path in candidates if path.is_symlink()), None)


def account_dirs(dirs: Dirs, registry: Registry, account: Account) -> list[Account]:
    """
    Give ``account`` in each of its directories that exist, its own first.

    The others are its configuration directories of the projects that have
    their own, in the order of the file, then those that the registry no
    longer gives it, which still hold its login, by project name.
    """
    found = [account]
    for project in own_config_projects(registry, account.name):
        there = config_dir_account(dirs, account, project)
        if there.dir.is_dir():
            found.append(there)
    known = {there.dir for there in found}
    root = dirs.state_dir / PROJECTS
    if root.is_dir() and not root.is_symlink():
        for entry in sorted(root.iterdir()):
            path = entry / account.name
            if not entry.is_symlink() and path not in known and path.is_dir():
                found.append(replace(account, dir=path))
    return found
