"""Builders shared by the test modules."""

import fcntl
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from atc.git import run_git
from atc.jsonval import JsonObject
from atc.merge import merge_objects
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


_PERSONAL_ACCOUNT = (
    "version: 1\n"
    "default-account: personal\n"
    "accounts:\n"
    "  personal:\n"
    "    agent: claude\n"
    "    dir: ~/.claude\n"
)


def registry(*account_lines: str, top: str = "") -> str:
    """
    Write a registry with one account, `personal`, in `~/.claude`.

    ``account_lines`` are further keys of the account, such as ``skills: [a]``,
    and ``top`` is put before the ``version`` line.
    """
    lines = "".join(f"    {line}\n" for line in account_lines)
    return top + _PERSONAL_ACCOUNT + lines


def skill_md(name: str | None = None, description: str | None = None) -> str:
    """Write a SKILL.md with front matter that sets whichever fields are given."""
    fields = {"name": name, "description": description}
    shown = "".join(f"{key}: {value}\n" for key, value in fields.items() if value)
    return f"---\n{shown}---\n# Body\n"


LINKED_REGISTRY = registry("use-default-agents-md: true")
"""A registry whose `personal` account links the default instructions file."""


def skill_files(*names: str) -> dict[str, str | JsonObject]:
    """Write a skill for each of ``names``, as the files of a source."""
    return {
        f"skills/{name}/SKILL.md": skill_md(name, f"The {name} skill") for name in names
    }


class StopError(Exception):
    """Raised by a test's stand-in for a call that never returns."""


def is_locked(path: Path) -> bool:
    """Say whether a second descriptor on ``path`` is refused an exclusive ``flock``."""
    descriptor = os.open(path, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(descriptor)
    return False


def packed_history(repo: Path) -> JsonObject:
    """Build the session entries for a new repository: only ``objects/pack`` exists."""
    pack = repo / ".git" / "objects" / "pack"
    return {
        "sandbox": {"filesystem": {"denyWrite": [str(pack)]}},
        "permissions": {"deny": ["Edit(/" + str(pack) + "/**)"]},
    }


def guarded(repo: Path, fragment: JsonObject) -> JsonObject:
    """``fragment`` with the session entries for ``repo`` added."""
    return merge_objects(fragment, packed_history(repo))
