"""Deploying config sources: a commit is checked before it is fast-forwarded to."""

import os
import stat
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from atc.errors import ConfigError, SourceError
from atc.git import git_or_none, run_git
from atc.sources import SOURCE_DIRS, Source, Sources, check_source
from atc.validate import Validation

type PullStatus = Literal["updated", "current", "skipped", "refused", "failed"]

LOG_LIMIT = 20
"""How many of the commits an update deploys are listed; the others are counted."""

_GROUP_WORLD_WRITE = stat.S_IWGRP | stat.S_IWOTH


@dataclass(frozen=True, slots=True)
class Deployed:
    """
    What an update brought into a source, for the user to review.

    ``commits`` are the newest of the commits, as ``git log --oneline`` lists
    them, at most ``LOG_LIMIT``, and ``more`` counts the others. ``stat`` is
    the lines of ``git diff --stat`` for ``conf``, ``assets`` and ``skills``,
    with no leading spaces, and is empty when none of them changed.
    """

    commits: tuple[str, ...]
    more: int
    stat: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PullResult:
    """
    What happened to one source.

    ``status`` says whether it was ``updated``, was already ``current``, was
    ``skipped`` because there is nothing to pull from, ``refused`` because its
    state forbids a pull, or ``failed``. ``detail`` explains in a few words.
    ``validation`` holds the check's findings on the incoming commit. It is set
    when the check ran, and a ``failed`` result with errors in it was not
    deployed. ``deployed`` is set when the source was fast-forwarded.
    """

    name: str
    status: PullStatus
    detail: str
    validation: Validation | None
    deployed: Deployed | None = None


def pull(
    sources: Sources,
    names: Sequence[str],
    validate: Callable[[Sources], Validation],
) -> list[PullResult]:
    """
    Update the git sources named in ``names``, or all of them when it is empty.

    A source is fast-forwarded to its upstream only if it has no uncommitted
    changes in ``conf``, ``assets`` or ``skills``, and the fetched commit passes
    the checks. The commit is written out whole, as the fast-forward would write
    it, into a temporary directory. That tree must then meet what discovery
    requires of the source, trust rules included, as the user running atc, and
    ``validate`` must find no errors in ``sources`` with this one replaced by
    it. A source that is refused or fails is left as it was, except when it
    could not be made safe to load after the fast-forward, which the result
    says.

    Raises:
        PathError: a name is not that of a source. Nothing is pulled then.

    """
    chosen = (
        [sources.named(name) for name in dict.fromkeys(names)]
        if names
        else list(sources.items)
    )
    return [_pull_source(sources, source, validate) for source in chosen]


def _pull_source(
    sources: Sources, source: Source, validate: Callable[[Sources], Validation]
) -> PullResult:
    try:
        return _update(sources, source, validate)
    except (ConfigError, OSError) as exc:
        return PullResult(source.name, "failed", " ".join(str(exc).split()), None)


def _update(
    sources: Sources, source: Source, validate: Callable[[Sources], Validation]
) -> PullResult:
    root = source.root
    name = source.name
    if not (root / ".git").exists():
        return PullResult(name, "skipped", "not a git clone", None)
    changes = run_git(
        ["status", "--porcelain", "--untracked-files=normal", "--", *SOURCE_DIRS],
        cwd=root,
    )
    if changes:
        detail = "uncommitted changes in conf, assets or skills"
        return PullResult(name, "refused", detail, None)
    upstream = git_or_none(["rev-parse", "--abbrev-ref", "@{upstream}"], cwd=root)
    if upstream is None:
        return PullResult(name, "skipped", "no upstream branch to update from", None)

    _ = run_git(["fetch", "--quiet"], cwd=root)
    commit = run_git(["rev-parse", "--verify", "@{upstream}^{commit}"], cwd=root)
    old = run_git(["rev-parse", "--verify", "HEAD"], cwd=root)
    counts = run_git(
        ["rev-list", "--left-right", "--count", f"HEAD...{commit}"], cwd=root
    )
    ahead, behind = map(int, counts.split())
    if ahead:
        detail = (
            f"cannot fast-forward to {upstream}: "
            f"the clone is {ahead} ahead and {behind} behind"
        )
        return PullResult(name, "refused", detail, None)
    if not behind:
        return PullResult(name, "current", f"already at {old[:7]}", None)

    with tempfile.TemporaryDirectory(prefix="atc-pull-") as scratch:
        tree = _checkout(root, commit, Path(scratch))
        found = _named_in(_check(sources, Source(name, tree), validate), tree, root)
    if found.errors:
        detail = f"{upstream} ({commit[:7]}) fails the check, so it was not deployed"
        return PullResult(name, "failed", detail, found)

    deployed = _deployed(root, old, commit, behind)
    _ = run_git(["merge", "--ff-only", "--quiet", commit], cwd=root)
    try:
        _remove_group_world_write(source)
    except OSError as exc:
        detail = f"fast-forwarded to {commit[:7]}, but {exc}"
        return PullResult(name, "failed", detail, found, deployed)
    return PullResult(name, "updated", f"{old[:7]}..{commit[:7]}", found, deployed)


def _deployed(root: Path, old: str, new: str, count: int) -> Deployed:
    """Describe the ``count`` commits from ``old`` to ``new`` and what they change."""
    span = f"{old}..{new}"
    log = run_git(
        [
            "log",
            "--oneline",
            "--no-decorate",
            "--no-color",
            "--no-show-signature",
            f"--max-count={LOG_LIMIT}",
            span,
        ],
        cwd=root,
    )
    diff = run_git(["diff", "--stat", "--no-color", span, "--", *SOURCE_DIRS], cwd=root)
    commits = tuple(log.splitlines())
    stat_lines = tuple(line.strip() for line in diff.splitlines())
    return Deployed(commits, max(count - len(commits), 0), stat_lines)


def _checkout(root: Path, commit: str, scratch: Path) -> Path:
    """Write the whole of ``commit`` into ``scratch`` as git would, and return it."""
    tree = scratch / "tree"
    tree.mkdir(mode=0o700)
    # The work tree is the new tree, so that attributes such as eol are those of
    # the incoming commit, as they are for the fast-forward. Export attributes
    # play no part, and symbolic links are written as links.
    env = {
        "GIT_DIR": run_git(["rev-parse", "--absolute-git-dir"], cwd=root),
        "GIT_WORK_TREE": str(tree),
        "GIT_INDEX_FILE": str(scratch / "index"),
    }
    _ = run_git(["read-tree", commit], cwd=tree, env=env)
    _ = run_git(["checkout-index", "--all"], cwd=tree, env=env)
    return tree


def _check(
    sources: Sources,
    candidate: Source,
    validate: Callable[[Sources], Validation],
) -> Validation:
    """Check ``candidate`` as a source, then ``sources`` with it in place of its own."""
    try:
        _remove_group_world_write(candidate)
        check_source(candidate, layout=sources.layout, uid=os.getuid())
    except (OSError, SourceError) as exc:
        return Validation(units=0, errors=(str(exc),), warnings=())
    return validate(sources.replace(candidate.name, candidate.root))


def _named_in(found: Validation, tree: Path, root: Path) -> Validation:
    """Name ``root`` where ``found`` names ``tree``, which is about to be deleted."""
    return Validation(
        units=found.units,
        errors=tuple(error.replace(str(tree), str(root)) for error in found.errors),
        warnings=tuple(w.replace(str(tree), str(root)) for w in found.warnings),
    )


def _remove_group_world_write(source: Source) -> None:
    """
    Remove group and world write from ``conf``, ``assets`` and ``skills``.

    Git applies the umask to what it writes, and the trust rules refuse
    anything writable by others. Symbolic links are left alone.

    Raises:
        OSError: a mode could not be read or changed. The message names the path.

    """
    for directory in SOURCE_DIRS:
        top = source.root / directory
        if top.is_symlink() or not top.is_dir():
            continue
        _remove_write(top)
        for here, subdirs, files in os.walk(top):
            for child in (*subdirs, *files):
                _remove_write(Path(here, child))


def _remove_write(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
        if not stat.S_ISLNK(mode) and mode & _GROUP_WORLD_WRITE:
            os.chmod(path, stat.S_IMODE(mode) & ~_GROUP_WORLD_WRITE)
    except OSError as exc:
        detail = exc.strerror or str(exc)
        raise OSError(f"{path}: cannot remove group and world write: {detail}") from exc
