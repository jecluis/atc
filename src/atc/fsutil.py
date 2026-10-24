"""Writing files without losing what was there."""

import os
import re
import stat
import tempfile
from datetime import UTC, datetime
from pathlib import Path


def atomic_write(
    path: Path, text: str, *, default_mode: int = 0o600, mode: int | None = None
) -> None:
    """
    Replace the file at ``path`` with ``text``, all at once.

    The text goes to a temporary file in the same directory, which is flushed to
    disk and then renamed over ``path``, so a reader sees the old file or the new
    one and never half of either. When ``path`` is a symbolic link, such as one
    into a dotfiles repository, the file it leads to is the one replaced, in its
    own directory, and the link stays. The new file has the permissions ``mode``
    when it is given, else those of the one it replaces, or ``default_mode``
    when there is none. Nothing is left behind if the write fails.

    Raises:
        OSError: the file cannot be written.

    """
    target = path.resolve() if path.is_symlink() else path
    if mode is None:
        try:
            mode = stat.S_IMODE(target.stat().st_mode)
        except FileNotFoundError:
            mode = default_mode
    descriptor, name = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as file:
            _ = file.write(text)
            file.flush()
            os.fsync(file.fileno())
        temporary.chmod(mode)
        _ = temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def make_private_dir(path: Path) -> None:
    """
    Create ``path`` and the parents it is missing, each for its owner alone.

    ``Path.mkdir`` gives its mode only to the last directory and leaves the
    parents it makes to the umask, so each missing level is made on its own.

    Raises:
        OSError: a directory cannot be created.

    """
    if not path.exists():
        make_private_dir(path.parent)
        path.mkdir(mode=0o700, exist_ok=True)


def backup(path: Path, backups_dir: Path, now: datetime, *, keep: int = 10) -> Path:
    """
    Copy the file at ``path`` into ``backups_dir`` and return the copy's path.

    The copy is named ``<UTC time of now>-<file name>``, such as
    ``20261023T120000Z-settings.json``, or, when that name is taken,
    ``<time>.1-<file name>``, ``<time>.2-<file name>`` and so on, so that the
    backups of ``rust`` and of ``rust-2024`` can never be taken for each other.
    Only the ``keep`` newest backups of the same file name stay, and the copy
    just made always does. The copy is readable by its owner alone, since
    settings can hold secrets.

    Raises:
        OSError: the file cannot be copied.

    """
    make_private_dir(backups_dir)
    content = path.read_bytes()
    stamp = f"{now.astimezone(UTC):%Y%m%dT%H%M%SZ}"
    copy = backups_dir / f"{stamp}-{path.name}"
    number = 0
    while True:
        try:
            descriptor = os.open(copy, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode=0o600)
            break
        except FileExistsError:
            number += 1
            copy = backups_dir / f"{stamp}.{number}-{path.name}"
    with os.fdopen(descriptor, "wb") as file:
        _ = file.write(content)
    # The copy just made stays, even if a clock that went back dates it before
    # the others: the file it backs up is about to be replaced.
    others = [other for other in _backups_of(path.name, backups_dir) if other != copy]
    for old in others[: max(len(others) - max(keep - 1, 0), 0)]:
        old.unlink()
    return copy


def _backups_of(name: str, backups_dir: Path) -> list[Path]:
    """List the backups of the file called ``name``, oldest first."""
    pattern = re.compile(rf"(\d{{8}}T\d{{6}}Z)(?:\.(\d+))?-{re.escape(name)}")
    found: list[tuple[str, int, Path]] = []
    for entry in backups_dir.iterdir():
        match = pattern.fullmatch(entry.name)
        if match is not None:
            found.append((match[1], int(match[2] or 0), entry))
    return [entry for _, _, entry in sorted(found)]
