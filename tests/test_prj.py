"""``atc prj``: list, show, add and remove the projects of the registry."""

import re
import textwrap
from pathlib import Path

import pytest

from atc.cli import main
from atc.exitcodes import CONFIG, OK, USAGE
from atc.git import run_git
from atc.paths import Dirs
from atc.registry import REGISTRY_FILE, load_registry
from atc.worktree import Worktree, list_worktrees
from tests.conftest import ConfigWriter
from tests.helpers import git_repo, make_source, unit

PERSONAL = "conf/accounts/personal.json"
WORK = "conf/accounts/work.json"
BAR_UNIT = "conf/projects/bar.json"
FILES = {PERSONAL: unit(), WORK: unit(), BAR_UNIT: unit()}
HEAD = """\
    # this machine
    version: 1
    default-account: personal

    accounts:
      personal: {agent: claude, dir: ~/.claude}
      work: {agent: claude, dir: ~/.config/work}  # the second one
"""
TAIL = """
    # the end
"""


@pytest.fixture(autouse=True)
def wide_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMNS", "400")


def make_repo(root: Path) -> Path:
    """Make a repository on `main` with one commit."""
    return git_repo(make_source(root, {"README.md": "hello\n"}))


def registry_text(*projects: str) -> str:
    """Write the registry: the accounts, then `projects` as lines of the section."""
    if not projects:
        return HEAD + TAIL
    section = "\n    # my projects\n    projects:\n" + "".join(projects)
    return HEAD + section + TAIL


def project(name: str, repo: Path, *more: str) -> str:
    """Write a project entry of the registry, with `more` as further keys."""
    lines = [f"      {name}:\n", f"        repo: {repo}  # {name}'s checkout\n"]
    lines.extend(f"        {line}\n" for line in more)
    return "".join(lines)


def registry_file(dirs: Dirs) -> Path:
    return dirs.config_dir / REGISTRY_FILE


def fields_of(out: str) -> dict[str, str]:
    """Read the label and value lines of `prj show`, up to the first blank line."""
    fields: dict[str, str] = {}
    for line in out.splitlines():
        if not line.strip():
            break
        label, _, value = line.strip().partition("  ")
        fields[label] = value.strip()
    return fields


def rows_of(out: str, first: str) -> list[list[str]]:
    """Read a table of `out` whose heading starts with `first`, by columns."""
    lines = out.splitlines()
    start = next(i for i, line in enumerate(lines) if line.split()[:1] == [first])
    starts = [m.start() for m in re.finditer(r"\S+", lines[start])]
    body: list[list[str]] = []
    for line in lines[start + 2 :]:
        if not line.strip():
            break
        body.append(
            [
                line[a:b].strip()
                for a, b in zip(starts, [*starts[1:], len(line) + 1], strict=True)
            ]
        )
    return body


def test_add_writes_the_project_and_keeps_comments(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = make_repo(tmp_path / "bar")
    other = make_repo(tmp_path / "other")
    configure(FILES, registry_text(project("other", other)))
    before = registry_file(dirs).read_text(encoding="utf-8")
    worktrees = tmp_path / "wt" / "bar"

    status = main(
        [
            *("prj", "add", "bar", str(repo), "--worktrees", str(worktrees)),
            *("--from", "main", "-a", "work"),
        ]
    )

    captured = capsys.readouterr()
    assert status == OK
    assert captured.err == ""
    assert "bar" in captured.out
    entry = (
        f"  bar:\n    repo: {repo}\n    worktrees: {worktrees}\n"
        "    branch-from: main\n    account: work\n"
    )
    assert registry_file(dirs).read_text(encoding="utf-8") == before.replace(
        "\n# the end\n", f"{entry}\n# the end\n"
    )
    added = load_registry(registry_file(dirs), home).projects["bar"]
    assert (added.repo, added.worktrees) == (repo, worktrees)
    assert (added.branch_from, added.account) == ("main", "work")


def test_add_refuses_an_existing_name(
    configure: ConfigWriter,
    dirs: Dirs,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = make_repo(tmp_path / "bar")
    second = make_repo(tmp_path / "second")
    configure(FILES, registry_text(project("bar", repo)))
    before = registry_file(dirs).read_text(encoding="utf-8")

    status = main(["prj", "add", "bar", str(second)])

    assert status == CONFIG
    assert "/projects/bar: a project of that name exists already" in (
        capsys.readouterr().err
    )
    assert registry_file(dirs).read_text(encoding="utf-8") == before


def test_add_refuses_an_unknown_account_and_leaves_the_file(
    configure: ConfigWriter,
    dirs: Dirs,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = make_repo(tmp_path / "bar")
    configure(FILES, registry_text())
    before = registry_file(dirs).read_text(encoding="utf-8")

    status = main(["prj", "add", "bar", str(repo), "-a", "ghost"])

    assert status == CONFIG
    err = capsys.readouterr().err
    assert "/projects/bar/account: no account named 'ghost'" in err
    assert registry_file(dirs).read_text(encoding="utf-8") == before


def test_add_refuses_a_missing_branch(
    configure: ConfigWriter,
    dirs: Dirs,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = make_repo(tmp_path / "bar")
    configure(FILES, registry_text())
    before = registry_file(dirs).read_text(encoding="utf-8")

    status = main(["prj", "add", "bar", str(repo), "--from", "nope"])

    assert status == CONFIG
    assert "no branch named 'nope'" in capsys.readouterr().err
    assert registry_file(dirs).read_text(encoding="utf-8") == before


def test_add_checks_the_default_base_branch_too(
    configure: ConfigWriter,
    dirs: Dirs,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = make_repo(tmp_path / "bar")
    _ = run_git(["branch", "-m", "main", "trunk"], cwd=repo)
    configure(FILES, registry_text())

    status = main(["prj", "add", "bar", str(repo)])

    assert status == CONFIG
    assert "no branch named 'main'" in capsys.readouterr().err
    assert main(["prj", "add", "bar", str(repo), "--from", "trunk"]) == OK
    assert "branch-from: trunk" in registry_file(dirs).read_text(encoding="utf-8")


def test_add_refuses_a_directory_that_is_not_a_repository(
    configure: ConfigWriter,
    dirs: Dirs,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plain = make_source(tmp_path / "plain", {"a": "b"})
    configure(FILES, registry_text())
    before = registry_file(dirs).read_text(encoding="utf-8")

    status = main(["prj", "add", "bar", str(plain)])

    assert status == CONFIG
    assert f"{plain}: not a git repository" in capsys.readouterr().err
    assert registry_file(dirs).read_text(encoding="utf-8") == before


def test_add_defaults_repo_to_the_current_repository(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path / "bar")
    (repo / "src").mkdir()
    configure(FILES, registry_text())
    monkeypatch.chdir(repo / "src")

    status = main(["prj", "add", "bar"])

    assert status == OK
    assert load_registry(registry_file(dirs), home).projects["bar"].repo == repo


def test_add_without_a_repo_outside_a_repository_is_refused(
    configure: ConfigWriter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure(FILES, registry_text())
    monkeypatch.chdir(make_source(tmp_path / "plain", {"a": "b"}))

    status = main(["prj", "add", "bar"])

    assert status == CONFIG
    assert "is not in a git repository; name the REPO" in capsys.readouterr().err


def test_add_warns_without_a_project_unit(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = make_repo(tmp_path / "bar")
    configure({PERSONAL: unit(), WORK: unit()}, registry_text())

    status = main(["prj", "add", "bar", str(repo)])

    assert status == OK
    assert "warning: no config source has projects/bar.json" in (
        capsys.readouterr().err
    )
    assert "bar" in load_registry(registry_file(dirs), home).projects


def test_rm_removes_only_the_entry(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bar, other = tmp_path / "bar", tmp_path / "other"
    configure(FILES, registry_text(project("bar", bar), project("other", other)))
    before = registry_file(dirs).read_text(encoding="utf-8")

    status = main(["prj", "rm", "bar"])

    assert status == OK
    assert "bar" in capsys.readouterr().out
    assert registry_file(dirs).read_text(encoding="utf-8") == before.replace(
        textwrap.indent(textwrap.dedent(project("bar", bar)), "  "), ""
    )
    assert list(load_registry(registry_file(dirs), home).projects) == ["other"]


@pytest.mark.parametrize(("gap", "comment"), [("", ""), ("\n", "  # about other\n")])
def test_rm_drops_the_comment_directly_above_the_entry_only(
    configure: ConfigWriter, dirs: Dirs, tmp_path: Path, gap: str, comment: str
) -> None:
    bar = tmp_path / "bar"
    entries = [
        project("bar", bar),
        f"      # about other\n{gap}",
        project("other", tmp_path / "other"),
    ]
    configure(FILES, registry_text(*entries))

    assert main(["prj", "rm", "other"]) == OK

    assert (
        registry_file(dirs)
        .read_text(encoding="utf-8")
        .endswith(f"    repo: {bar}  # bar's checkout\n{comment}\n# the end\n")
    )


def test_rm_refuses_an_unknown_project(
    configure: ConfigWriter,
    dirs: Dirs,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure(FILES, registry_text(project("bar", tmp_path / "bar")))
    before = registry_file(dirs).read_text(encoding="utf-8")

    status = main(["prj", "rm", "ghost"])

    assert status == CONFIG
    assert "no project named 'ghost'; the projects are bar" in capsys.readouterr().err
    assert registry_file(dirs).read_text(encoding="utf-8") == before


def test_show_defaults_to_the_current_project_and_lists_worktrees(
    configure: ConfigWriter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = make_repo(tmp_path / "bar")
    wt = tmp_path / "wt" / "feature"
    _ = run_git(["worktree", "add", "-q", "-b", "feature", str(wt)], cwd=repo)
    configure(
        FILES,
        registry_text(
            project("bar", repo, f"worktrees: {tmp_path / 'wt'}", "account: work")
        ),
    )
    monkeypatch.chdir(wt)

    status = main(["prj", "show"])

    out = capsys.readouterr().out
    assert status == OK
    assert fields_of(out) == {
        "name": "bar",
        "repository": str(repo),
        "account": "work",
        "accounts": "every account",
        "unit": "projects/bar.json (the default)",
        "worktrees": str(tmp_path / "wt"),
        "branch from": "main",
        "snapshot": "-",
        "vars": "-",
    }
    short = run_git(["rev-parse", "HEAD"], cwd=repo)[:7]
    assert rows_of(out, "Worktree") == [
        [str(repo), "main", short],
        [str(wt), "feature", short],
    ]


def test_show_takes_a_name_from_anywhere(
    configure: ConfigWriter,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = make_repo(tmp_path / "bar")
    configure(FILES, registry_text(project("bar", repo, "accounts: [work, personal]")))

    status = main(["prj", "show", "bar"])

    out = capsys.readouterr().out
    assert status == OK
    assert fields_of(out)["account"] == "personal (the default account)"
    assert fields_of(out)["accounts"] == "work, personal"


def test_show_outside_a_registered_project_needs_a_name(
    configure: ConfigWriter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure(FILES, registry_text(project("bar", make_repo(tmp_path / "bar"))))
    monkeypatch.chdir(make_repo(tmp_path / "elsewhere"))

    status = main(["prj", "show"])

    assert status == USAGE
    assert "not in a registered project" in capsys.readouterr().err


def test_show_still_shows_the_fields_when_git_cannot_list_the_worktrees(
    configure: ConfigWriter,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure(FILES, registry_text(project("bar", tmp_path / "gone")))

    status = main(["prj", "show", "bar"])

    captured = capsys.readouterr()
    assert status == OK
    assert fields_of(captured.out)["repository"] == str(tmp_path / "gone")
    assert "warning: cannot list the worktrees of 'bar'" in captured.err


def test_ls_lists_every_project(
    configure: ConfigWriter,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bar, other = tmp_path / "bar", tmp_path / "other"
    configure(
        FILES,
        registry_text(
            project("bar", bar, f"worktrees: {tmp_path / 'wt'}", "account: work"),
            project("other", other),
        ),
    )

    status = main(["prj", "ls"])

    out = capsys.readouterr().out
    assert status == OK
    assert rows_of(out, "Name") == [
        ["bar", str(bar), "work", str(tmp_path / "wt")],
        ["other", str(other), "personal", "-"],
    ]


def test_ls_says_when_there_are_no_projects(
    configure: ConfigWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(FILES, registry_text())

    status = main(["prj", "ls"])

    captured = capsys.readouterr()
    assert status == OK
    assert captured.out == ""
    assert "no projects are registered" in captured.err


def test_list_worktrees_gives_the_branch_or_none_and_leaves_out_a_bare_repository(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path / "repo")
    feature, loose = tmp_path / "wt" / "feature", tmp_path / "wt" / "loose"
    _ = run_git(["worktree", "add", "-q", "-b", "feature", str(feature)], cwd=repo)
    _ = run_git(["worktree", "add", "-q", "--detach", str(loose)], cwd=repo)
    head = run_git(["rev-parse", "HEAD"], cwd=repo)
    bare = tmp_path / "bare.git"
    _ = run_git(["clone", "-q", "--bare", str(repo), str(bare)])
    _ = run_git(["worktree", "add", "-q", str(tmp_path / "wt" / "b"), "main"], cwd=bare)

    assert list_worktrees(repo) == [
        Worktree(repo, "main", head),
        Worktree(feature, "feature", head),
        Worktree(loose, None, head),
    ]
    assert list_worktrees(bare) == [Worktree(tmp_path / "wt" / "b", "main", head)]


def test_add_refuses_a_repository_that_is_registered_already(
    configure: ConfigWriter,
    dirs: Dirs,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = make_repo(tmp_path / "bar")
    wt = tmp_path / "wt" / "feature"
    _ = run_git(["worktree", "add", "-q", "-b", "feature", str(wt)], cwd=repo)
    configure(FILES, registry_text(project("bar", repo)))
    before = registry_file(dirs).read_text(encoding="utf-8")

    status = main(["prj", "add", "again", str(wt)])

    assert status == CONFIG
    assert f"{repo}: already registered as project 'bar'" in capsys.readouterr().err
    assert registry_file(dirs).read_text(encoding="utf-8") == before


@pytest.mark.parametrize("base", ["main~1", "main^", "main@{0}", "HEAD"])
def test_add_refuses_a_base_that_is_not_a_branch(
    configure: ConfigWriter,
    dirs: Dirs,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    base: str,
) -> None:
    repo = make_repo(tmp_path / "bar")
    _ = run_git(["commit", "-q", "--allow-empty", "-m", "second"], cwd=repo)
    configure(FILES, registry_text())
    before = registry_file(dirs).read_text(encoding="utf-8")

    status = main(["prj", "add", "bar", str(repo), "--from", base])

    assert status == CONFIG
    assert f"no branch named '{base}'" in capsys.readouterr().err
    assert registry_file(dirs).read_text(encoding="utf-8") == before


def test_add_stores_the_top_level_of_an_explicit_repo(
    configure: ConfigWriter, dirs: Dirs, home: Path, tmp_path: Path
) -> None:
    repo = make_repo(tmp_path / "bar")
    (repo / "src").mkdir()
    configure(FILES, registry_text())

    status = main(["prj", "add", "bar", str(repo / "src")])

    assert status == OK
    assert load_registry(registry_file(dirs), home).projects["bar"].repo == repo


def test_add_keeps_a_bare_repository_as_it_is(
    configure: ConfigWriter, dirs: Dirs, home: Path, tmp_path: Path
) -> None:
    bare = tmp_path / "bar.git"
    _ = run_git(["clone", "-q", "--bare", str(make_repo(tmp_path / "src")), str(bare)])
    configure(FILES, registry_text())

    status = main(["prj", "add", "bar", str(bare)])

    assert status == OK
    assert load_registry(registry_file(dirs), home).projects["bar"].repo == bare


@pytest.mark.parametrize("given", [False, True], ids=["from-cwd", "as-repo"])
def test_add_from_a_linked_worktree_stores_the_main_worktree(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    given: bool,
) -> None:
    repo = make_repo(tmp_path / "bar")
    wt = tmp_path / "wt" / "feature"
    _ = run_git(["worktree", "add", "-q", "-b", "feature", str(wt)], cwd=repo)
    configure(FILES, registry_text())
    monkeypatch.chdir(wt)

    status = main(["prj", "add", "bar", *([str(wt)] if given else [])])

    assert status == OK
    assert load_registry(registry_file(dirs), home).projects["bar"].repo == repo


def test_add_inside_a_bare_repository_without_a_repo_stores_it(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bare = tmp_path / "bar.git"
    _ = run_git(["clone", "-q", "--bare", str(make_repo(tmp_path / "src")), str(bare)])
    configure(FILES, registry_text())
    monkeypatch.chdir(bare / "refs")

    status = main(["prj", "add", "bar"])

    assert status == OK
    assert load_registry(registry_file(dirs), home).projects["bar"].repo == bare


def test_add_keeps_the_top_level_when_the_git_directory_is_elsewhere(
    configure: ConfigWriter, dirs: Dirs, home: Path, tmp_path: Path
) -> None:
    # As a submodule's checkout is: its git directory is not next to it, so the
    # one above the git directory is no checkout of it.
    checkout = tmp_path / "work" / "bar"
    git_dir = tmp_path / "gitdirs" / "bar"
    git_dir.parent.mkdir()
    _ = run_git(
        ["init", "-q", "-b", "main", "--separate-git-dir", str(git_dir), str(checkout)]
    )
    _ = run_git(["commit", "-q", "--allow-empty", "-m", "init"], cwd=checkout)
    configure(FILES, registry_text())

    status = main(["prj", "add", "bar", str(checkout)])

    assert status == OK
    assert load_registry(registry_file(dirs), home).projects["bar"].repo == checkout
