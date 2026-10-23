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
        GitError: git could not be run, or exited with a failure status.

    """
    command = ["git", *args]
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
        raise GitError(f"cannot run git: {exc}") from exc
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
