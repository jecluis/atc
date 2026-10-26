"""Guarding a repository's packed history, and keeping it packed."""

import fcntl
import hashlib
import os
import re
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path

from atc.errors import ConfigError, GitError
from atc.fsutil import make_private_dir
from atc.git import run_git
from atc.paths import Dirs
from atc.ui import Ui

REPOS_FILE = "repos"
"""The list of guarded repositories, in the state directory."""

COUNT_VARIABLE = "GIT_CONFIG_COUNT"
GUARD_VARIABLE = "ATC_GUARD"
"""The variable that holds the git directory a session's guard covers, when guarded."""
GC_SETTINGS = (("gc.auto", "0"), ("maintenance.auto", "false"))
"""The git settings that keep git from packing the repository by itself."""

_COUNT = re.compile(r"\s*\+?(\d+)", re.ASCII)
_POLL_SECONDS = 0.05
_SECONDS_PER_HOUR = 3600


def protected_paths(common_dir: Path) -> tuple[Path, ...]:
    """
    Return the packed history of the repository with git directory ``common_dir``.

    That is ``objects/pack`` and ``packed-refs``, whichever of them exists: the
    sandbox would put a read-only placeholder where a missing path is.
    """
    candidates = (common_dir / "objects" / "pack", common_dir / "packed-refs")
    return tuple(path for path in candidates if path.exists())


def with_gc_disabled(env: Mapping[str, str]) -> dict[str, str]:
    """
    Return ``env`` with git's automatic gc turned off through ``GIT_CONFIG_*``.

    The two settings go after any that ``env`` already has, so those stay.
    ``GIT_CONFIG_COUNT`` is read as git reads it: empty or unset is 0, and
    leading white space and a ``+`` are allowed.

    Raises:
        ConfigError: ``GIT_CONFIG_COUNT`` is not a count, which git refuses too.

    """
    text = env.get(COUNT_VARIABLE, "")
    count = 0
    if text:
        match = _COUNT.fullmatch(text)
        if match is None:
            message = (
                f"{COUNT_VARIABLE}='{text}' is not a number, so atc cannot add "
                "its own git settings after it"
            )
            raise ConfigError(message)
        count = int(match[1])
    changed = dict(env)
    for index, (key, value) in enumerate(GC_SETTINGS, start=count):
        changed[f"GIT_CONFIG_KEY_{index}"] = key
        changed[f"GIT_CONFIG_VALUE_{index}"] = value
    changed[COUNT_VARIABLE] = str(count + len(GC_SETTINGS))
    return changed


def repo_key(common_dir: Path) -> str:
    """Name the repository for its lock: 16 hex digits of the path's SHA-256."""
    return hashlib.sha256(os.fsencode(common_dir)).hexdigest()[:16]


def register_repo(dirs: Dirs, common_dir: Path) -> None:
    """
    Add ``common_dir`` to the repository list in ``dirs.state_dir``, if absent.

    The list has one path per line. Only its owner can write it. It is read
    first and opened for writing only when ``common_dir`` is not in it, so a
    list that is read-only, as it is inside a sandbox, does for a repository it
    already has.

    Raises:
        OSError: the list cannot be read, or the state directory or the list
            cannot be written and ``common_dir`` is not in the list.

    """
    path = dirs.state_dir / REPOS_FILE
    line = str(common_dir)
    try:
        with path.open(encoding="utf-8", errors="surrogateescape", newline="") as file:
            fcntl.flock(file, fcntl.LOCK_SH)
            if line in file.read().split("\n"):
                return
    except FileNotFoundError:
        pass
    make_private_dir(dirs.state_dir)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(
        descriptor, "a+", encoding="utf-8", errors="surrogateescape"
    ) as file:
        fcntl.flock(file, fcntl.LOCK_EX)
        _ = file.seek(0)
        content = file.read()
        if line in content.split("\n"):
            return
        separator = "\n" if content and not content.endswith("\n") else ""
        _ = file.write(f"{separator}{line}\n")


def registered_repos(dirs: Dirs) -> list[Path]:
    """
    List the repositories in the list in ``dirs.state_dir``, in its order.

    There are none when the list does not exist yet.

    Raises:
        ConfigError: the list exists but cannot be read.

    """
    path = dirs.state_dir / REPOS_FILE
    try:
        with path.open(encoding="utf-8", errors="surrogateescape", newline="") as file:
            fcntl.flock(file, fcntl.LOCK_SH)
            content = file.read()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise ConfigError(f"{path}: cannot be read: {exc}") from exc
    return [Path(line) for line in content.split("\n") if line]


def open_session_lock(
    dirs: Dirs, common_dir: Path, *, timeout: float = 60.0
) -> tuple[int | None, str | None]:
    """
    Take a shared lock on the repository, for the session that is starting.

    The lock is a ``flock`` on a file in ``dirs.runtime_dir``, and the returned
    descriptor is inheritable, so the lock lasts until the last process that
    inherited it, which is the agent and everything it starts, has exited.
    Repository maintenance takes the lock exclusively, so this waits up to
    ``timeout`` seconds for it.

    Without a lock, the result is ``None`` and a warning: when the lock file
    cannot be made or opened, or maintenance is still running after ``timeout``.
    """
    path = _lock_path(dirs, common_dir)
    try:
        make_private_dir(dirs.runtime_dir)
        descriptor = os.open(path, os.O_RDONLY | os.O_CREAT, 0o600)
    except OSError as exc:
        return None, f"cannot open the session lock {path}: {exc}; starting without it"
    deadline = time.monotonic() + timeout
    try:
        while not _try_lock(descriptor, fcntl.LOCK_SH):
            if time.monotonic() >= deadline:
                os.close(descriptor)
                return None, (
                    "repository maintenance is still running; "
                    "starting without the session lock"
                )
            time.sleep(_POLL_SECONDS)
        os.set_inheritable(descriptor, True)
    except OSError as exc:
        os.close(descriptor)
        message = f"cannot lock {path}: {exc}; starting without the session lock"
        return None, message
    return descriptor, None


def take_snapshot(
    config: str, cwd: Path, *, run: Callable[[Sequence[str]], int]
) -> str | None:
    """
    Make a snapper snapshot with the config ``config``, for a session in ``cwd``.

    ``run`` runs a command and gives its exit status. The result is ``None``, or
    a warning when snapper fails or cannot be run.
    """
    command = [
        "snapper",
        "-c",
        config,
        "create",
        "--cleanup-algorithm",
        "number",
        "--description",
        f"atc: {cwd}",
    ]
    try:
        status = run(command)
    except OSError as exc:
        return f"snapper snapshot failed: {exc}; starting anyway"
    if status != 0:
        return f"snapper snapshot failed (exit status {status}); starting anyway"
    return None


def run_command(command: Sequence[str]) -> int:
    """Run ``command``, and return its exit status."""
    return subprocess.run(command, check=False).returncode  # noqa: S603 - fixed program


def maint_repo(dirs: Dirs, common_dir: Path, *, gc_hours: int, now: datetime) -> None:
    """
    Keep the repository with git directory ``common_dir`` packed.

    Loose objects go into packs first. That only merges packs and never drops an
    object, so it is safe while sessions run. Then, only if no session holds the
    repository's lock, so the exclusive lock is free without waiting, the
    references are packed and ``git gc`` runs when the repository's stamp in
    ``dirs.state_dir`` is missing or older than ``gc_hours`` before ``now``, and
    the stamp is renewed. Both rewrite ``packed-refs``, which a running session
    must not see change.

    Raises:
        GitError: a git command failed.
        OSError: the lock file or the stamp cannot be used.

    """
    env = {"GIT_DIR": str(common_dir)}
    _ = run_git(["repack", "--geometric=2", "-d", "-q"], env=env)
    make_private_dir(dirs.runtime_dir)
    descriptor = os.open(_lock_path(dirs, common_dir), os.O_RDONLY | os.O_CREAT, 0o600)
    try:
        if not _try_lock(descriptor, fcntl.LOCK_EX):
            return
        _ = run_git(["pack-refs", "--all"], env=env)
        stamp = dirs.state_dir / f"{repo_key(common_dir)}.gc"
        if _gc_due(stamp, gc_hours, now):
            _ = run_git(["gc", "--quiet"], env=env)
            make_private_dir(dirs.state_dir)
            stamp.touch(mode=0o600)
            os.utime(stamp, (now.timestamp(), now.timestamp()))
    finally:
        os.close(descriptor)


def maint(dirs: Dirs, *, gc_hours: int, now: datetime) -> list[str]:
    """
    Run :func:`maint_repo` on every repository in the list, and say what failed.

    A repository without ``objects/`` is skipped with a warning on standard
    error. The result has one message for each repository whose maintenance
    failed, and the others are maintained all the same.

    Raises:
        ConfigError: the list of repositories cannot be read.

    """
    ui = Ui.default()
    failures: list[str] = []
    for common_dir in registered_repos(dirs):
        if not (common_dir / "objects").is_dir():
            ui.warning(f"skipping {common_dir}: not a repository any more")
            continue
        try:
            maint_repo(dirs, common_dir, gc_hours=gc_hours, now=now)
        except (GitError, OSError) as exc:
            failures.append(f"maintenance failed for {common_dir}: {exc}")
    return failures


def _lock_path(dirs: Dirs, common_dir: Path) -> Path:
    """Name the file whose lock says that sessions are using the repository."""
    return dirs.runtime_dir / f"{repo_key(common_dir)}.lock"


def _try_lock(descriptor: int, operation: int) -> bool:
    """Try to take the lock ``operation`` without waiting, and say if it was taken."""
    try:
        fcntl.flock(descriptor, operation | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _gc_due(stamp: Path, gc_hours: int, now: datetime) -> bool:
    """Say whether ``stamp`` is missing or more than ``gc_hours`` older than ``now``."""
    try:
        renewed = stamp.stat().st_mtime
    except FileNotFoundError:
        return True
    return renewed < now.timestamp() - gc_hours * _SECONDS_PER_HOUR
