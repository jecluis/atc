"""Config sources: finding them, and checking atc may trust them."""

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from atc.errors import SourceError

SOURCE_DIRS = ("conf", "assets", "skills")
"""The subdirectories atc reads from a source. Anything else in it is ignored."""

_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*")


@dataclass(frozen=True, slots=True)
class Source:
    """
    A directory with units in ``conf``, files in ``assets`` and skills in ``skills``.

    ``name`` is empty for the one source of the single layout.
    """

    name: str
    root: Path

    @property
    def conf(self) -> Path:
        return self.root / "conf"

    @property
    def assets(self) -> Path:
        return self.root / "assets"

    @property
    def skills(self) -> Path:
        return self.root / "skills"

    def display(self, rel: str) -> str:
        """Name ``rel`` in this source the way units refer to it: ``@name/rel``."""
        return f"@{self.name}/{rel}" if self.name else rel


@dataclass(frozen=True, slots=True)
class Sources:
    """
    The config sources found in ``conf_d``.

    ``items`` are sorted by name. ``warnings`` describe entries atc ignored.
    """

    conf_d: Path
    layout: Literal["single", "multi"]
    items: tuple[Source, ...]
    warnings: tuple[str, ...]


def discover(conf_d: Path, *, uid: int) -> Sources:
    """
    Find the config sources in ``conf_d`` and check that each one is trusted.

    With ``conf``, ``assets`` or ``skills`` directly in ``conf_d`` it is itself
    the one source. Otherwise each visible subdirectory that has one of them is
    a source, named after the directory. Nothing deeper is read.

    ``conf_d``, each source directory, and everything under a source's
    ``conf``, ``assets`` and ``skills`` must be owned by ``uid``, must not be
    group- or world-writable, and must not be a symbolic link.

    Raises:
        SourceError: ``conf_d`` is missing, has sources at both levels or none
            at all, a source name is invalid, or something is not trusted.

    """
    _require_directory(conf_d, uid)
    if _has_source_dirs(conf_d):
        _reject_nested_sources(conf_d)
        layout: Literal["single", "multi"] = "single"
        items = (Source("", conf_d),)
        warnings: tuple[str, ...] = ()
    else:
        layout = "multi"
        items, warnings = _find_nested(conf_d)
    if not items:
        message = (
            f"{conf_d}: no config sources found; a source has a conf, assets "
            f"or skills directory. Add one with: git clone <url> {conf_d}"
        )
        raise SourceError(message)
    for source in items:
        _ = _check_entry(source.root, uid)
        for name in SOURCE_DIRS:
            _check_tree(source.root / name, uid)
    return Sources(conf_d=conf_d, layout=layout, items=items, warnings=warnings)


def _require_directory(conf_d: Path, uid: int) -> None:
    if not conf_d.is_symlink() and not conf_d.exists():
        message = (
            f"{conf_d}: not found. Add a source with: git clone <url> {conf_d} "
            f"(or {conf_d}/<name> to keep several)"
        )
        raise SourceError(message)
    if not stat.S_ISDIR(_check_entry(conf_d, uid).st_mode):
        raise SourceError(f"{conf_d}: not a directory")


def _has_source_dirs(directory: Path) -> bool:
    return any((directory / name).is_dir() for name in SOURCE_DIRS)


def _children(directory: Path) -> list[Path]:
    """List the visible entries of ``directory``, sorted."""
    try:
        entries = sorted(directory.iterdir())
    except OSError as exc:
        raise SourceError(f"{directory}: cannot be read: {exc}") from exc
    return [entry for entry in entries if not entry.name.startswith(".")]


def _reject_nested_sources(conf_d: Path) -> None:
    """Refuse a ``conf_d`` that is a source and also holds sources of its own."""
    nested = [
        entry.name
        for entry in _children(conf_d)
        if entry.name not in SOURCE_DIRS
        and not entry.is_symlink()
        and entry.is_dir()
        and _has_source_dirs(entry)
    ]
    if nested:
        here = [name for name in SOURCE_DIRS if (conf_d / name).is_dir()]
        message = (
            f"{conf_d}: config sources at both levels: {', '.join(here)} here, "
            f"and in {', '.join(nested)}; use one layout"
        )
        raise SourceError(message)


def _find_nested(conf_d: Path) -> tuple[tuple[Source, ...], tuple[str, ...]]:
    """Find the sources one level down, and warn about directories that are not."""
    items: list[Source] = []
    warnings: list[str] = []
    for entry in _children(conf_d):
        if not entry.is_dir():
            continue
        if not _has_source_dirs(entry):
            unused = (
                f"{entry}: not a config source, as it has none of "
                f"{', '.join(SOURCE_DIRS)}; ignored"
            )
            warnings.append(unused)
            continue
        if entry.is_symlink():
            raise SourceError(f"{entry}: is a symbolic link; it must be a directory")
        if not _NAME.fullmatch(entry.name):
            message = (
                f"{entry}: '{entry.name}' is not a valid source name; names are "
                "lowercase letters, digits, '_' and '-', and start with a letter "
                "or a digit"
            )
            raise SourceError(message)
        items.append(Source(entry.name, entry))
    return tuple(items), tuple(warnings)


def _check_entry(path: Path, uid: int) -> os.stat_result:
    """Check ``path`` itself against the trust rules, and return its status."""
    try:
        info = path.lstat()
    except OSError as exc:
        raise SourceError(f"{path}: cannot be read: {exc}") from exc
    if stat.S_ISLNK(info.st_mode):
        raise SourceError(f"{path}: is a symbolic link, which is not allowed here")
    if info.st_uid != uid:
        raise SourceError(f"{path}: owned by uid {info.st_uid}, not by you (uid {uid})")
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise SourceError(f"{path}: group- or world-writable; run chmod go-w '{path}'")
    return info


def _check_tree(top: Path, uid: int) -> None:
    """Check ``top`` and everything under it against the trust rules."""
    if not top.is_symlink() and not top.is_dir():
        return
    pending = [top]
    while pending:
        path = pending.pop()
        if stat.S_ISDIR(_check_entry(path, uid).st_mode):
            try:
                pending.extend(sorted(path.iterdir(), reverse=True))
            except OSError as exc:
                raise SourceError(f"{path}: cannot be read: {exc}") from exc
