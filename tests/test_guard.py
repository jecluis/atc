"""The guard: packed history, gc, the repository list, the session lock, snapshots."""

import fcntl
import os
import stat
import subprocess
import sys
import threading
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from atc.errors import ConfigError
from atc.guard import (
    open_session_lock,
    protected_paths,
    register_repo,
    repo_key,
    run_command,
    take_snapshot,
    with_gc_disabled,
)
from atc.paths import Dirs
from tests.helpers import is_locked


@pytest.fixture
def common(tmp_path: Path) -> Path:
    path = tmp_path / "repo" / ".git"
    path.mkdir(parents=True)
    return path


def lock_file(dirs: Dirs, common: Path) -> Path:
    return dirs.runtime_dir / f"{repo_key(common)}.lock"


def open_descriptors() -> int:
    return len(os.listdir("/proc/self/fd"))


def test_only_existing_paths_are_protected(common: Path) -> None:
    pack = common / "objects" / "pack"
    refs = common / "packed-refs"
    assert protected_paths(common) == ()

    pack.mkdir(parents=True)
    assert protected_paths(common) == (pack,)

    _ = refs.write_text("", encoding="utf-8")
    assert protected_paths(common) == (pack, refs)

    pack.rmdir()
    assert protected_paths(common) == (refs,)


@pytest.mark.parametrize(
    ("count", "start"),
    [(None, 0), ("", 0), ("0", 0), ("1", 1), ("+1", 1), (" 1", 1), ("\t02", 2)],
)
def test_gc_entries_are_added_after_existing_ones(
    count: str | None, start: int
) -> None:
    existing = {
        f"GIT_CONFIG_{kind}_{index}": f"{kind.lower()}{index}"
        for index in range(start)
        for kind in ("KEY", "VALUE")
    }
    env = {"PATH": "/bin", **existing}
    if count is not None:
        env["GIT_CONFIG_COUNT"] = count

    result = with_gc_disabled(env)

    assert result == {
        **env,
        "GIT_CONFIG_COUNT": str(start + 2),
        f"GIT_CONFIG_KEY_{start}": "gc.auto",
        f"GIT_CONFIG_VALUE_{start}": "0",
        f"GIT_CONFIG_KEY_{start + 1}": "maintenance.auto",
        f"GIT_CONFIG_VALUE_{start + 1}": "false",
    }
    assert env.get("GIT_CONFIG_COUNT") == count


@pytest.mark.parametrize("count", ["x", "-1", "1.5", "0x1", "+", " ", "1 ", "+ 1"])
def test_a_count_git_refuses_is_a_config_error(count: str) -> None:
    with pytest.raises(ConfigError, match=r"GIT_CONFIG_COUNT.*not a number"):
        _ = with_gc_disabled({"GIT_CONFIG_COUNT": count})


def test_the_key_is_the_start_of_the_sha256_of_the_path() -> None:
    assert repo_key(Path("/x/.git")) == "e67df524a033e9f9"


def test_registering_twice_keeps_one_line(dirs: Dirs, common: Path) -> None:
    other = common.parent.parent / "other" / ".git"

    register_repo(dirs, common)
    register_repo(dirs, common)
    register_repo(dirs, other)
    register_repo(dirs, common)

    repos = dirs.state_dir / "repos"
    assert repos.read_text(encoding="utf-8") == f"{common}\n{other}\n"


def test_a_path_with_a_line_break_character_is_listed_once(
    dirs: Dirs, common: Path
) -> None:
    odd = common.parent / "a\x0cb" / ".git"

    register_repo(dirs, odd)
    register_repo(dirs, odd)

    assert (dirs.state_dir / "repos").read_text(encoding="utf-8") == f"{odd}\n"


def test_a_list_that_lacks_a_final_newline_keeps_its_last_entry(
    dirs: Dirs, common: Path
) -> None:
    dirs.state_dir.mkdir(parents=True)
    repos = dirs.state_dir / "repos"
    _ = repos.write_text("/old/.git", encoding="utf-8")

    register_repo(dirs, common)

    assert repos.read_text(encoding="utf-8") == f"/old/.git\n{common}\n"


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anything")
def test_a_read_only_list_that_has_the_repository_is_only_read(
    dirs: Dirs, common: Path
) -> None:
    other = common.parent.parent / "other" / ".git"
    register_repo(dirs, common)
    repos = dirs.state_dir / "repos"
    repos.chmod(0o400)
    dirs.state_dir.chmod(0o500)
    try:
        register_repo(dirs, common)

        with pytest.raises(PermissionError):
            register_repo(dirs, other)
    finally:
        dirs.state_dir.chmod(0o700)
        repos.chmod(0o600)

    assert repos.read_text(encoding="utf-8") == f"{common}\n"


def test_the_state_directory_and_the_list_are_private(dirs: Dirs, common: Path) -> None:
    register_repo(dirs, common)

    for directory in (dirs.state_dir, dirs.state_dir.parent):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    repos = dirs.state_dir / "repos"
    assert stat.S_IMODE(repos.stat().st_mode) & 0o077 == 0


def test_a_shared_lock_blocks_an_exclusive_one(dirs: Dirs, common: Path) -> None:
    descriptor, warning = open_session_lock(dirs, common)

    assert warning is None
    assert descriptor is not None
    try:
        assert os.get_inheritable(descriptor)
        assert stat.S_IMODE(dirs.runtime_dir.stat().st_mode) == 0o700
        assert is_locked(lock_file(dirs, common))
        second, second_warning = open_session_lock(dirs, common, timeout=0)
        assert second is not None
        assert second_warning is None
        os.close(second)
    finally:
        os.close(descriptor)
    assert not is_locked(lock_file(dirs, common))


def test_a_lock_file_that_cannot_be_written_still_takes_the_lock(
    dirs: Dirs, common: Path
) -> None:
    path = lock_file(dirs, common)
    dirs.runtime_dir.mkdir(parents=True)
    _ = path.write_text("", encoding="utf-8")
    path.chmod(0o400)

    descriptor, warning = open_session_lock(dirs, common)

    assert warning is None
    assert descriptor is not None
    os.close(descriptor)


def test_the_lock_outlives_exec_in_a_child(dirs: Dirs, common: Path) -> None:
    descriptor, _warning = open_session_lock(dirs, common)
    assert descriptor is not None
    child = subprocess.Popen(["sleep", "2"], close_fds=False)  # noqa: S607
    try:
        os.close(descriptor)
        assert is_locked(lock_file(dirs, common))
    finally:
        child.kill()
        _ = child.wait()
    assert not is_locked(lock_file(dirs, common))


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write to any directory")
def test_a_runtime_dir_that_cannot_be_created_warns_and_continues(
    dirs: Dirs, common: Path, tmp_path: Path
) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    blocked = replace(dirs, runtime_dir=locked / "run" / "atc")
    try:
        descriptor, warning = open_session_lock(blocked, common)
    finally:
        locked.chmod(0o700)

    assert descriptor is None
    assert warning is not None
    assert str(blocked.runtime_dir) in warning
    assert "without it" in warning


def test_a_lock_that_stays_exclusive_gives_up_with_a_warning(
    dirs: Dirs, common: Path
) -> None:
    path = lock_file(dirs, common)
    dirs.runtime_dir.mkdir(parents=True)
    holder = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
    before = open_descriptors()
    try:
        fcntl.flock(holder, fcntl.LOCK_EX)

        descriptor, warning = open_session_lock(dirs, common, timeout=0.2)

        assert open_descriptors() == before
    finally:
        os.close(holder)
    assert descriptor is None
    assert warning is not None
    assert "maintenance" in warning
    assert "without the session lock" in warning


def test_the_lock_waits_for_maintenance_to_finish(dirs: Dirs, common: Path) -> None:
    path = lock_file(dirs, common)
    dirs.runtime_dir.mkdir(parents=True)
    holder = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
    fcntl.flock(holder, fcntl.LOCK_EX)
    release = threading.Timer(0.2, os.close, [holder])
    release.start()
    try:
        descriptor, warning = open_session_lock(dirs, common, timeout=10)
    finally:
        release.join()

    assert warning is None
    assert descriptor is not None
    os.close(descriptor)


def test_a_snapshot_runs_snapper_for_the_config(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def run(command: Sequence[str]) -> int:
        commands.append(list(command))
        return 0

    assert take_snapshot("bravo", tmp_path, run=run) is None

    assert commands == [
        [
            "snapper",
            "-c",
            "bravo",
            "create",
            "--cleanup-algorithm",
            "number",
            "--description",
            f"atc: {tmp_path}",
        ]
    ]


def test_a_failed_snapshot_warns_and_continues(tmp_path: Path) -> None:
    def refuses(_command: Sequence[str]) -> int:
        return 1

    def missing(_command: Sequence[str]) -> int:
        raise FileNotFoundError(2, "No such file or directory", "snapper")

    status = take_snapshot("bravo", tmp_path, run=refuses)
    absent = take_snapshot("bravo", tmp_path, run=missing)

    assert status is not None
    assert "snapper" in status
    assert "exit status 1" in status
    assert "starting anyway" in status
    assert absent is not None
    assert "No such file or directory" in absent
    assert "starting anyway" in absent


def test_the_default_runner_gives_the_exit_status() -> None:
    assert run_command([sys.executable, "-c", "raise SystemExit(3)"]) == 3
    assert run_command([sys.executable, "-c", "pass"]) == 0
