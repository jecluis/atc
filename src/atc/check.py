"""Checking, from inside a session, that the session's protections hold."""

import os
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, final

from atc.backends import backend_for
from atc.backends.claude import CONFIG_DIR_VARIABLE
from atc.errors import GitError, RegistryError
from atc.git import common_dir as git_common_dir
from atc.git import run_git
from atc.paths import Dirs
from atc.registry import Account, Registry, normalize_path

ACCOUNT_VARIABLE = "ATC_ACCOUNT"
"""The variable that names the account a session was started in."""

_TOP_LEVEL = ("rev-parse", "--show-toplevel")
_GC_AUTO = ("config", "--get", "gc.auto")


class Probe(Protocol):
    """What a session can do to files and to git, found out without changing them."""

    def writable(self, path: Path) -> bool:
        """Say whether ``path`` can be written, without writing it."""
        ...

    def readable_contents(self, path: Path) -> bool:
        """Say whether ``path`` is really readable, not hidden or masked."""
        ...

    def exists(self, path: Path) -> bool:
        """Say whether ``path`` is there, even as a link that leads nowhere."""
        ...

    def git(self, args: Sequence[str]) -> str:
        """
        Give git's output for ``args``.

        Raises:
            GitError: git could not be run, or failed.

        """
        ...

    def common_dir(self) -> Path | None:
        """
        Give the git directory of the repository the process is in.

        ``None`` means git says it is not in one.

        Raises:
            GitError: git could not be run, or failed for another reason.

        """
        ...

    def readlink(self, path: Path) -> Path | None:
        """Give the target of the link ``path``, or ``None`` when it is not one."""
        ...

    def entries(self, directory: Path) -> list[Path]:
        """List what ``directory`` holds, or nothing when it cannot be read."""
        ...


@final
class RealProbe:
    """The :class:`Probe` of the process itself, in its own directory."""

    def writable(self, path: Path) -> bool:
        # access() reports a read-only mount (EROFS) without writing anything.
        return os.access(path, os.W_OK)

    def readable_contents(self, path: Path) -> bool:
        """
        Say whether ``path`` has contents the process can read.

        A sandbox hides a file by putting a character device over it, and hides a
        directory by putting an empty one over it, so those are not contents.
        """
        try:
            info = path.stat()
        except OSError:
            return False
        if stat.S_ISCHR(info.st_mode):
            return False
        if stat.S_ISDIR(info.st_mode):
            return _has_entries(path)
        return info.st_size > 0 and os.access(path, os.R_OK)

    def exists(self, path: Path) -> bool:
        return os.path.lexists(path)

    def git(self, args: Sequence[str]) -> str:
        return run_git(args)

    def common_dir(self) -> Path | None:
        return git_common_dir(Path())

    def readlink(self, path: Path) -> Path | None:
        try:
            return path.readlink()
        except OSError:
            return None

    def entries(self, directory: Path) -> list[Path]:
        try:
            return sorted(directory.iterdir())
        except OSError:
            return []


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One check: whether it ``ok``, and the claim ``what`` it makes."""

    ok: bool
    what: str


@dataclass(frozen=True, slots=True)
class Report:
    """
    What the checks found.

    ``results`` are the checks that ran. ``notes`` say which checks did not
    apply, or could not run, without being a result either way.
    """

    results: tuple[CheckResult, ...]
    notes: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """Whether every check that ran passed."""
        return all(result.ok for result in self.results)


def in_sandbox(probe: Probe, home: Path) -> bool:
    """Say whether this looks like a sandbox: the home directory cannot be written."""
    return not probe.writable(home)


def current_account(
    registry: Registry, env: Mapping[str, str], home: Path
) -> Account | None:
    """
    Find the account of the session whose environment is ``env``.

    ``ATC_ACCOUNT`` names it. Otherwise it is the account whose directory is
    ``CLAUDE_CONFIG_DIR``, compared as the registry compares directories, or,
    when that is unset, the account in the agent's default directory. ``None``
    means no account matches.
    """
    named = registry.accounts.get(env.get(ACCOUNT_VARIABLE, ""))
    if named is not None:
        return named
    configured = env.get(CONFIG_DIR_VARIABLE, "")
    if configured:
        try:
            directory = normalize_path(configured, home)
        except RegistryError:
            return None
        return next((a for a in registry.accounts.values() if a.dir == directory), None)
    return next(
        (
            a
            for a in registry.accounts.values()
            if backend_for(a.agent, home).is_default(a)
        ),
        None,
    )


def run_checks(
    probe: Probe,
    *,
    registry: Registry,
    dirs: Dirs,
    install_dir: Path,
    python_dir: Path,
    home: Path,
    env: Mapping[str, str],
) -> Report:
    """
    Check the protections of the session whose environment is ``env``.

    In order: the repository the process is in, the accounts' credentials,
    atc's own directories, ``install_dir`` and ``python_dir``, which are atc's
    environment and the Python installation it is built on, and what the
    current account's instructions file and skills lead to. A repository check
    that git cannot make fails, and one that does not apply, outside a
    repository, is a note.
    """
    account = current_account(registry, env, home)
    parts = [
        _repository(probe),
        _accounts(probe, registry, account, home, env),
        _atc_itself(probe, dirs, install_dir, python_dir),
        _instructions_and_skills(probe, account, home),
    ]
    return Report(
        tuple(result for part in parts for result in part.results),
        tuple(note for part in parts for note in part.notes),
    )


def _repository(probe: Probe) -> Report:
    try:
        git_dir = probe.common_dir()
    except GitError as exc:
        what = f"git cannot inspect this repository, so it is unchecked: {exc}"
        return Report((CheckResult(False, what),))
    if git_dir is None:
        return Report(
            (), ("not in a git repository, so the repository checks are skipped",)
        )
    results = [_read_only(probe, git_dir / "objects" / "pack")]
    results += [
        _read_only(probe, git_dir / name)
        for name in ("packed-refs", "hooks", "config")
        if probe.exists(git_dir / name)
    ]
    results.append(
        CheckResult(
            probe.writable(git_dir / "objects"),
            "new objects can be written, so commits work",
        )
    )
    results.append(_gc_is_off(probe))
    results.append(_worktree_parent_is_read_only(probe))
    return Report(tuple(results))


def _read_only(probe: Probe, path: Path) -> CheckResult:
    return CheckResult(not probe.writable(path), f"{path} is read-only")


def _gc_is_off(probe: Probe) -> CheckResult:
    what = "automatic gc is off in this session"
    try:
        return CheckResult(probe.git(_GC_AUTO) == "0", what)
    except GitError as exc:
        return CheckResult(False, f"{what} ({exc})")


def _worktree_parent_is_read_only(probe: Probe) -> CheckResult:
    try:
        top = probe.git(_TOP_LEVEL)
    except GitError as exc:
        what = f"the worktree can't be found, so its removal is unchecked ({exc})"
        return CheckResult(False, what)
    parent = Path(top).parent
    return CheckResult(
        not probe.writable(parent),
        f"the worktree can't be removed ({parent} is read-only)",
    )


def _accounts(
    probe: Probe,
    registry: Registry,
    current: Account | None,
    home: Path,
    env: Mapping[str, str],
) -> Report:
    """Check that the current account's secrets and every peer's files are hidden."""
    results: list[CheckResult] = []
    owned: list[tuple[Path, Account]] = []
    if current is None:
        results.append(CheckResult(False, _unidentified(env)))
    else:
        owned += [
            (path, current)
            for path in backend_for(current.agent, home).secrets(current)
        ]
    for peer in registry.accounts.values():
        if current is None or peer.name != current.name:
            backend = backend_for(peer.agent, home)
            owned += [(path, peer) for path in (peer.dir, *backend.files(peer))]
    seen: set[Path] = set()
    for path, account in owned:
        if path not in seen and probe.exists(path):
            results.append(
                CheckResult(
                    not probe.readable_contents(path),
                    f"{path} is not readable (account {account.name})",
                )
            )
        seen.add(path)
    return Report(tuple(results))


def _unidentified(env: Mapping[str, str]) -> str:
    """Say why no account matches the session, naming what the environment holds."""
    named = env.get(ACCOUNT_VARIABLE, "")
    configured = env.get(CONFIG_DIR_VARIABLE, "")
    first = (
        f"{ACCOUNT_VARIABLE}={named} names no account"
        if named
        else (f"{ACCOUNT_VARIABLE} is unset")
    )
    second = (
        f"{CONFIG_DIR_VARIABLE}={configured} is not an account's directory"
        if configured
        else f"{CONFIG_DIR_VARIABLE} is unset and no account uses the default directory"
    )
    return (
        "cannot tell which account this session is in, so its own credentials "
        f"are unchecked: {first}, and {second}"
    )


def _atc_itself(
    probe: Probe, dirs: Dirs, install_dir: Path, python_dir: Path
) -> Report:
    named = (
        ("configuration", dirs.config_dir),
        ("config sources", dirs.conf_d),
        ("state", dirs.state_dir),
        ("runtime", dirs.runtime_dir),
        ("install", install_dir),
        ("Python", python_dir),
    )
    return Report(
        tuple(
            CheckResult(
                not probe.writable(path),
                f"atc's {label} directory {path} is not writable",
            )
            for label, path in named
        )
    )


def _instructions_and_skills(
    probe: Probe, account: Account | None, home: Path
) -> Report:
    """
    Check what the account's instructions file and every skill entry lead to.

    That is the target of a link, or the entry itself when it is a file or a
    directory. An entry that is not there is not checked.
    """
    if account is None:
        note = "no account for this session: instructions and skills checks are skipped"
        return Report((), (note,))
    backend = backend_for(account.agent, home)
    entries = [
        backend.instructions_file(account),
        *probe.entries(backend.skills_dir(account)),
    ]
    results: list[CheckResult] = []
    for entry in entries:
        link = probe.readlink(entry)
        if link is not None:
            target = entry.parent / link
            results.append(
                CheckResult(
                    not probe.writable(target),
                    f"{target} is not writable (linked from {entry})",
                )
            )
        elif probe.exists(entry):
            results.append(
                CheckResult(
                    not probe.writable(entry), f"{entry} is not writable (not a link)"
                )
            )
    return Report(tuple(results))


def _has_entries(directory: Path) -> bool:
    try:
        with os.scandir(directory) as entries:
            return next(entries, None) is not None
    except OSError:
        return False
