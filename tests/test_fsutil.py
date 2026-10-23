"""Atomic writes and backups."""

import os
import stat
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from atc.fsutil import atomic_write, backup

START = datetime(2026, 10, 23, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def work(tmp_path: Path) -> Path:
    directory = tmp_path / "work"
    directory.mkdir()
    return directory


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_atomic_write_replaces_the_text(work: Path) -> None:
    target = work / "settings.json"
    _ = target.write_text("old")

    atomic_write(target, "new\n")

    assert target.read_text() == "new\n"
    assert list(work.iterdir()) == [target]


def test_atomic_write_keeps_the_mode(work: Path) -> None:
    target = work / "settings.json"
    _ = target.write_text("old")
    target.chmod(0o644)

    atomic_write(target, "new")

    assert mode(target) == 0o644


def test_atomic_write_makes_a_new_file_private_by_default(work: Path) -> None:
    target = work / "settings.json"

    atomic_write(target, "new")

    assert mode(target) == 0o600
    atomic_write(work / "other.json", "new", default_mode=0o640)
    assert mode(work / "other.json") == 0o640


def test_atomic_write_leaves_no_temporary_file_on_failure(work: Path) -> None:
    target = work / "settings.json"
    target.mkdir()

    with pytest.raises(OSError, match=r"settings\.json"):
        atomic_write(target, "new")

    assert list(work.iterdir()) == [target]


def test_a_backup_is_a_private_copy_named_for_the_time(work: Path) -> None:
    source = work / "settings.json"
    _ = source.write_bytes(b'{"a": 1}\n\xff')
    backups = work / "state" / "backups"

    copy = backup(source, backups, START)

    assert copy == backups / "20261023T120000Z-settings.json"
    assert copy.read_bytes() == source.read_bytes()
    assert mode(copy) == 0o600


def test_a_backup_names_its_time_in_utc(work: Path) -> None:
    source = work / "settings.json"
    _ = source.write_text("x")
    cest = datetime(2026, 10, 23, 14, 0, 0, tzinfo=timezone(timedelta(hours=2)))

    copy = backup(source, work / "b", cest)

    assert copy.name == "20261023T120000Z-settings.json"


def test_backups_in_the_same_second_do_not_overwrite_each_other(
    work: Path,
) -> None:
    source = work / "settings.json"
    backups = work / "b"
    names: list[str] = []
    for text in ("one", "two", "three"):
        _ = source.write_text(text)
        names.append(backup(source, backups, START).name)

    assert names == [
        "20261023T120000Z-settings.json",
        "20261023T120000Z.1-settings.json",
        "20261023T120000Z.2-settings.json",
    ]
    assert (backups / names[1]).read_text() == "two"


def test_backups_keep_the_newest_ten_per_file_name(work: Path) -> None:
    settings = work / "settings.json"
    instructions = work / "CLAUDE.md"
    _ = settings.write_text("x")
    _ = instructions.write_text("x")
    backups = work / "b"

    _ = backup(instructions, backups, START)
    for second in range(12):
        _ = backup(settings, backups, START + timedelta(seconds=second))

    kept = sorted(p.name for p in backups.iterdir())
    assert len(kept) == 11
    assert "20261023T120000Z-CLAUDE.md" in kept
    assert next(n for n in kept if n.endswith("settings.json")) == (
        "20261023T120002Z-settings.json"
    )


def test_a_collision_suffix_counts_as_newer_when_pruning(work: Path) -> None:
    source = work / "settings.json"
    _ = source.write_text("x")
    backups = work / "b"

    for _round in range(3):
        _ = backup(source, backups, START, keep=2)

    assert sorted(p.name for p in backups.iterdir()) == [
        "20261023T120000Z.1-settings.json",
        "20261023T120000Z.2-settings.json",
    ]


def test_pruning_never_takes_a_file_whose_name_ends_in_digits_for_another(
    work: Path,
) -> None:
    rust = work / "rust"
    edition = work / "rust-2024"
    _ = rust.write_text("x")
    _ = edition.write_text("x")
    backups = work / "b"

    for _round in range(3):
        _ = backup(edition, backups, START, keep=2)
    for _round in range(3):
        _ = backup(rust, backups, START, keep=2)

    assert sorted(p.name for p in backups.iterdir()) == [
        "20261023T120000Z.1-rust",
        "20261023T120000Z.1-rust-2024",
        "20261023T120000Z.2-rust",
        "20261023T120000Z.2-rust-2024",
    ]


def test_the_backup_just_made_is_never_pruned(work: Path) -> None:
    source = work / "settings.json"
    _ = source.write_text("x")
    backups = work / "b"
    for second in range(10):
        _ = backup(source, backups, START + timedelta(seconds=second))

    earlier = backup(source, backups, START - timedelta(days=1))

    assert earlier.exists()
    kept = sorted(p.name for p in backups.iterdir())
    assert len(kept) == 10
    assert earlier.name in kept
    assert "20261023T120000Z-settings.json" not in kept


def test_backup_directories_are_private_whatever_the_umask(work: Path) -> None:
    source = work / "settings.json"
    _ = source.write_text("x")
    backups = work / "state" / "sync" / "personal" / "backups"
    previous = os.umask(0o002)
    try:
        _ = backup(source, backups, START)
    finally:
        _ = os.umask(previous)

    personal = backups.parent
    for directory in (backups, personal, personal.parent, personal.parent.parent):
        assert mode(directory) == 0o700, directory
