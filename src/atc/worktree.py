"""The worktrees of a repository, as git lists them."""

from dataclasses import dataclass
from pathlib import Path

from atc.git import run_git

_BRANCH_PREFIX = "refs/heads/"


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
