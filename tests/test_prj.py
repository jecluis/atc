"""``atc prj``: list, show, add and remove the projects of the registry."""

import re
import sys
import textwrap
from pathlib import Path

import pytest

from atc.cli import main
from atc.exitcodes import CONFIG, INTERRUPTED, OK, USAGE
from atc.git import run_git
from atc.paths import Dirs
from atc.registry import REGISTRY_FILE, load_registry
from atc.worktree import Worktree, list_worktrees
from tests.conftest import ConfigWriter
from tests.helpers import Answers, answering, git_repo, make_source, unit

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


REPOSITORY = "The project's repository:"
BRANCH = "The branch new branches start from:"
WORKTREES = "The directory the project's worktrees are made in (empty for none):"
ACCOUNTS = "The accounts allowed to work on the project (none for every account):"


def asking(monkeypatch: pytest.MonkeyPatch, repo: Path | None = None) -> Answers:
    """
    Answer the questions of `prj add`, in a terminal.

    The answers are `repo` for the repository, then `main`, no worktrees
    directory and no accounts.
    """
    given = answering(monkeypatch)
    given.text = "main"
    given.path = ""
    given.pick = []
    if repo is not None:
        given.paths = {REPOSITORY: str(repo)}
    return given


def test_add_writes_the_project_and_keeps_comments(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path / "bar")
    other = make_repo(tmp_path / "other")
    configure(FILES, registry_text(project("other", other)))
    before = registry_file(dirs).read_text(encoding="utf-8")
    worktrees = tmp_path / "wt" / "bar"

    given = asking(monkeypatch)

    status = main(
        [
            *("prj", "add", "bar", str(repo), "--worktrees", str(worktrees)),
            *("--from", "main", "-a", "work", "--account", "personal"),
        ]
    )

    captured = capsys.readouterr()
    assert status == OK
    assert captured.err == ""
    assert "bar" in captured.out
    entry = (
        f"  bar:\n    repo: {repo}\n    worktrees: {worktrees}\n"
        "    branch-from: main\n    account: work\n    accounts: [work, personal]\n"
    )
    assert registry_file(dirs).read_text(encoding="utf-8") == before.replace(
        "\n# the end\n", f"{entry}\n# the end\n"
    )
    assert given.asked == []
    added = load_registry(registry_file(dirs), home).projects["bar"]
    assert (added.repo, added.worktrees) == (repo, worktrees)
    assert (added.branch_from, added.account) == ("main", "work")
    assert added.accounts == ("work", "personal")


def test_add_asks_for_the_branch_and_worktrees_and_writes_no_branch_for_main(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path / "bar")
    configure(FILES, registry_text())
    given = asking(monkeypatch)

    status = main(["prj", "add", "bar", str(repo), "-a", "work"])

    assert status == OK
    assert given.asked == [f"{BRANCH} ['main'] default=main", f"{WORKTREES} default="]
    assert given.directories
    text = registry_file(dirs).read_text(encoding="utf-8")
    assert (
        f"  bar:\n    repo: {repo}\n    account: work\n    accounts: [work]\n" in text
    )
    added = load_registry(registry_file(dirs), home).projects["bar"]
    assert (added.account, added.accounts) == ("work", ("work",))
    assert (added.branch_from, added.worktrees) == ("main", None)


def test_add_with_no_account_ticked_allows_every_account(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path / "bar")
    configure(FILES, registry_text())
    given = asking(monkeypatch)

    status = main(["prj", "add", "bar", str(repo), "--worktrees", str(tmp_path)])

    assert status == OK
    assert given.asked[-1] == f"{ACCOUNTS} [('personal', False), ('work', False)]"
    added = load_registry(registry_file(dirs), home).projects["bar"]
    assert (added.account, added.accounts) == (None, None)


def test_add_with_several_accounts_ticked_asks_for_the_default(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path / "bar")
    configure(FILES, registry_text())
    given = asking(monkeypatch)
    given.pick = ["personal", "work"]
    given.choose = "work"

    status = main(["prj", "add", "bar", str(repo), "--worktrees", str(tmp_path)])

    assert status == OK
    expected = "The account the project uses by default: ['personal', 'work']"
    assert given.asked[-1] == expected
    added = load_registry(registry_file(dirs), home).projects["bar"]
    assert (added.account, added.accounts) == ("work", ("work", "personal"))


def test_add_makes_the_worktrees_answer_absolute(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path / "bar")
    configure(FILES, registry_text())
    given = asking(monkeypatch)
    given.paths = {WORKTREES: "~/wt/"}
    _ = (home / "file").write_text("x", encoding="utf-8")

    status = main(["prj", "add", "bar", str(repo)])

    assert status == OK
    added = load_registry(registry_file(dirs), home).projects["bar"]
    assert added.worktrees == home / "wt"
    check = given.checks[WORKTREES]
    assert check("") is None
    assert check(str(home / "nowhere")) is None
    assert check("~/file") == f"{home / 'file'} is not a directory"


def test_add_without_a_terminal_is_a_usage_error(
    configure: ConfigWriter,
    dirs: Dirs,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = make_repo(tmp_path / "bar")
    configure(FILES, registry_text())
    before = registry_file(dirs).read_text(encoding="utf-8")

    status = main(["prj", "add", "bar", str(repo), "--worktrees", str(tmp_path)])

    assert status == USAGE
    assert "atc prj add needs a terminal" in capsys.readouterr().err
    assert registry_file(dirs).read_text(encoding="utf-8") == before


def test_add_cancelled_at_a_question_writes_nothing(
    configure: ConfigWriter,
    dirs: Dirs,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = make_repo(tmp_path / "bar")
    configure(FILES, registry_text())
    before = registry_file(dirs).read_text(encoding="utf-8")
    given = asking(monkeypatch)
    given.pick = None

    assert main(["prj", "add", "bar", str(repo)]) == INTERRUPTED

    assert "cancelled; nothing changed" in capsys.readouterr().err
    assert registry_file(dirs).read_text(encoding="utf-8") == before


def test_add_refuses_an_invalid_name_before_asking(
    configure: ConfigWriter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure(FILES, registry_text())
    given = asking(monkeypatch)

    assert main(["prj", "add", "Bar", str(make_repo(tmp_path / "bar"))]) == CONFIG

    assert "'Bar' is not a valid project name" in capsys.readouterr().err
    assert given.asked == []


def test_add_refuses_an_account_given_twice_before_reading_anything(
    configure: ConfigWriter,
    dirs: Dirs,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure(FILES, registry_text())
    before = registry_file(dirs).read_text(encoding="utf-8")

    status = main(
        [
            *("prj", "add", "bar", str(tmp_path / "nowhere")),
            *("-a", "work", "-a", "personal", "--account", "work"),
        ]
    )

    assert status == USAGE
    assert "given more than once: work" in capsys.readouterr().err
    assert registry_file(dirs).read_text(encoding="utf-8") == before


def test_add_refuses_an_unknown_second_account_and_leaves_the_file(
    configure: ConfigWriter,
    dirs: Dirs,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path / "bar")
    configure(FILES, registry_text())
    before = registry_file(dirs).read_text(encoding="utf-8")

    given = asking(monkeypatch)

    status = main(["prj", "add", "bar", str(repo), "-a", "work", "-a", "ghost"])

    assert status == CONFIG
    err = capsys.readouterr().err
    assert "no account named 'ghost'; the accounts are personal, work" in err
    assert given.asked == []
    assert registry_file(dirs).read_text(encoding="utf-8") == before


def test_add_refuses_an_existing_name(
    configure: ConfigWriter,
    dirs: Dirs,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path / "bar")
    second = make_repo(tmp_path / "second")
    configure(FILES, registry_text(project("bar", repo)))
    before = registry_file(dirs).read_text(encoding="utf-8")

    given = asking(monkeypatch)

    status = main(["prj", "add", "bar", str(second)])

    assert status == CONFIG
    assert "/projects/bar: a project of that name exists already" in (
        capsys.readouterr().err
    )
    assert registry_file(dirs).read_text(encoding="utf-8") == before
    assert given.asked == []


def test_add_refuses_an_unknown_account_and_leaves_the_file(
    configure: ConfigWriter,
    dirs: Dirs,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path / "bar")
    configure(FILES, registry_text())
    before = registry_file(dirs).read_text(encoding="utf-8")

    given = asking(monkeypatch)

    status = main(["prj", "add", "bar", str(repo), "-a", "ghost"])

    assert status == CONFIG
    err = capsys.readouterr().err
    assert "no account named 'ghost'; the accounts are personal, work" in err
    assert registry_file(dirs).read_text(encoding="utf-8") == before
    assert given.asked == []


def test_add_refuses_a_missing_branch(
    configure: ConfigWriter,
    dirs: Dirs,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path / "bar")
    configure(FILES, registry_text())
    before = registry_file(dirs).read_text(encoding="utf-8")

    given = asking(monkeypatch)

    status = main(["prj", "add", "bar", str(repo), "--from", "nope"])

    assert status == CONFIG
    assert "no branch named 'nope'" in capsys.readouterr().err
    assert registry_file(dirs).read_text(encoding="utf-8") == before
    assert given.asked == []


def test_add_asks_for_a_branch_when_main_is_missing(
    configure: ConfigWriter,
    dirs: Dirs,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path / "bar")
    _ = run_git(["branch", "-m", "main", "trunk"], cwd=repo)
    configure(FILES, registry_text())
    given = asking(monkeypatch)
    given.text = "trunk"

    assert main(["prj", "add", "bar", str(repo)]) == OK

    assert given.asked[0] == f"{BRANCH} ['trunk'] default="
    check = given.checks[BRANCH]
    assert check("") == "a branch is needed"
    assert check("main") == "no branch named 'main'"
    assert check(" trunk ") is None
    assert "branch-from: trunk" in registry_file(dirs).read_text(encoding="utf-8")


def test_add_refuses_a_directory_that_is_not_a_repository(
    configure: ConfigWriter,
    dirs: Dirs,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plain = make_source(tmp_path / "plain", {"a": "b"})
    configure(FILES, registry_text())
    before = registry_file(dirs).read_text(encoding="utf-8")

    given = asking(monkeypatch)

    status = main(["prj", "add", "bar", str(plain)])

    assert status == CONFIG
    assert f"{plain}: not a git repository" in capsys.readouterr().err
    assert registry_file(dirs).read_text(encoding="utf-8") == before
    assert given.asked == []


def test_add_asks_for_the_repository_from_the_current_directory(
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
    given = asking(monkeypatch, repo / "src")

    status = main(["prj", "add", "bar"])

    assert status == OK
    assert given.asked[0] == f"{REPOSITORY} default={repo / 'src'}"
    assert load_registry(registry_file(dirs), home).projects["bar"].repo == repo


def test_add_outside_a_repository_asks_for_one_with_no_default(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path / "bar")
    other = make_repo(tmp_path / "other")
    plain = make_source(tmp_path / "plain", {"a": "b"})
    configure(FILES, registry_text(project("other", other)))
    monkeypatch.chdir(plain)
    given = asking(monkeypatch, repo)

    assert main(["prj", "add", "bar"]) == OK

    assert given.asked[0] == f"{REPOSITORY} default="
    check = given.checks[REPOSITORY]
    assert check("") == "a repository is needed"
    assert check(str(plain)) == f"{plain}: not a git repository"
    assert check(str(other)) == f"{other}: already registered as project 'other'"
    assert check(str(repo)) is None
    assert load_registry(registry_file(dirs), home).projects["bar"].repo == repo


def test_add_checks_the_given_branch_in_the_repository_it_asks_for(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path / "bar")
    _ = run_git(["branch", "trunk"], cwd=repo)
    plain = make_source(tmp_path / "plain", {"a": "b"})
    configure(FILES, registry_text())
    monkeypatch.chdir(plain)
    given = asking(monkeypatch, repo)

    assert main(["prj", "add", "bar", "--from", "trunk"]) == OK

    lacking = make_repo(tmp_path / "lacking")
    check = given.checks[REPOSITORY]
    assert check(str(lacking)) == (
        f"{lacking}: no branch named 'trunk'; name the one new branches start "
        "from with --from"
    )
    assert not [q for q in given.asked if q.startswith(BRANCH)]
    added = load_registry(registry_file(dirs), home).projects["bar"]
    assert added.branch_from == "trunk"


def test_add_warns_without_a_project_unit(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path / "bar")
    configure({PERSONAL: unit(), WORK: unit()}, registry_text())

    _ = asking(monkeypatch)

    status = main(["prj", "add", "bar", str(repo)])

    assert status == OK
    assert "warning: no config source has projects/bar.json" in (
        capsys.readouterr().err
    )
    assert "bar" in load_registry(registry_file(dirs), home).projects


THREE = """\
    version: 1
    default-account: work
    accounts:
      personal: {agent: claude, dir: ~/.claude}
      spare: {agent: claude, dir: ~/.config/spare}
      work: {agent: claude, dir: ~/.config/work}
"""


@pytest.mark.parametrize(
    ("picked", "offered", "chosen", "accounts"),
    [
        (["personal", "work"], ["work", "personal"], "work", ("work", "personal")),
        (["personal", "spare"], ["personal", "spare"], "spare", ("spare", "personal")),
        (
            ["personal", "spare", "work"],
            ["work", "personal", "spare"],
            "spare",
            ("spare", "personal", "work"),
        ),
    ],
    ids=["default-ticked", "default-not-ticked", "others-in-file-order"],
)
def test_add_offers_the_default_account_first_and_writes_it_first(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    picked: list[str],
    offered: list[str],
    chosen: str,
    accounts: tuple[str, ...],
) -> None:
    repo = make_repo(tmp_path / "bar")
    spare = {"conf/accounts/spare.json": unit()}
    configure({**FILES, **spare}, THREE)
    given = asking(monkeypatch)
    given.pick = picked
    given.choose = chosen

    status = main(["prj", "add", "bar", str(repo), "--worktrees", str(tmp_path)])

    assert status == OK
    assert given.asked[-1] == f"The account the project uses by default: {offered}"
    added = load_registry(registry_file(dirs), home).projects["bar"]
    assert (added.account, added.accounts) == (chosen, accounts)


def test_add_with_one_account_ticked_writes_it_as_the_default_and_a_list(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path / "bar")
    configure(FILES, registry_text())
    given = asking(monkeypatch)
    given.pick = ["work"]

    status = main(["prj", "add", "bar", str(repo), "--worktrees", str(tmp_path)])

    assert status == OK
    assert given.asked[-1].startswith(ACCOUNTS)
    text = registry_file(dirs).read_text(encoding="utf-8")
    assert "    account: work\n    accounts: [work]\n" in text
    added = load_registry(registry_file(dirs), home).projects["bar"]
    assert (added.account, added.accounts) == ("work", ("work",))


def test_add_asks_for_the_repository_among_directories_only(
    configure: ConfigWriter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path / "bar")
    configure(FILES, registry_text())
    monkeypatch.chdir(tmp_path)
    given = asking(monkeypatch, repo)

    args = ["--from", "main", "--worktrees", str(tmp_path), "-a", "work"]
    assert main(["prj", "add", "bar", *args]) == OK

    assert given.asked == [f"{REPOSITORY} default="]
    assert given.directories


def test_add_warns_of_a_missing_unit_before_any_question(
    configure: ConfigWriter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure({PERSONAL: unit(), WORK: unit()}, registry_text())
    monkeypatch.chdir(tmp_path)
    given = asking(monkeypatch)
    given.paths = {REPOSITORY: None}

    assert main(["prj", "add", "bar"]) == INTERRUPTED

    assert given.asked == [f"{REPOSITORY} default="]
    err = capsys.readouterr().err
    assert "warning: no config source has projects/bar.json" in err
    assert "cancelled; nothing changed" in err


def test_add_refuses_a_repository_without_a_branch_yet(
    configure: ConfigWriter,
    dirs: Dirs,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    empty = tmp_path / "empty"
    _ = run_git(["init", "-q", "-b", "main", str(empty)])
    configure(FILES, registry_text())
    before = registry_file(dirs).read_text(encoding="utf-8")
    given = asking(monkeypatch)

    assert main(["prj", "add", "bar", str(empty)]) == CONFIG

    assert "has no branch yet, for new branches to start from" in (
        capsys.readouterr().err
    )
    assert given.asked == []
    assert registry_file(dirs).read_text(encoding="utf-8") == before


def test_add_refuses_config_sources_that_do_not_load_before_asking(
    configure: ConfigWriter,
    dirs: Dirs,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure(FILES, registry_text())
    _ = (dirs.conf_d / "conf").rename(dirs.conf_d / "elsewhere")
    given = asking(monkeypatch)

    assert main(["prj", "add", "bar", str(make_repo(tmp_path / "bar"))]) == CONFIG

    assert "no config sources found" in capsys.readouterr().err
    assert given.asked == []


NEEDY = {
    PERSONAL: unit(),
    WORK: unit(),
    BAR_UNIT: unit(
        {
            "env": {
                "CACHE": "{{account.vars.cache}}",
                "PORT": "{{project.vars.port}}",
                "HOST": "{{vars.host}}",
            }
        }
    ),
}
"""Units where the project uses a variable of each level that nothing defines."""


def test_add_asks_for_the_variables_the_project_needs_and_writes_them_at_once(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = make_repo(tmp_path / "bar")
    configure(NEEDY, registry_text())
    given = asking(monkeypatch)
    given.paths = {
        "project.vars.port": "p80",
        "account.vars.cache for account personal": "~/c",
        "account.vars.cache for account work": "/w",
        "vars.host": "h",
    }

    status = main(["prj", "add", "bar", str(repo), "--worktrees", str(tmp_path)])

    assert status == OK
    users = "used by projects/bar.json: default="
    assert given.asked[-4:] == [
        f"project.vars.port for project bar, {users}",
        f"account.vars.cache for account personal, {users}",
        f"account.vars.cache for account work, {users}",
        f"vars.host for machine, {users}",
    ]
    assert given.check is not None
    assert given.check("") == "a value is needed"
    registry = load_registry(registry_file(dirs), home)
    assert registry.projects["bar"].vars == {"port": "p80"}
    assert registry.accounts["personal"].vars == {"cache": f"{home}/c"}
    assert registry.accounts["work"].vars == {"cache": "/w"}
    assert registry.vars == {"host": "h"}
    text = registry_file(dirs).read_text(encoding="utf-8")
    assert "default-account: personal\nvars:\n  host: h\n" in text
    assert "account and machine-wide variables take effect once atc sync runs" in (
        capsys.readouterr().err
    )
    assert main(["src", "check"]) == OK


def test_add_asks_only_for_the_accounts_the_project_allows(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path / "bar")
    configure(NEEDY, registry_text())
    given = asking(monkeypatch)
    given.path = "v"

    args = [str(repo), "--worktrees", str(tmp_path), "-a", "work"]
    assert main(["prj", "add", "bar", *args]) == OK

    asked = [q.split(",")[0] for q in given.asked if "vars." in q]
    assert asked == [
        "project.vars.port for project bar",
        "account.vars.cache for account work",
        "vars.host for machine",
    ]
    registry = load_registry(registry_file(dirs), home)
    assert registry.accounts["personal"].vars == {}


def test_add_with_only_project_variables_does_not_ask_for_a_sync(
    configure: ConfigWriter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = make_repo(tmp_path / "bar")
    files = {**NEEDY, BAR_UNIT: unit({"env": {"PORT": "{{project.vars.port}}"}})}
    configure(files, registry_text())
    given = asking(monkeypatch)
    given.path = "p"

    assert main(["prj", "add", "bar", str(repo), "--worktrees", str(tmp_path)]) == OK

    assert "atc sync" not in capsys.readouterr().err


def test_add_reports_what_no_answer_could_fix_before_asking_for_variables(
    configure: ConfigWriter,
    dirs: Dirs,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = make_repo(tmp_path / "bar")
    files = {
        **NEEDY,
        BAR_UNIT: unit({"env": {"A": "{{project.vars.p}}", "B": "{{projct.name}}"}}),
    }
    configure(files, registry_text())
    before = registry_file(dirs).read_text(encoding="utf-8")
    given = asking(monkeypatch)

    status = main(["prj", "add", "bar", str(repo), "--worktrees", str(tmp_path)])

    assert status == CONFIG
    assert "unknown reference {{projct.name}}" in capsys.readouterr().err
    assert not [q for q in given.asked if "vars." in q]
    assert registry_file(dirs).read_text(encoding="utf-8") == before


def test_add_cancelled_at_a_variable_writes_nothing(
    configure: ConfigWriter,
    dirs: Dirs,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path / "bar")
    configure(NEEDY, registry_text())
    before = registry_file(dirs).read_text(encoding="utf-8")
    given = asking(monkeypatch)
    given.path = "v"
    given.paths = {"vars.host": None}

    status = main(["prj", "add", "bar", str(repo), "--worktrees", str(tmp_path)])

    assert status == INTERRUPTED
    assert registry_file(dirs).read_text(encoding="utf-8") == before


def test_rm_removes_only_the_entry(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bar, other = tmp_path / "bar", tmp_path / "other"
    configure(FILES, registry_text(project("bar", bar), project("other", other)))
    before = registry_file(dirs).read_text(encoding="utf-8")
    given = answering(monkeypatch)

    status = main(["prj", "rm", "bar"])

    assert status == OK
    assert given.asked == ["Remove project bar? default=False"]
    captured = capsys.readouterr()
    assert captured.out == f"bar: removed from {registry_file(dirs)}\n"
    assert f"this removes project 'bar' ({bar}) from" in captured.err
    assert "its worktrees and its unit projects/bar.json stay" in captured.err
    assert registry_file(dirs).read_text(encoding="utf-8") == before.replace(
        textwrap.indent(textwrap.dedent(project("bar", bar)), "  "), ""
    )
    assert list(load_registry(registry_file(dirs), home).projects) == ["other"]


@pytest.mark.parametrize(("gap", "comment"), [("", ""), ("\n", "  # about other\n")])
def test_rm_drops_the_comment_directly_above_the_entry_only(
    configure: ConfigWriter,
    dirs: Dirs,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    gap: str,
    comment: str,
) -> None:
    bar = tmp_path / "bar"
    entries = [
        project("bar", bar),
        f"      # about other\n{gap}",
        project("other", tmp_path / "other"),
    ]
    configure(FILES, registry_text(*entries))
    _ = answering(monkeypatch)

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
    err = capsys.readouterr().err
    assert "no project named 'ghost'; the projects are bar" in err
    assert "needs a terminal" not in err
    assert registry_file(dirs).read_text(encoding="utf-8") == before


def test_rm_names_the_vars_that_go_with_the_project(
    configure: ConfigWriter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bar = tmp_path / "bar"
    configure(FILES, registry_text(project("bar", bar, "vars: {x: '1', y: '2'}")))
    _ = answering(monkeypatch)

    assert main(["prj", "rm", "bar"]) == OK

    err = capsys.readouterr().err
    assert f"project 'bar' ({bar}) from " in err
    assert ", with its vars x, y\n" in err


@pytest.mark.parametrize(("answer", "status"), [(False, OK), (None, INTERRUPTED)])
def test_rm_answered_no_or_cancelled_changes_nothing(
    configure: ConfigWriter,
    dirs: Dirs,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    answer: bool | None,
    status: int,
) -> None:
    configure(FILES, registry_text(project("bar", tmp_path / "bar")))
    before = registry_file(dirs).read_text(encoding="utf-8")
    given = answering(monkeypatch)
    given.confirm = answer

    assert main(["prj", "rm", "bar"]) == status

    captured = capsys.readouterr()
    assert captured.out == ""
    assert ("cancelled; " if answer is None else "") + "nothing changed" in (
        captured.err
    )
    assert registry_file(dirs).read_text(encoding="utf-8") == before


def test_rm_without_a_terminal_is_a_usage_error(
    configure: ConfigWriter,
    dirs: Dirs,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure(FILES, registry_text(project("bar", tmp_path / "bar")))
    before = registry_file(dirs).read_text(encoding="utf-8")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    assert main(["prj", "rm", "bar"]) == USAGE

    assert "atc prj rm needs a terminal" in capsys.readouterr().err
    assert registry_file(dirs).read_text(encoding="utf-8") == before


def test_rm_writes_nothing_when_the_file_changed_while_it_asked(
    configure: ConfigWriter,
    dirs: Dirs,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure(FILES, registry_text(project("bar", tmp_path / "bar")))
    path = registry_file(dirs)
    changed = path.read_text(encoding="utf-8") + "# edited meanwhile\n"
    given = answering(monkeypatch)

    def edit() -> None:
        _ = path.write_text(changed, encoding="utf-8")

    given.meanwhile = edit

    assert main(["prj", "rm", "bar"]) == CONFIG

    assert "changed since it was read; nothing was removed" in capsys.readouterr().err
    assert path.read_text(encoding="utf-8") == changed


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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path / "bar")
    wt = tmp_path / "wt" / "feature"
    _ = run_git(["worktree", "add", "-q", "-b", "feature", str(wt)], cwd=repo)
    configure(FILES, registry_text(project("bar", repo)))
    before = registry_file(dirs).read_text(encoding="utf-8")

    given = asking(monkeypatch)

    status = main(["prj", "add", "again", str(wt)])

    assert status == CONFIG
    assert f"{repo}: already registered as project 'bar'" in capsys.readouterr().err
    assert registry_file(dirs).read_text(encoding="utf-8") == before
    assert given.asked == []


@pytest.mark.parametrize("base", ["main~1", "main^", "main@{0}", "HEAD"])
def test_add_refuses_a_base_that_is_not_a_branch(
    configure: ConfigWriter,
    dirs: Dirs,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    base: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path / "bar")
    _ = run_git(["commit", "-q", "--allow-empty", "-m", "second"], cwd=repo)
    configure(FILES, registry_text())
    before = registry_file(dirs).read_text(encoding="utf-8")

    given = asking(monkeypatch)

    status = main(["prj", "add", "bar", str(repo), "--from", base])

    assert status == CONFIG
    assert f"no branch named '{base}'" in capsys.readouterr().err
    assert registry_file(dirs).read_text(encoding="utf-8") == before
    assert given.asked == []


def test_add_stores_the_top_level_of_an_explicit_repo(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path / "bar")
    (repo / "src").mkdir()
    configure(FILES, registry_text())

    _ = asking(monkeypatch)

    status = main(["prj", "add", "bar", str(repo / "src")])

    assert status == OK
    assert load_registry(registry_file(dirs), home).projects["bar"].repo == repo


def test_add_keeps_a_bare_repository_as_it_is(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bare = tmp_path / "bar.git"
    _ = run_git(["clone", "-q", "--bare", str(make_repo(tmp_path / "src")), str(bare)])
    configure(FILES, registry_text())

    _ = asking(monkeypatch)

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

    _ = asking(monkeypatch, wt)

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

    _ = asking(monkeypatch, bare / "refs")

    status = main(["prj", "add", "bar"])

    assert status == OK
    assert load_registry(registry_file(dirs), home).projects["bar"].repo == bare


def test_add_keeps_the_top_level_when_the_git_directory_is_elsewhere(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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

    _ = asking(monkeypatch)

    status = main(["prj", "add", "bar", str(checkout)])

    assert status == OK
    assert load_registry(registry_file(dirs), home).projects["bar"].repo == checkout
