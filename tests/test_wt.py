"""``atc wt``: add, list and remove the worktrees of the current project."""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import NoReturn, Protocol

import pytest

from atc import launch
from atc.cli import main
from atc.exitcodes import CONFIG, OK, USAGE
from atc.git import git_or_none, run_git
from atc.launch import LaunchPlan
from atc.paths import Dirs
from atc.worktree import Worktree, list_worktrees, slug
from tests.conftest import ConfigWriter
from tests.helpers import StopError, git_repo, make_source, unit

REGISTRY = """\
version: 1
default-account: personal
agents: {{claude: {{bin: {agent}}}}}
accounts:
  personal: {{agent: claude, dir: ~/.claude}}
projects:
  bar:
    repo: {repo}
{more}"""


class Registrar(Protocol):
    def __call__(
        self, *, with_worktrees: bool = True, branch_from: str | None = None
    ) -> None: ...


@pytest.fixture(autouse=True)
def never_start_a_process(monkeypatch: pytest.MonkeyPatch) -> None:
    def execvpe(file: str, args: list[str], _env: Mapping[str, str]) -> NoReturn:
        raise AssertionError(f"a test started {file} {args}")

    monkeypatch.setattr(os, "execvpe", execvpe)


@pytest.fixture(autouse=True)
def wide_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMNS", "400")


@pytest.fixture
def executed(monkeypatch: pytest.MonkeyPatch, dirs: Dirs) -> list[LaunchPlan]:
    """Stand in for ``execute``: record the plan and stop the command."""
    plans: list[LaunchPlan] = []

    def record(plan: LaunchPlan, given: Dirs) -> NoReturn:
        assert given == dirs
        plans.append(plan)
        raise StopError

    monkeypatch.setattr(launch, "execute", record)
    return plans


@pytest.fixture
def agent(tmp_path: Path) -> Path:
    """Make an executable that stands for the agent. No test runs it."""
    path = make_source(tmp_path / "bin", {"claude": "#!/bin/sh\n"}) / "claude"
    path.chmod(0o755)
    return path


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return git_repo(make_source(tmp_path / "bar", {"README": "x"}))


@pytest.fixture
def worktrees(tmp_path: Path) -> Path:
    """Give the directory ``bar`` makes its worktrees in, which does not exist yet."""
    return tmp_path / "wt"


@pytest.fixture
def register(
    configure: ConfigWriter,
    agent: Path,
    repo: Path,
    worktrees: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Registrar:
    """Register ``bar``, as the registry says, and work in its repository."""

    def write(*, with_worktrees: bool = True, branch_from: str | None = None) -> None:
        more = f"    worktrees: {worktrees}\n" if with_worktrees else ""
        if branch_from is not None:
            more += f"    branch-from: {branch_from}\n"
        configure(
            {"conf/accounts/personal.json": unit({})},
            REGISTRY.format(agent=agent, repo=repo, more=more),
        )
        monkeypatch.chdir(repo)

    return write


def commit(repo: Path, message: str) -> str:
    """Add an empty commit to the branch ``repo`` is on, and return its id."""
    _ = run_git(["commit", "-q", "--allow-empty", "-m", message], cwd=repo)
    return run_git(["rev-parse", "HEAD"], cwd=repo)


def tree_at(repo: Path, path: Path) -> Worktree:
    """Find the worktree git lists at ``path``."""
    (found,) = (tree for tree in list_worktrees(repo) if tree.path == path)
    return found


def has_branch(repo: Path, branch: str) -> bool:
    return (
        git_or_none(
            ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=repo
        )
        is not None
    )


@pytest.mark.parametrize(
    ("branch", "expected"),
    [
        ("feature/x", "feature--x"),
        ("Feature X", "feature-x"),
        ("feat/a b", "feat--a-b"),
        ("v1.2_rc", "v1.2_rc"),
        ("fix#12", "fix--12"),
    ],
)
def test_slug(branch: str, expected: str) -> None:
    assert slug(branch) == expected


def test_new_uses_an_existing_branch(
    repo: Path,
    worktrees: Path,
    register: Registrar,
    executed: list[LaunchPlan],
    capsys: pytest.CaptureFixture[str],
) -> None:
    register()
    old = run_git(["rev-parse", "HEAD"], cwd=repo)
    _ = run_git(["branch", "existing"], cwd=repo)
    _ = commit(repo, "later")

    status = main(["wt", "new", "existing"])

    captured = capsys.readouterr()
    path = worktrees / "existing"
    assert status == OK
    assert captured.err == ""
    assert str(path) in captured.out
    assert tree_at(repo, path) == Worktree(path=path, branch="existing", head=old)
    assert executed == []


def test_new_creates_a_branch_from_branch_from(
    repo: Path, worktrees: Path, register: Registrar
) -> None:
    register(branch_from="develop")
    _ = run_git(["branch", "develop"], cwd=repo)
    develop = run_git(["rev-parse", "develop"], cwd=repo)
    _ = commit(repo, "later")

    assert main(["wt", "new", "feature/x"]) == OK

    path = worktrees / "feature--x"
    assert tree_at(repo, path) == Worktree(path=path, branch="feature/x", head=develop)


def test_new_starts_from_main_when_the_project_says_nothing(
    repo: Path, worktrees: Path, register: Registrar
) -> None:
    register()
    old = run_git(["rev-parse", "HEAD"], cwd=repo)
    latest = commit(repo, "later")

    assert main(["wt", "new", "Topic/Two"]) == OK

    path = worktrees / "topic--two"
    assert tree_at(repo, path) == Worktree(path=path, branch="Topic/Two", head=latest)
    assert latest != old


def test_new_from_ref(repo: Path, worktrees: Path, register: Registrar) -> None:
    register()
    old = run_git(["rev-parse", "HEAD"], cwd=repo)
    _ = run_git(["branch", "other"], cwd=repo)
    _ = commit(repo, "later")

    assert main(["wt", "new", "a", "--from", "other"]) == OK
    assert main(["wt", "new", "b", "--from", old]) == OK

    assert tree_at(repo, worktrees / "a").head == old
    assert tree_at(repo, worktrees / "b").head == old


def test_new_from_a_ref_git_does_not_know_is_an_error(
    repo: Path, register: Registrar, capsys: pytest.CaptureFixture[str]
) -> None:
    register()

    assert main(["wt", "new", "a", "--from", "nowhere"]) == CONFIG

    assert capsys.readouterr().err.startswith("error: ")
    assert not has_branch(repo, "a")


def test_new_without_a_worktrees_dir_is_an_error(
    repo: Path, register: Registrar, capsys: pytest.CaptureFixture[str]
) -> None:
    register(with_worktrees=False)

    assert main(["wt", "new", "a"]) == CONFIG

    err = capsys.readouterr().err
    assert err.startswith("error: ")
    assert "project 'bar' has no worktrees directory" in err
    assert not has_branch(repo, "a")


def test_new_twice_is_an_error(
    worktrees: Path, register: Registrar, capsys: pytest.CaptureFixture[str]
) -> None:
    register()
    assert main(["wt", "new", "a/b"]) == OK
    _ = capsys.readouterr()

    assert main(["wt", "new", "a/b"]) == CONFIG

    assert str(worktrees / "a--b") in capsys.readouterr().err


def test_ls_lists_the_worktrees_with_their_branches(
    repo: Path,
    worktrees: Path,
    register: Registrar,
    capsys: pytest.CaptureFixture[str],
) -> None:
    register()
    assert main(["wt", "new", "feature/x"]) == OK
    _ = capsys.readouterr()

    status = main(["wt", "ls"])

    captured = capsys.readouterr()
    assert status == OK
    assert captured.err == ""
    rows = [line.split() for line in captured.out.splitlines()]
    head = run_git(["rev-parse", "--short=7", "HEAD"], cwd=repo)
    assert [str(repo), "main", head] in rows
    assert [str(worktrees / "feature--x"), "feature/x", head] in rows


def test_ls_works_from_a_worktree(
    repo: Path,
    worktrees: Path,
    register: Registrar,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    register()
    assert main(["wt", "new", "a"]) == OK
    _ = capsys.readouterr()
    monkeypatch.chdir(worktrees / "a")

    assert main(["wt", "ls"]) == OK

    assert str(repo) in capsys.readouterr().out


def test_rm_removes_the_worktree(worktrees: Path, register: Registrar) -> None:
    register()
    assert main(["wt", "new", "feature/x"]) == OK

    assert main(["wt", "rm", "feature--x"]) == OK

    assert not (worktrees / "feature--x").exists()


def test_rm_refuses_a_dirty_worktree_without_force(
    repo: Path,
    worktrees: Path,
    register: Registrar,
    capsys: pytest.CaptureFixture[str],
) -> None:
    register()
    assert main(["wt", "new", "a"]) == OK
    path = worktrees / "a"
    _ = (path / "scratch.txt").write_text("not committed\n", encoding="utf-8")
    _ = capsys.readouterr()

    assert main(["wt", "rm", "a"]) == CONFIG

    assert capsys.readouterr().err.startswith("error: ")
    assert path.is_dir()

    assert main(["wt", "rm", "a", "--force"]) == OK

    assert not path.exists()
    assert [tree.path for tree in list_worktrees(repo)] == [repo]


def test_rm_refuses_a_modified_tracked_file_without_force(
    worktrees: Path, register: Registrar, capsys: pytest.CaptureFixture[str]
) -> None:
    register()
    assert main(["wt", "new", "a"]) == OK
    path = worktrees / "a"
    _ = (path / "README").write_text("changed\n", encoding="utf-8")
    _ = capsys.readouterr()

    assert main(["wt", "rm", "a"]) == CONFIG

    assert "contains modified or untracked files" in capsys.readouterr().err
    assert (path / "README").read_text(encoding="utf-8") == "changed\n"
    assert main(["wt", "rm", "a", "--force"]) == OK
    assert not path.exists()


def test_rm_refuses_a_detached_head_that_no_branch_contains(
    worktrees: Path, register: Registrar, capsys: pytest.CaptureFixture[str]
) -> None:
    register()
    assert main(["wt", "new", "a"]) == OK
    path = worktrees / "a"
    _ = run_git(["switch", "-q", "--detach"], cwd=path)
    lost = commit(path, "on no branch")
    _ = capsys.readouterr()

    assert main(["wt", "rm", "a"]) == CONFIG

    assert capsys.readouterr().err == (
        f"error: {path}: HEAD is detached at {lost[:7]}, which no branch "
        + "contains, so removing the worktree would lose it; make a branch for "
        + "it, or give --force\n"
    )
    assert path.is_dir()
    assert main(["wt", "rm", "a", "--force"]) == OK
    assert not path.exists()


def test_rm_allows_a_detached_head_that_a_branch_contains(
    worktrees: Path, register: Registrar
) -> None:
    register()
    assert main(["wt", "new", "a"]) == OK
    path = worktrees / "a"
    _ = run_git(["switch", "-q", "--detach"], cwd=path)

    assert main(["wt", "rm", "a"]) == OK

    assert not path.exists()


def test_rm_refuses_a_worktree_in_the_middle_of_a_rebase(
    worktrees: Path, register: Registrar, capsys: pytest.CaptureFixture[str]
) -> None:
    register()
    assert main(["wt", "new", "a"]) == OK
    path = worktrees / "a"
    _ = commit(path, "to replay")
    assert git_or_none(["rebase", "-q", "--exec", "false", "HEAD~1"], cwd=path) is None
    _ = capsys.readouterr()

    assert main(["wt", "rm", "a"]) == CONFIG

    assert capsys.readouterr().err == (
        f"error: {path}: a rebase is in progress there; finish it, or give --force "
        + "to remove the worktree all the same\n"
    )
    assert main(["wt", "rm", "a", "--force"]) == OK
    assert not path.exists()


@pytest.mark.parametrize(
    ("marker", "operation"),
    [
        ("rebase-apply", "a rebase"),
        ("MERGE_HEAD", "a merge"),
        ("CHERRY_PICK_HEAD", "a cherry-pick"),
        ("REVERT_HEAD", "a revert"),
        ("BISECT_LOG", "a bisect"),
    ],
)
def test_rm_refuses_a_worktree_with_an_operation_in_progress(
    worktrees: Path,
    register: Registrar,
    capsys: pytest.CaptureFixture[str],
    marker: str,
    operation: str,
) -> None:
    register()
    assert main(["wt", "new", "a"]) == OK
    path = worktrees / "a"
    git_dir = Path(run_git(["rev-parse", "--absolute-git-dir"], cwd=path))
    _ = (git_dir / marker).write_text("x\n", encoding="utf-8")
    _ = capsys.readouterr()

    assert main(["wt", "rm", "a"]) == CONFIG

    assert f"{operation} is in progress there" in capsys.readouterr().err
    assert path.is_dir()


def test_rm_deletes_files_git_ignores(
    repo: Path, worktrees: Path, register: Registrar
) -> None:
    register()
    exclude = repo / ".git" / "info" / "exclude"
    _ = exclude.write_text("target/\n", encoding="utf-8")
    assert main(["wt", "new", "a"]) == OK
    path = worktrees / "a"
    (path / "target").mkdir()
    _ = (path / "target" / "build.o").write_text("x", encoding="utf-8")

    assert main(["wt", "rm", "a"]) == OK

    assert not path.exists()


def test_rm_refuses_the_projects_repository(
    configure: ConfigWriter,
    agent: Path,
    repo: Path,
    worktrees: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A project registered by a linked worktree, as atc once allowed.
    linked = worktrees / "linked"
    _ = run_git(["worktree", "add", "-q", "-b", "linked", str(linked)], cwd=repo)
    configure(
        {"conf/accounts/personal.json": unit({})},
        REGISTRY.format(agent=agent, repo=linked, more=f"    worktrees: {worktrees}\n"),
    )
    monkeypatch.chdir(repo)

    assert main(["wt", "rm", "linked", "--force"]) == CONFIG

    assert capsys.readouterr().err == (
        f"error: {linked}: it is the repository of project 'bar', not a worktree "
        + "to remove\n"
    )
    assert linked.is_dir()


def test_rm_keeps_the_branch(repo: Path, register: Registrar) -> None:
    register()
    assert main(["wt", "new", "feature/x"]) == OK

    assert main(["wt", "rm", "feature--x"]) == OK

    assert has_branch(repo, "feature/x")


@pytest.mark.parametrize("name", ["..", ".", "../bar", "a/b", ""])
def test_rm_only_takes_the_name_of_a_directory_of_the_worktrees_dir(
    repo: Path, register: Registrar, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    register()

    assert main(["wt", "rm", name, "--force"]) == CONFIG

    assert "not a name in" in capsys.readouterr().err
    assert repo.is_dir()


def test_rm_of_an_unknown_name_is_an_error(
    worktrees: Path, register: Registrar, capsys: pytest.CaptureFixture[str]
) -> None:
    register()

    assert main(["wt", "rm", "absent"]) == CONFIG

    assert str(worktrees / "absent") in capsys.readouterr().err


def test_rm_without_a_worktrees_dir_is_an_error(
    register: Registrar, capsys: pytest.CaptureFixture[str]
) -> None:
    register(with_worktrees=False)

    assert main(["wt", "rm", "a"]) == CONFIG

    assert "has no worktrees directory" in capsys.readouterr().err


@pytest.mark.parametrize("args", [["new", "a"], ["ls"], ["rm", "a"]])
def test_wt_outside_a_project_is_an_error(
    repo: Path,
    tmp_path: Path,
    register: Registrar,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    args: list[str],
) -> None:
    register()
    elsewhere = git_repo(make_source(tmp_path / "elsewhere", {"README": "y"}))
    plain = tmp_path / "plain"
    plain.mkdir()

    for directory in (elsewhere, plain):
        monkeypatch.chdir(directory)

        assert main(["wt", *args]) == USAGE

        assert "not in a registered project" in capsys.readouterr().err
    assert not has_branch(repo, "a")


def test_wt_has_help_for_each_command(
    executed: list[LaunchPlan], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["wt", "--help"]) == OK
    out = capsys.readouterr().out
    assert all(command in out for command in ("new", "ls", "rm"))

    assert main(["wt", "new", "--help"]) == OK
    out = capsys.readouterr().out
    assert "--from" in out
    assert "--run" in out

    assert main(["wt", "rm", "--help"]) == OK
    out = capsys.readouterr().out
    assert "--force" in out
    assert "ignores" in out
    assert executed == []


def test_new_run_starts_a_session_in_the_worktree(
    repo: Path,
    worktrees: Path,
    agent: Path,
    register: Registrar,
    executed: list[LaunchPlan],
    capsys: pytest.CaptureFixture[str],
) -> None:
    register()

    with pytest.raises(StopError):
        _ = main(["wt", "new", "feature/x", "--run"])

    (plan,) = executed
    path = worktrees / "feature--x"
    assert plan.cwd == path
    assert plan.project is not None
    assert plan.project.name == "bar"
    assert plan.argv[0] == str(agent)
    assert plan.common_dir == repo / ".git"
    assert tree_at(repo, path).branch == "feature/x"
    assert capsys.readouterr().err.endswith(
        "atc: project bar · account personal · guard on\n"
    )


def test_new_run_does_not_start_a_session_when_the_worktree_fails(
    register: Registrar, executed: list[LaunchPlan]
) -> None:
    register(with_worktrees=False)

    assert main(["wt", "new", "a", "--run"]) == CONFIG

    assert executed == []
