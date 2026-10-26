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
from atc.guard import GUARD_VARIABLE
from atc.paths import Dirs
from atc.refs import SKILL_FILE
from atc.registry import REGISTRY_FILE, Account, Registry, normalize_path

ACCOUNT_VARIABLE = "ATC_ACCOUNT"
"""The variable that names the account a session was started in."""
_GUARD_SKIPPED = "the objects/pack, packed-refs and automatic gc checks are skipped"
_NOT_STARTED = (
    "atc run did not start this session, so nothing guards its packed history, "
    f"and {_GUARD_SKIPPED}"
)
"""Why the guard's checks are skipped in a session that atc run did not start."""
_UNGUARDED = (
    "atc run started this session unguarded (--no-guard, or outside a "
    f"repository), so {_GUARD_SKIPPED}"
)
"""Why the guard's checks are skipped in a session atc run started unguarded."""

_TOP_LEVEL = ("rev-parse", "--show-toplevel")
_GC_AUTO = ("config", "--get", "gc.auto")
_WORKTREE_CONFIG = (
    "config",
    "--local",
    "--type=bool",
    "--default=false",
    "--get",
    "extensions.worktreeConfig",
)
_CONFIG_WORKTREE = (
    "rev-parse",
    "--path-format=absolute",
    "--git-path",
    "config.worktree",
)
_MAX_LINKS = 40
"""How many symbolic links are followed from one path, as Linux follows them."""


class Probe(Protocol):
    """What a session can do to files and to git, found out without changing them."""

    def writable(self, path: Path) -> bool:
        """
        Say whether ``path``, which exists, can be written, without writing it.

        For a directory that is whether entries can be made in it.
        """
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
        """
        List what ``directory`` holds, which is nothing when it is missing.

        Raises:
            OSError: ``directory`` is there but cannot be listed.

        """
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
        except FileNotFoundError:
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
    environment and the Python installation it is built on, and the registry,
    and then the current account's settings file and what its instructions
    file and skills lead to. The registry and the settings file are checked
    where a link leads. A repository check that git cannot make fails, and one
    that does not apply, outside a repository, is a note. So are the checks of
    the guard that ``atc run`` sets up, in a session it did not guard.
    """
    account = current_account(registry, env, home)
    parts = [
        _repository(probe, env),
        _accounts(probe, registry, account, home, env),
        _atc_itself(probe, dirs, install_dir, python_dir),
        _instructions_and_skills(probe, account, home),
    ]
    return Report(
        tuple(result for part in parts for result in part.results),
        tuple(note for part in parts for note in part.notes),
    )


def _repository(probe: Probe, env: Mapping[str, str]) -> Report:
    try:
        git_dir = probe.common_dir()
    except GitError as exc:
        what = f"git cannot inspect this repository, so it is unchecked: {exc}"
        return Report((CheckResult(False, what),))
    if git_dir is None:
        return Report(
            (), ("not in a git repository, so the repository checks are skipped",)
        )
    guarded, note = _guard(env, git_dir)
    names = ("packed-refs", "hooks", "config") if guarded else ("hooks", "config")
    results = [_read_only(probe, git_dir / "objects" / "pack")] if guarded else []
    results += [
        _read_only(probe, git_dir / name)
        for name in names
        if probe.exists(git_dir / name)
    ]
    results += _config_worktree(probe, git_dir)
    results.append(
        CheckResult(
            probe.writable(git_dir / "objects"),
            "new objects can be written, so commits work",
        )
    )
    if guarded:
        results.append(_gc_is_off(probe))
    results.append(_worktree_parent_is_read_only(probe))
    return Report(tuple(results), () if note is None else (note,))


def _guard(env: Mapping[str, str], git_dir: Path) -> tuple[bool, str | None]:
    """
    Say whether to check the guard of ``git_dir``, and the note to give, if any.

    The guard is what ``atc run`` sets up to protect the packed history:
    ``objects/pack`` and ``packed-refs`` read-only, and automatic gc off. It is
    checked when ``atc run`` guarded the session, and for this repository even
    when the guard is another one's, since this is the history at stake.
    """
    guarded = env.get(GUARD_VARIABLE, "")
    if guarded:
        if Path(guarded) == git_dir:
            return True, None
        return True, f"atc run guarded {guarded}, not this repository"
    if env.get(ACCOUNT_VARIABLE, ""):
        return False, _UNGUARDED
    return False, _NOT_STARTED


def _config_worktree(probe: Probe, git_dir: Path) -> list[CheckResult]:
    """
    Check the ``config.worktree`` files git reads, if it reads any.

    It does when ``extensions.worktreeConfig`` is on: the shared one, and the
    current worktree's own, which is the shared one in the main worktree.
    """
    try:
        if probe.git(_WORKTREE_CONFIG) != "true":
            return []
    except GitError as exc:
        what = (
            f"cannot tell whether git reads config.worktree, so it is unchecked ({exc})"
        )
        return [CheckResult(False, what)]
    shared = git_dir / "config.worktree"
    results = [_read_only(probe, shared)]
    try:
        own = Path(probe.git(_CONFIG_WORKTREE))
    except GitError as exc:
        what = (
            f"cannot find this worktree's config.worktree, so it is unchecked ({exc})"
        )
        return [*results, CheckResult(False, what)]
    if own != shared:
        results.append(_read_only(probe, own))
    return results


def _read_only(probe: Probe, path: Path) -> CheckResult:
    return _cannot_write(probe, path, str(path), claim="is read-only")


def _cannot_write(
    probe: Probe,
    path: Path,
    subject: str,
    *,
    claim: str = "is not writable",
    where: str = "",
) -> CheckResult:
    """
    Check that ``path``, which messages call ``subject``, cannot be written.

    Symbolic links are followed to where writing ``path`` would land, as
    :func:`_landing` does. When that is missing, what counts is whether a
    session could create it: whether the nearest directory above it that
    exists can be written. A loop of links, or too many, fails. ``claim`` is
    what passing says, and ``where``, when given, is added in brackets.
    """
    tail = f" ({where})" if where else ""
    landing = _landing(probe, path)
    if landing is None:
        what = (
            f"{subject} leads through a loop of symbolic links, or too many of "
            + f"them, so it is unchecked{tail}"
        )
        return CheckResult(False, what)
    if probe.exists(landing):
        return CheckResult(not probe.writable(landing), f"{subject} {claim}{tail}")
    above = _nearest(probe, landing)
    if above is None:
        missing = f"{subject} is missing, and no directory above it exists{tail}"
        return CheckResult(False, missing)
    if probe.writable(above):
        return CheckResult(
            False, f"{subject} is missing and could be created in {above}{tail}"
        )
    return CheckResult(True, f"{subject} is missing and can't be created{tail}")


def _landing(probe: Probe, path: Path) -> Path | None:
    """
    Find where writing ``path`` would land, following symbolic links.

    That is past the links at its end, and, while it is missing, past a link
    that is the nearest directory above it that exists, since what is missing
    would be made where that link leads. ``None`` means the links run out: a
    loop, or more than ``_MAX_LINKS``.
    """
    for _ in range(_MAX_LINKS):
        link = probe.readlink(path)
        if link is not None:
            path = path.parent / link
            continue
        if probe.exists(path):
            return path
        above = _nearest(probe, path)
        link = None if above is None else probe.readlink(above)
        if above is None or link is None:
            return path
        path = above.parent / link / path.relative_to(above)
    return None


def _nearest(probe: Probe, path: Path) -> Path | None:
    """Find the nearest directory above ``path`` that exists, if any does."""
    return next((parent for parent in path.parents if probe.exists(parent)), None)


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
    registry = dirs.config_dir / REGISTRY_FILE
    return Report(
        (
            *(
                _cannot_write(probe, path, f"atc's {label} directory {path}")
                for label, path in named
            ),
            _linked_file(probe, registry, f"atc's registry {registry}"),
        )
    )


def _linked_file(probe: Probe, path: Path, subject: str) -> CheckResult:
    """Check ``path`` as :func:`_cannot_write` does, naming where a link leads."""
    link = probe.readlink(path)
    where = "" if link is None else f"a link to {path.parent / link}"
    return _cannot_write(probe, path, subject, where=where)


def _instructions_and_skills(
    probe: Probe, account: Account | None, home: Path
) -> Report:
    """
    Check the account's settings, instructions file and every skill entry.

    The settings file is checked where a link leads. For the others it is
    the target of a link, or the entry itself when it is a file or a
    directory, and for a skill that is there, its ``SKILL.md`` as well. An
    instructions file that is not there is not checked. A skills directory that
    is there but cannot be listed fails.
    """
    if account is None:
        note = (
            "no account for this session: settings, instructions and skills checks "
            + "are skipped"
        )
        return Report((), (note,))
    backend = backend_for(account.agent, home)
    settings = backend.settings_file(account)
    skills = backend.skills_dir(account)
    results = [_linked_file(probe, settings, f"the account's settings {settings}")]
    try:
        listed = probe.entries(skills)
    except OSError as exc:
        what = f"{skills} cannot be listed, so its skills are unchecked: {exc}"
        results.append(CheckResult(False, what))
        listed = []
    instructions = backend.instructions_file(account)
    for entry in (instructions, *listed):
        link = probe.readlink(entry)
        if link is not None:
            found, where = entry.parent / link, f"linked from {entry}"
        elif probe.exists(entry):
            found, where = entry, "not a link"
        else:
            continue
        results.append(_cannot_write(probe, found, str(found), where=where))
        landing = _landing(probe, found)
        if entry != instructions and landing is not None and probe.exists(landing):
            skill = found / SKILL_FILE
            results.append(_cannot_write(probe, skill, str(skill), where=where))
    return Report(tuple(results))


def _has_entries(directory: Path) -> bool:
    try:
        with os.scandir(directory) as entries:
            return next(entries, None) is not None
    except OSError:
        return False
