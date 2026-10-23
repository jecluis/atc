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
