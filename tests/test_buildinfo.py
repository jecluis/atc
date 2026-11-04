"""Which commit a build of atc records, and what ``atc --version`` makes of it."""

from pathlib import Path

import pytest

import hatch_build
from atc.buildinfo import COMMIT_FILE, built_commit, version_message
from atc.cli import main
from atc.git import run_git
from hatch_build import commit_of
from tests.helpers import git_repo


def repository(path: Path) -> Path:
    path.mkdir()
    _ = (path / "tracked").write_text("one\n", encoding="utf-8")
    return git_repo(path)


def head(path: Path) -> str:
    return run_git(["rev-parse", "HEAD"], cwd=path).strip()


def test_a_clean_checkout_records_its_commit(tmp_path: Path) -> None:
    root = repository(tmp_path / "repo")

    assert commit_of(root) == head(root)[:12]


def test_a_changed_tracked_file_marks_the_commit_dirty(tmp_path: Path) -> None:
    root = repository(tmp_path / "repo")
    _ = (root / "tracked").write_text("two\n", encoding="utf-8")

    assert commit_of(root) == f"{head(root)[:12]}-dirty"


def test_an_untracked_file_does_not_mark_the_commit_dirty(tmp_path: Path) -> None:
    root = repository(tmp_path / "repo")
    _ = (root / "untracked").write_text("x\n", encoding="utf-8")

    assert commit_of(root) == head(root)[:12]


def test_a_tag_does_not_replace_the_commit(tmp_path: Path) -> None:
    root = repository(tmp_path / "repo")
    _ = run_git(["tag", "v1"], cwd=root)

    assert commit_of(root) == head(root)[:12]


def test_the_git_variables_of_the_caller_do_not_change_the_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = repository(tmp_path / "repo")
    other = tmp_path / "other"
    other.mkdir()
    _ = (other / "tracked").write_text("another\n", encoding="utf-8")
    _ = git_repo(other)
    expected = head(root)[:12]
    assert head(other)[:12] != expected
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))

    assert commit_of(root) == expected


def test_a_directory_with_no_repository_records_nothing(tmp_path: Path) -> None:
    assert commit_of(tmp_path) is None


def test_a_tree_inside_another_repository_records_nothing(tmp_path: Path) -> None:
    root = repository(tmp_path / "repo")
    inner = root / "unpacked"
    inner.mkdir()

    assert commit_of(inner) is None


def test_a_repository_without_commits_records_nothing(tmp_path: Path) -> None:
    _ = run_git(["init", "-q"], cwd=tmp_path)

    assert commit_of(tmp_path) is None


def test_the_commit_is_read_from_the_file_a_build_leaves(tmp_path: Path) -> None:
    _ = (tmp_path / COMMIT_FILE).write_text("abc123\n", encoding="utf-8")

    assert built_commit(tmp_path) == "abc123"


def test_no_commit_is_known_without_the_file(tmp_path: Path) -> None:
    assert built_commit(tmp_path) is None


def test_an_empty_file_gives_no_commit(tmp_path: Path) -> None:
    _ = (tmp_path / COMMIT_FILE).write_text("\n", encoding="utf-8")

    assert built_commit(tmp_path) is None


@pytest.mark.parametrize("text", ["%s\n", "%(prog)s\n", "\x1b[31m\n", "abc def\n"])
def test_a_file_that_holds_no_commit_gives_no_commit(tmp_path: Path, text: str) -> None:
    _ = (tmp_path / COMMIT_FILE).write_text(text, encoding="utf-8")

    assert built_commit(tmp_path) is None


def test_the_build_and_the_package_agree_on_the_file() -> None:
    assert hatch_build.COMMIT_FILE == COMMIT_FILE


def test_the_version_message_names_the_commit(tmp_path: Path) -> None:
    _ = (tmp_path / COMMIT_FILE).write_text("abc123-dirty\n", encoding="utf-8")

    message = version_message(tmp_path)

    assert message % {"prog": "atc", "version": "1.2"} == (
        "atc, version 1.2 (abc123-dirty)"
    )


def test_the_version_message_says_when_no_commit_is_recorded(tmp_path: Path) -> None:
    message = version_message(tmp_path)

    assert message % {"prog": "atc", "version": "1.2"} == (
        "atc, version 1.2 (commit not recorded)"
    )


def test_a_checkout_that_is_not_installed_shows_no_commit(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--version"]) == 0

    assert capsys.readouterr().out.endswith("(commit not recorded)\n")
