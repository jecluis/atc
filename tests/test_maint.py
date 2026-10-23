"""``atc maint``: keep the guarded repositories packed."""

import configparser
import os
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from atc.cli import main
from atc.errors import ConfigError, GitError, RegistryError
from atc.exitcodes import CONFIG, OK
from atc.git import run_git
from atc.guard import (
    maint,
    maint_repo,
    open_session_lock,
    register_repo,
    registered_repos,
    repo_key,
)
from atc.paths import Dirs
from atc.registry import load_registry
from tests.conftest import RegistryWriter
from tests.helpers import MINIMAL_REGISTRY, git_repo, is_locked

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
HOUR = 3600


def make_repo(parent: Path, name: str) -> Path:
    """Make a repository with one commit, and return its git directory."""
    path = parent / name
    path.mkdir()
    _ = (path / "file.txt").write_text(name, encoding="utf-8")
    _ = git_repo(path)
    return (path / ".git").resolve()


def loose_objects(common: Path) -> int:
    report = run_git(["count-objects", "-v"], env={"GIT_DIR": str(common)})
    for line in report.splitlines():
        key, _, value = line.partition(": ")
        if key == "count":
            return int(value)
    raise AssertionError(report)


def lock_file(dirs: Dirs, common: Path) -> Path:
    return dirs.runtime_dir / f"{repo_key(common)}.lock"


def stamp_file(dirs: Dirs, common: Path) -> Path:
    return dirs.state_dir / f"{repo_key(common)}.gc"


def set_age(path: Path, age: timedelta, *, now: datetime = NOW) -> None:
    moment = (now - age).timestamp()
    os.utime(path, (moment, moment))


@pytest.fixture
def trace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Make git record the commands it runs, which `gc_runs` reads."""
    path = tmp_path / "git-trace"
    monkeypatch.setenv("GIT_TRACE", str(path))
    return path


def gc_runs(trace: Path) -> int:
    if not trace.exists():
        return 0
    text = trace.read_text(encoding="utf-8")
    return len(re.findall(r"built-in: git gc --quiet$", text, re.MULTILINE))


def test_loose_objects_get_packed(dirs: Dirs, tmp_path: Path) -> None:
    common = make_repo(tmp_path, "one")
    assert loose_objects(common) > 0

    maint_repo(dirs, common, gc_hours=24, now=NOW)

    assert loose_objects(common) == 0
    _ = run_git(["fsck", "--strict"], env={"GIT_DIR": str(common)})


def test_refs_are_packed_only_when_no_session_holds_the_lock(
    dirs: Dirs, tmp_path: Path, trace: Path
) -> None:
    common = make_repo(tmp_path, "one")
    branch = common / "refs" / "heads" / "main"
    descriptor, warning = open_session_lock(dirs, common, timeout=0)
    assert warning is None
    assert descriptor is not None
    try:
        maint_repo(dirs, common, gc_hours=24, now=NOW)

        assert branch.exists()
        assert not (common / "packed-refs").exists()
        assert gc_runs(trace) == 0
        assert not stamp_file(dirs, common).exists()
    finally:
        os.close(descriptor)

    maint_repo(dirs, common, gc_hours=24, now=NOW)

    assert not branch.exists()
    assert "refs/heads/main" in (common / "packed-refs").read_text(encoding="utf-8")
    assert gc_runs(trace) == 1
    assert not is_locked(lock_file(dirs, common))


def test_gc_runs_only_after_gc_hours(dirs: Dirs, tmp_path: Path, trace: Path) -> None:
    common = make_repo(tmp_path, "one")
    stamp = stamp_file(dirs, common)

    maint_repo(dirs, common, gc_hours=24, now=NOW)

    assert gc_runs(trace) == 1
    assert stamp.stat().st_mtime == NOW.timestamp()

    set_age(stamp, timedelta(hours=23))
    maint_repo(dirs, common, gc_hours=24, now=NOW)

    assert gc_runs(trace) == 1
    assert stamp.stat().st_mtime == (NOW - timedelta(hours=23)).timestamp()

    set_age(stamp, timedelta(hours=25))
    maint_repo(dirs, common, gc_hours=24, now=NOW)

    assert gc_runs(trace) == 2
    assert stamp.stat().st_mtime == NOW.timestamp()


def test_the_stamp_and_the_lock_are_private(dirs: Dirs, tmp_path: Path) -> None:
    common = make_repo(tmp_path, "one")

    maint_repo(dirs, common, gc_hours=24, now=NOW)

    for path in (stamp_file(dirs, common), lock_file(dirs, common)):
        assert path.stat().st_mode & 0o077 == 0
    for directory in (dirs.state_dir, dirs.runtime_dir):
        assert directory.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("gc_hours", ["0", "-3", "'6'", "1.5", "true", "null"])
def test_gc_hours_defaults_to_24_and_must_be_positive(
    write_registry: RegistryWriter, home: Path, gc_hours: str
) -> None:
    assert load_registry(write_registry(MINIMAL_REGISTRY), home).gc_hours == 24
    text = MINIMAL_REGISTRY + "    maint: {}\n"
    assert load_registry(write_registry(text), home).gc_hours == 24
    text = MINIMAL_REGISTRY + "    maint:\n      gc-hours: 1\n"
    assert load_registry(write_registry(text), home).gc_hours == 1
    text = MINIMAL_REGISTRY + "    maint:\n      gc-hours: 168\n"
    assert load_registry(write_registry(text), home).gc_hours == 168

    text = MINIMAL_REGISTRY + f"    maint:\n      gc-hours: {gc_hours}\n"
    with pytest.raises(RegistryError, match="/maint/gc-hours"):
        _ = load_registry(write_registry(text), home)


def test_an_unknown_maint_key_is_refused(
    write_registry: RegistryWriter, home: Path
) -> None:
    text = MINIMAL_REGISTRY + "    maint:\n      gc-hour: 6\n"

    with pytest.raises(RegistryError, match="/maint/gc-hour"):
        _ = load_registry(write_registry(text), home)


def test_a_repository_that_no_longer_exists_is_skipped(
    dirs: Dirs, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gone = tmp_path / "gone" / ".git"
    kept = make_repo(tmp_path, "kept")
    register_repo(dirs, gone)
    register_repo(dirs, kept)

    failures = maint(dirs, gc_hours=24, now=NOW)

    assert failures == []
    assert f"warning: skipping {gone}" in capsys.readouterr().err
    assert loose_objects(kept) == 0


def test_a_failure_is_reported_and_the_rest_continue(
    dirs: Dirs, tmp_path: Path
) -> None:
    broken = tmp_path / "broken" / ".git"
    (broken / "objects").mkdir(parents=True)
    first = make_repo(tmp_path, "first")
    last = make_repo(tmp_path, "last")
    for common in (first, broken, last):
        register_repo(dirs, common)

    failures = maint(dirs, gc_hours=24, now=NOW)

    assert len(failures) == 1
    assert str(broken) in failures[0]
    assert "git repack" in failures[0]
    assert loose_objects(first) == 0
    assert loose_objects(last) == 0


def test_a_failing_command_raises_and_releases_the_lock(
    dirs: Dirs, tmp_path: Path
) -> None:
    common = make_repo(tmp_path, "one")
    _ = (common / "packed-refs.lock").write_text("", encoding="utf-8")

    with pytest.raises(GitError, match="pack-refs"):
        maint_repo(dirs, common, gc_hours=24, now=NOW)

    assert not is_locked(lock_file(dirs, common))
    assert not stamp_file(dirs, common).exists()


def test_the_list_gives_its_repositories_in_order(dirs: Dirs) -> None:
    assert registered_repos(dirs) == []

    dirs.state_dir.mkdir(parents=True)
    _ = (dirs.state_dir / "repos").write_text("/b/.git\n\n/a/.git\n", encoding="utf-8")

    assert registered_repos(dirs) == [Path("/b/.git"), Path("/a/.git")]


def test_a_list_that_cannot_be_read_is_a_configuration_error(dirs: Dirs) -> None:
    (dirs.state_dir / "repos").mkdir(parents=True)

    with pytest.raises(ConfigError, match=r"repos: cannot be read"):
        _ = registered_repos(dirs)


def test_the_command_packs_what_the_list_names(
    dirs: Dirs,
    write_registry: RegistryWriter,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _ = write_registry(MINIMAL_REGISTRY)
    common = make_repo(tmp_path, "one")
    register_repo(dirs, common)

    status = main(["maint"])

    assert status == OK
    assert loose_objects(common) == 0
    assert capsys.readouterr().err == ""


def test_the_command_exits_1_when_a_repository_failed(
    dirs: Dirs,
    write_registry: RegistryWriter,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _ = write_registry(MINIMAL_REGISTRY)
    broken = tmp_path / "broken" / ".git"
    (broken / "objects").mkdir(parents=True)
    good = make_repo(tmp_path, "good")
    register_repo(dirs, broken)
    register_repo(dirs, good)

    status = main(["maint"])

    assert status == CONFIG
    assert f"error: maintenance failed for {broken}" in capsys.readouterr().err
    assert loose_objects(good) == 0


def test_the_command_takes_gc_hours_from_the_registry(
    dirs: Dirs,
    write_registry: RegistryWriter,
    tmp_path: Path,
    trace: Path,
) -> None:
    _ = write_registry(MINIMAL_REGISTRY + "    maint:\n      gc-hours: 2\n")
    common = make_repo(tmp_path, "one")
    register_repo(dirs, common)
    stamp = stamp_file(dirs, common)
    stamp.touch()
    set_age(stamp, timedelta(hours=3), now=datetime.fromtimestamp(time.time(), UTC))

    assert main(["maint"]) == OK

    assert gc_runs(trace) == 1


def test_the_command_does_not_read_the_config_sources(
    dirs: Dirs, write_registry: RegistryWriter
) -> None:
    _ = write_registry(MINIMAL_REGISTRY)
    assert not dirs.conf_d.exists()

    assert main(["maint"]) == OK


def test_the_systemd_units_run_atc_maint_every_ten_minutes() -> None:
    directory = Path(__file__).parents[1] / "contrib" / "systemd"
    service = configparser.ConfigParser(interpolation=None)
    timer = configparser.ConfigParser(interpolation=None)
    _ = service.read(directory / "atc-maint.service", encoding="utf-8")
    _ = timer.read(directory / "atc-maint.timer", encoding="utf-8")

    assert dict(service["Service"]) == {
        "type": "oneshot",
        "execstart": "%h/.local/bin/atc maint",
        "nice": "10",
        "ioschedulingclass": "idle",
    }
    assert dict(timer["Timer"]) == {
        "onbootsec": "5min",
        "onunitactivesec": "10min",
    }
    assert dict(timer["Install"]) == {"wantedby": "timers.target"}
