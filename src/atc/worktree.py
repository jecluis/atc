"""The worktrees of a repository: listing them, adding them and removing them."""

import re
from dataclasses import dataclass
from pathlib import Path

from atc.errors import ConfigError
from atc.git import git_or_none, run_git
from atc.registry import Project

_BRANCH_PREFIX = "refs/heads/"
_OUTSIDE_SLUG = re.compile(r"[^a-z0-9_.-]")
_IN_PROGRESS = (
    ("rebase-merge", "a rebase"),
    ("rebase-apply", "a rebase"),
    ("MERGE_HEAD", "a merge"),
    ("CHERRY_PICK_HEAD", "a cherry-pick"),
    ("REVERT_HEAD", "a revert"),
    ("BISECT_LOG", "a bisect"),
)
"""What a worktree's git directory holds while an operation is in progress there."""


@dataclass(frozen=True, slots=True)
class Worktree:
    """
    One checked-out worktree of a repository.

    ``path`` is where it is, as git prints it. ``branch`` is the branch checked
    out in it, without ``refs/heads/``, and ``None`` means ``HEAD`` is detached.
    ``head`` is the commit ``HEAD`` is at.
    """

    path: Path
    branch: str | None
    head: str


def list_worktrees(repo: Path) -> list[Worktree]:
    """
    List the worktrees of the repository that ``repo`` is in, main one first.

    The repository itself is a worktree unless it is bare, and a bare
    repository is left out.

    Raises:
        GitError: git could not be run in ``repo``, or failed.

    """
    output = run_git(["worktree", "list", "--porcelain"], cwd=repo)
    found: list[Worktree] = []
    for block in output.split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            name, _, value = line.partition(" ")
            fields[name] = value
        if "worktree" not in fields or "bare" in fields:
            continue
        branch = fields.get("branch")
        found.append(
            Worktree(
                path=Path(fields["worktree"]),
                branch=None if branch is None else branch.removeprefix(_BRANCH_PREFIX),
                head=fields.get("HEAD", ""),
            )
        )
    return found


def slug(branch: str) -> str:
    """
    Turn a branch name into the name of a directory.

    It is lowercased, each space becomes ``-`` and every character outside
    ``a-z``, ``0-9``, ``_``, ``.`` and ``-`` becomes ``--``, so ``feature/x`` is
    ``feature--x``. A ``/`` never survives, so the result is one path component.
    """
    return _OUTSIDE_SLUG.sub("--", branch.lower().replace(" ", "-"))


def new_worktree(project: Project, branch: str, from_ref: str | None) -> Path:
    """
    Add a worktree for ``branch`` to ``project`` and return its path.

    It is made at ``slug(branch)`` in the project's worktrees directory. If the
    branch exists it is checked out there, and ``from_ref`` plays no part.
    Otherwise the branch is created at ``from_ref``, or at the project's
    ``branch_from`` when that is ``None``.

    Raises:
        ConfigError: the project has no worktrees directory.
        GitError: git refused, for example because the directory exists or the
            branch is checked out in another worktree.

    """
    path = _worktrees_dir(project) / slug(branch)
    exists = git_or_none(
        ["show-ref", "--verify", "--quiet", f"{_BRANCH_PREFIX}{branch}"],
        cwd=project.repo,
    )
    if exists is not None:
        args = ["worktree", "add", str(path), branch]
    else:
        start = project.branch_from if from_ref is None else from_ref
        args = ["worktree", "add", "-b", branch, str(path), start]
    _ = run_git(args, cwd=project.repo)
    return path


def remove_worktree(project: Project, name: str, *, force: bool) -> None:
    """
    Remove the worktree ``name`` from ``project``, and keep its branch.

    ``name`` is a directory in the project's worktrees directory, and never the
    project's repository. Unless ``force``, a worktree whose removal would lose
    commits is refused: one where a rebase, merge, cherry-pick, revert or
    bisect is in progress, or whose ``HEAD`` is detached at a commit no branch
    contains. Git refuses one with uncommitted changes or untracked files. The
    files git ignores are deleted with it.

    Raises:
        ConfigError: the project has no worktrees directory, ``name`` is not
            the name of a directory in it, it is the project's repository, or
            removing it would lose commits.
        GitError: git refused, for example because ``name`` is not a worktree.

    """
    directory = _worktrees_dir(project)
    if name in {"", ".."} or Path(name).name != name:
        raise ConfigError(
            f"'{name}' is not a name in {directory}; give the name of a directory in it"
        )
    path = directory / name
    if path.resolve() == project.repo.resolve():
        message = (
            f"{path}: it is the repository of project '{project.name}', not a "
            + "worktree to remove"
        )
        raise ConfigError(message)
    if not force:
        _refuse_losing_commits(project, path)
    args = ["worktree", "remove", *(["--force"] if force else []), str(path)]
    _ = run_git(args, cwd=project.repo)


def _refuse_losing_commits(project: Project, path: Path) -> None:
    """
    Refuse to remove the worktree at ``path`` if that would lose commits.

    A path that is not one of the project's worktrees is left to git.

    Raises:
        ConfigError: an operation is in progress there, or its ``HEAD`` is
            detached at a commit that no branch contains.
        GitError: git cannot inspect the worktree.

    """
    found = path.resolve()
    tree = next(
        (t for t in list_worktrees(project.repo) if t.path.resolve() == found), None
    )
    if tree is None:
        return
    git_dir = Path(run_git(["rev-parse", "--absolute-git-dir"], cwd=tree.path))
    for marker, operation in _IN_PROGRESS:
        if (git_dir / marker).exists():
            message = (
                f"{path}: {operation} is in progress there; finish it, or give "
                + "--force to remove the worktree all the same"
            )
            raise ConfigError(message)
    if tree.branch is not None:
        return
    containing = run_git(
        ["for-each-ref", "--contains", tree.head, "--format=%(refname)", "refs/heads"],
        cwd=project.repo,
    )
    if not containing:
        message = (
            f"{path}: HEAD is detached at {tree.head[:7]}, which no branch "
            + "contains, so removing the worktree would lose it; make a branch for "
            + "it, or give --force"
        )
        raise ConfigError(message)


def _worktrees_dir(project: Project) -> Path:
    if project.worktrees is None:
        message = (
            f"project '{project.name}' has no worktrees directory; "
            "set 'worktrees' in its entry of the registry"
        )
        raise ConfigError(message)
    return project.worktrees
