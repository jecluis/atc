"""Running git, and what a failure looks like."""

from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from atc import git
from atc.errors import GitError
from atc.git import common_dir, git_or_none, run_git
from tests.helpers import git_repo, make_source

type Option = Path | Mapping[str, str] | None


def test_run_git_returns_stripped_output(tmp_path: Path) -> None:
    _ = git_repo(make_source(tmp_path, {"a": "x"}))
    assert run_git(["branch", "--show-current"], cwd=tmp_path) == "main"


def test_run_git_adds_to_the_environment(tmp_path: Path) -> None:
    _ = git_repo(make_source(tmp_path, {"a": "x"}))
    _ = run_git(
        ["commit", "-q", "--allow-empty", "-m", "x"],
        cwd=tmp_path,
        env={"GIT_AUTHOR_NAME": "Other"},
    )
    assert run_git(["log", "-1", "--format=%an,%cn"], cwd=tmp_path) == "Other,Test"


def test_run_git_failure_names_the_command(tmp_path: Path) -> None:
    with pytest.raises(GitError, match=r"git rev-parse HEAD"):
        _ = run_git(["rev-parse", "HEAD"], cwd=tmp_path)


def test_a_missing_directory_is_named_as_a_plain_path(tmp_path: Path) -> None:
    gone = tmp_path / "gone"

    with pytest.raises(GitError) as caught:
        _ = run_git(["status"], cwd=gone)

    assert str(caught.value) == f"cannot run git in {gone}: no such directory"


def test_git_that_cannot_be_found_is_named_plainly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))

    with pytest.raises(GitError) as caught:
        _ = run_git(["status"], cwd=tmp_path)

    assert str(caught.value) == "cannot run git: No such file or directory"


def test_git_or_none_gives_none_on_failure(tmp_path: Path) -> None:
    assert git_or_none(["rev-parse", "HEAD"], cwd=tmp_path) is None


def test_common_dir_is_none_outside_a_repository(tmp_path: Path) -> None:
    assert common_dir(tmp_path) is None


def test_common_dir_is_the_git_directory_from_any_subdirectory(tmp_path: Path) -> None:
    _ = git_repo(make_source(tmp_path / "repo", {"sub/a": "x"}))

    assert common_dir(tmp_path / "repo" / "sub") == (tmp_path / "repo" / ".git")


def test_a_linked_worktree_has_the_common_dir_of_its_repository(
    tmp_path: Path,
) -> None:
    repo = git_repo(make_source(tmp_path / "repo", {"a": "x"}))
    wt = tmp_path / "wt"
    _ = run_git(["worktree", "add", "-q", "-b", "feature", str(wt)], cwd=repo)

    assert common_dir(wt) == repo / ".git"


def test_common_dir_follows_symbolic_links(tmp_path: Path) -> None:
    repo = git_repo(make_source(tmp_path / "repo", {"a": "x"}))
    (tmp_path / "link").symlink_to(repo)

    assert common_dir(tmp_path / "link") == (repo / ".git").resolve()


def test_common_dir_of_a_missing_directory_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(GitError, match="cannot run git"):
        _ = common_dir(tmp_path / "absent")


def test_common_dir_reports_a_failure_that_is_not_a_missing_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = git_repo(make_source(tmp_path / "repo", {"a": "x"}))
    broken = tmp_path / "gitconfig"
    _ = broken.write_text("[core\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(broken))

    with pytest.raises(GitError, match="bad config"):
        _ = common_dir(repo)


def test_common_dir_reports_a_repository_git_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(_args: Sequence[str], **_options: Option) -> str:
        raise GitError("fatal: detected dubious ownership in repository")

    monkeypatch.setattr(git, "run_git", refuse)

    with pytest.raises(GitError, match="dubious ownership"):
        _ = common_dir(tmp_path)


def test_common_dir_reads_the_failure_in_the_c_locale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[Option] = []

    def fail(_args: Sequence[str], **options: Option) -> str:
        asked.append(options.get("env"))
        raise GitError("fatal: not a git repository (or any of the parent dirs)")

    monkeypatch.setattr(git, "run_git", fail)

    assert common_dir(tmp_path) is None
    assert asked == [{"LC_ALL": "C"}]
