"""Running git."""

import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from atc.errors import GitError


def run_git(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    """
    Run ``git`` with ``args`` and return its standard output, stripped.

    ``env`` adds to the environment atc runs in.

    Raises:
        GitError: git could not be run, ``cwd`` is not a directory, or git
            exited with a failure status.

    """
    command = ["git", *args]
    if cwd is not None and not cwd.is_dir():
        raise GitError(f"cannot run git in {cwd}: no such directory")
    try:
        completed = subprocess.run(  # noqa: S603 - fixed program, no shell
            command,
            cwd=cwd,
            env={**os.environ, **env} if env is not None else None,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise GitError(f"cannot run git: {exc.strerror or exc}") from exc
    if completed.returncode != 0:
        detail = (
            completed.stderr.strip()
            or completed.stdout.strip()
            or f"exit status {completed.returncode}"
        )
        raise GitError(f"{' '.join(command)}: {detail}")
    return completed.stdout.strip()


def git_or_none(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> str | None:
    """Run git as :func:`run_git` does, giving ``None`` instead of an error."""
    try:
        return run_git(args, cwd=cwd, env=env)
    except GitError:
        return None


def common_dir(cwd: Path) -> Path | None:
    """
    Return the git directory that the repository containing ``cwd`` shares.

    Every worktree of a repository has the same one, as an absolute path with
    symbolic links followed. ``None`` means git says ``cwd`` is not in a
    repository.

    Raises:
        GitError: git could not be run, or failed for another reason, such as
            refusing a repository that someone else owns.

    """
    try:
        shared = run_git(
            ["rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=cwd,
            # The message that says "not a repository" is only that in English.
            env={"LC_ALL": "C"},
        )
    except GitError as exc:
        if "not a git repository" in str(exc):
            return None
        raise
    return Path(shared).resolve()
