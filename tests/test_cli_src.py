"""``atc src ls``: what the table says about each config source."""

from pathlib import Path

import pytest

from atc.cli import main
from atc.git import run_git
from atc.paths import Dirs
from tests.helpers import git_repo, make_source, unit


@pytest.fixture(autouse=True)
def wide_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMNS", "400")


def run_ls(capsys: pytest.CaptureFixture[str], *args: str) -> str:
    assert main(["src", "ls", *args]) == 0
    return capsys.readouterr().out


def row_of(out: str, name: str) -> list[str]:
    (line,) = (line for line in out.splitlines() if line.split()[:1] == [name])
    return line.split()


def track_upstream(source: Path, bare: Path) -> None:
    """Give ``source`` an upstream, a bare copy of it, that it has fetched."""
    _ = run_git(["clone", "-q", "--bare", str(source), str(bare)])
    _ = run_git(["remote", "add", "origin", str(bare)], cwd=source)
    _ = run_git(["fetch", "-q", "origin"], cwd=source)
    _ = run_git(["branch", "-q", "-u", "origin/main"], cwd=source)


def test_src_ls_lists_every_source(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = make_source(dirs.conf_d / "personal", {"conf/a.json": unit()})
    _ = make_source(dirs.conf_d / "bravo", {"assets/x.sh": "#!/bin/sh\n"})
    out = run_ls(capsys)
    assert "personal" in out
    assert "bravo" in out


def test_src_ls_counts_units_and_assets(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = make_source(
        dirs.conf_d / "personal",
        {
            "conf/a.json": unit(),
            "conf/langs/b.json": unit(),
            "assets/hooks/one.sh": "x",
            "assets/hooks/two.sh": "x",
            "assets/three.txt": "x",
            "skills/review/SKILL.md": "x",
        },
    )
    _, path, layout, units, assets = row_of(run_ls(capsys), "personal")[:5]
    assert path == str(dirs.conf_d / "personal")
    assert (layout, units, assets) == ("multi", "2", "3")


def test_src_ls_shows_a_source_without_a_name(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = make_source(dirs.conf_d, {"conf/a.json": unit()})
    _, path, layout = row_of(run_ls(capsys), "-")[:3]
    assert (path, layout) == (str(dirs.conf_d), "single")


def test_src_ls_shows_branch_and_commit_of_git_clones(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    source = git_repo(make_source(dirs.conf_d / "personal", {"conf/a.json": unit()}))
    commit = run_git(["rev-parse", "--short", "HEAD"], cwd=source)
    row = row_of(run_ls(capsys), "personal")
    assert row[5:7] == ["main", commit]


def test_src_ls_marks_uncommitted_changes(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    source = git_repo(make_source(dirs.conf_d / "personal", {"conf/a.json": unit()}))
    _ = (source / "conf" / "a.json").write_text("{}", encoding="utf-8")
    assert "dirty" in run_ls(capsys)


def test_src_ls_marks_new_files_as_changes(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    source = git_repo(make_source(dirs.conf_d / "personal", {"conf/a.json": unit()}))
    _ = make_source(source, {"assets/new.sh": "x"})
    assert "dirty" in run_ls(capsys)


def test_src_ls_does_not_mark_a_clean_clone(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = git_repo(make_source(dirs.conf_d / "personal", {"conf/a.json": unit()}))
    assert "dirty" not in run_ls(capsys)


def test_src_ls_ignores_changes_outside_the_source_directories(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    source = git_repo(make_source(dirs.conf_d / "personal", {"conf/a.json": unit()}))
    _ = make_source(source, {"notes/todo.md": "x"})
    assert "dirty" not in run_ls(capsys)


def test_src_ls_shows_how_far_a_clone_is_from_its_upstream(
    dirs: Dirs, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = git_repo(make_source(dirs.conf_d / "personal", {"conf/a.json": unit()}))
    track_upstream(source, tmp_path / "origin.git")
    other = tmp_path / "other"
    _ = run_git(["clone", "-q", str(tmp_path / "origin.git"), str(other)])
    _ = run_git(["commit", "-q", "--allow-empty", "-m", "elsewhere"], cwd=other)
    _ = run_git(["push", "-q"], cwd=other)
    _ = run_git(["fetch", "-q"], cwd=source)
    for message in ("one", "two"):
        _ = run_git(["commit", "-q", "--allow-empty", "-m", message], cwd=source)
    assert row_of(run_ls(capsys), "personal")[7:] == ["2/1"]


def test_src_ls_leaves_ahead_and_behind_blank_without_an_upstream(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = git_repo(make_source(dirs.conf_d / "personal", {"conf/a.json": unit()}))
    assert len(row_of(run_ls(capsys), "personal")) == 7


def test_src_ls_reports_a_trust_violation_as_a_config_error(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    source = make_source(dirs.conf_d / "personal", {"conf/a.json": unit()})
    (source / "conf" / "a.json").chmod(0o666)
    assert main(["src", "ls"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert str(source / "conf" / "a.json") in captured.err


def test_src_ls_warns_about_directories_that_are_not_sources(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = make_source(dirs.conf_d / "personal", {"conf/a.json": unit()})
    _ = make_source(dirs.conf_d / "stray", {"notes.md": "x"})
    assert main(["src", "ls"]) == 0
    captured = capsys.readouterr()
    assert "stray" in captured.err
    assert "warning:" in captured.err
    assert "stray" not in captured.out


def test_src_ls_says_how_to_add_a_source_when_there_is_none(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["src", "ls"]) == 1
    assert "git clone" in capsys.readouterr().err


def test_conf_d_option_reads_another_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    elsewhere = make_source(tmp_path / "elsewhere", {"conf/a.json": unit()})
    assert str(elsewhere) in run_ls(capsys, "--conf-d", str(elsewhere))


def test_src_ls_keeps_the_whole_path_in_a_narrow_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("COLUMNS", "80")
    deep = make_source(
        tmp_path / "a" / "deeply" / "nested" / "dir", {"conf/a.json": unit()}
    )
    header, _, *body = run_ls(capsys, "--conf-d", str(deep)).splitlines()[1:]
    start, end = header.index("Path"), header.index("Layout")
    assert "".join(line[start:end].strip() for line in body) == str(deep)
