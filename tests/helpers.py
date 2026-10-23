"""Builders shared by the test modules."""

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from atc.git import run_git
from atc.jsonval import JsonObject
from atc.unit import UNIT_VERSION

UID = os.getuid()


def unit(
    settings: JsonObject | None = None, *, imports: Sequence[str] = ()
) -> JsonObject:
    """Build a unit envelope at the current unit version."""
    envelope: JsonObject = {"version": UNIT_VERSION}
    if imports:
        envelope["imports"] = [*imports]
    if settings is not None:
        envelope["settings"] = settings
    return envelope


def make_source(root: Path, files: Mapping[str, str | JsonObject]) -> Path:
    """
    Write ``files`` under ``root``, as a source that meets the trust rules.

    A ``JsonObject`` is written as JSON. Files get mode 0o644 and the
    directories from ``root`` down, and any above it that this creates, get
    0o755, whatever the umask.
    """
    missing = [path for path in (root, *root.parents) if not path.exists()]
    root.mkdir(parents=True, exist_ok=True)
    for path in (root, *missing):
        path.chmod(0o755)
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        directory = path.parent
        while directory != root.parent:
            directory.chmod(0o755)
            directory = directory.parent
        text = content if isinstance(content, str) else json.dumps(content)
        _ = path.write_text(text, encoding="utf-8")
        path.chmod(0o644)
    return root


def git_repo(path: Path) -> Path:
    """Turn ``path`` into a git repository with one commit of everything in it."""
    _ = run_git(["init", "-q", "-b", "main"], cwd=path)
    _ = run_git(["add", "-A"], cwd=path)
    _ = run_git(["commit", "-q", "-m", "init"], cwd=path)
    return path


def bare_upstream(path: Path, files: Mapping[str, str | JsonObject]) -> Path:
    """Create a bare repository at ``path`` with one commit of ``files``."""
    seed = git_repo(make_source(path.with_name(f"{path.name}-seed"), files))
    _ = run_git(["clone", "-q", "--bare", str(seed), str(path)])
    return path


def publish(
    upstream: Path,
    files: Mapping[str, str | JsonObject] | None = None,
    *,
    links: Mapping[str, str] | None = None,
    remove: Sequence[str] = (),
) -> str:
    """
    Add a commit to ``upstream`` that changes the files in it.

    It writes ``files``, makes the symbolic ``links`` (name to target) and
    deletes ``remove``. It goes through a second clone kept next to the
    repository, as someone else pushing would. Returns the new commit's id.
    """
    work = upstream.with_name(f"{upstream.name}-work")
    if not work.exists():
        _ = run_git(["clone", "-q", str(upstream), str(work)])
    _ = make_source(work, files or {})
    for relative, target in (links or {}).items():
        (work / relative).parent.mkdir(parents=True, exist_ok=True)
        (work / relative).symlink_to(target)
    for relative in remove:
        (work / relative).unlink()
    _ = run_git(["add", "-A"], cwd=work)
    _ = run_git(["commit", "-q", "--allow-empty", "-m", "update"], cwd=work)
    _ = run_git(["push", "-q"], cwd=work)
    return run_git(["rev-parse", "HEAD"], cwd=work)


# The closing quotes are not indented, so that lines can be appended: four
# spaces for a top-level key, six for another account.
MINIMAL_REGISTRY = """\
    version: 1
    default-account: personal
    accounts:
      personal: {agent: claude, dir: ~/.claude}
"""
"""
A registry with one account, `personal`, whose unit is `accounts/personal.json`.

It is also the default account.
"""
