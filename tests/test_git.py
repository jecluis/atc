"""Running git, and what a failure looks like."""

from pathlib import Path

import pytest

from atc.errors import GitError
from atc.git import git_or_none, run_git
from tests.helpers import git_repo, make_source


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


def test_git_or_none_gives_none_on_failure(tmp_path: Path) -> None:
    assert git_or_none(["rev-parse", "HEAD"], cwd=tmp_path) is None
