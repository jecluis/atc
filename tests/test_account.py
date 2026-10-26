"""``atc account``: list, add and remove the accounts of the registry."""

import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from atc.cli import main
from atc.exitcodes import CONFIG, INTERRUPTED, OK, USAGE
from atc.paths import Dirs
from atc.registry import REGISTRY_FILE, load_registry
from atc.ui import PickChoice, Ui
from tests.conftest import ConfigWriter
from tests.helpers import skill_files, unit

FILES = {
    "conf/accounts/personal.json": unit(),
    "conf/accounts/job.json": unit(),
    "conf/accounts/spare.json": unit(),
}
ACCOUNTS = """\
    # this machine
    version: 1
    default-account: personal

    accounts:
      personal: {agent: claude, dir: ~/.claude}
      # the one for work
      work:
        agent: claude
        dir: ~/.config/work
        unit: accounts/job.json
      spare: {agent: claude, dir: ~/.config/spare}  # a third
"""
TAIL = """
    # the end
"""


@pytest.fixture(autouse=True)
def wide_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMNS", "400")


@dataclass
class Answers:
    """What the questions answer, and what they were asked, in order."""

    confirm: bool | None = True
    confirms: dict[str, bool | None] = field(default_factory=dict[str, bool | None])
    """Answers to the yes or no questions that start with each key, else ``confirm``."""
    choose: str | None = None
    path: str | None = "~/.config/new"
    pick: list[str] | None = None
    asked: list[str] = field(default_factory=list[str])
    meanwhile: Callable[[], None] | None = None
    """What happens while a yes or no question is asked."""
    check: Callable[[str], str | None] | None = None
    """What the path question checked its answers with."""
    directories: bool = False
    """Whether the path question completed only directories."""


def answering(monkeypatch: pytest.MonkeyPatch) -> Answers:
    """
    Make stdin and stdout terminals, and answer the questions from the result.

    Call it in the test itself: pytest swaps the streams in for the test's call.
    """
    given = Answers()
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

    def confirm(_self: Ui, question: str, *, default: bool = False) -> bool | None:
        given.asked.append(f"{question} default={default}")
        if given.meanwhile is not None:
            given.meanwhile()
        found = (a for start, a in given.confirms.items() if question.startswith(start))
        return next(found, given.confirm)

    def choose(_self: Ui, question: str, choices: Sequence[str]) -> str | None:
        given.asked.append(f"{question} {list(choices)}")
        return given.choose

    def ask_path(
        _self: Ui,
        question: str,
        *,
        default: str = "",
        check: Callable[[str], str | None] | None = None,
        directories: bool = False,
    ) -> str | None:
        given.asked.append(f"{question} default={default}")
        given.directories = directories
        given.check = check
        return given.path

    def pick(_self: Ui, title: str, choices: Sequence[PickChoice]) -> list[str] | None:
        given.asked.append(f"{title} {[(c.value, c.checked) for c in choices]}")
        return given.pick

    monkeypatch.setattr(Ui, "confirm", confirm)
    monkeypatch.setattr(Ui, "choose", choose)
    monkeypatch.setattr(Ui, "ask_path", ask_path)
    monkeypatch.setattr(Ui, "pick", pick)
    return given


def registry_text(*projects: str) -> str:
    """Write the registry: the accounts, then ``projects`` as lines of the section."""
    if not projects:
        return ACCOUNTS + TAIL
    return ACCOUNTS + "\n    projects:\n" + "".join(projects) + TAIL


def project(name: str, *keys: str) -> str:
    """Write a project entry of the registry, with ``keys`` as further keys."""
    lines = [f"      {name}:\n", f"        repo: ~/src/{name}\n"]
    lines.extend(f"        {key}\n" for key in keys)
    return "".join(lines)


def text_of(dirs: Dirs) -> str:
    return (dirs.config_dir / REGISTRY_FILE).read_text(encoding="utf-8")


def test_ls_lists_the_accounts_in_the_order_of_the_file(
    configure: ConfigWriter, home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(FILES, registry_text())

    assert main(["account", "ls"]) == OK

    lines = [line.split() for line in capsys.readouterr().out.splitlines()]
    heading = lines.index(["Name", "Agent", "Directory", "Unit"])
    rows = [line for line in lines[heading + 2 :] if line]
    assert rows == [
        [
            "personal",
            "(default)",
            "claude",
            f"{home}/.claude",
            "accounts/personal.json",
        ],
        ["work", "claude", f"{home}/.config/work", "accounts/job.json"],
        ["spare", "claude", f"{home}/.config/spare", "accounts/spare.json"],
    ]


def test_acc_is_the_same_command(
    configure: ConfigWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(FILES, registry_text())
    assert main(["account", "ls"]) == OK
    listed = capsys.readouterr().out

    assert main(["acc", "ls"]) == OK

    assert capsys.readouterr().out == listed


def test_ls_without_a_registry_says_so_and_lists_nothing(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["account", "ls"]) == OK

    captured = capsys.readouterr()
    assert captured.out == ""
    path = dirs.config_dir / REGISTRY_FILE
    assert captured.err == (
        f"no accounts are registered: {path} does not exist; "
        "atc account add creates it\n"
    )


def test_rm_removes_an_unused_account_once_confirmed(
    configure: ConfigWriter,
    dirs: Dirs,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers = answering(monkeypatch)
    configure(FILES, registry_text())

    assert main(["account", "rm", "work"]) == OK

    assert answers.asked == ["Remove account work? default=False"]
    path = dirs.config_dir / REGISTRY_FILE
    assert capsys.readouterr().out == f"work: removed from {path}\n"
    expected = (
        "# this machine\nversion: 1\ndefault-account: personal\n\naccounts:\n"
        "  personal: {agent: claude, dir: ~/.claude}\n"
        "  spare: {agent: claude, dir: ~/.config/spare}  # a third\n"
        "\n# the end\n"
    )
    assert text_of(dirs) == expected


@pytest.mark.parametrize(("answer", "status"), [(False, OK), (None, INTERRUPTED)])
def test_rm_changes_nothing_unless_confirmed(
    configure: ConfigWriter,
    dirs: Dirs,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    answer: bool | None,
    status: int,
) -> None:
    answers = answering(monkeypatch)
    configure(FILES, registry_text())
    before = text_of(dirs)
    answers.confirm = answer

    assert main(["account", "rm", "work"]) == status

    assert text_of(dirs) == before
    assert "nothing changed" in capsys.readouterr().err


def test_rm_of_an_unknown_account_lists_the_accounts(
    configure: ConfigWriter, dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(FILES, registry_text())
    before = text_of(dirs)

    assert main(["account", "rm", "nobody"]) == CONFIG

    err = capsys.readouterr().err
    assert "no account named 'nobody'; the accounts are personal, work, spare" in err
    assert text_of(dirs) == before


def test_rm_never_removes_the_only_account(
    configure: ConfigWriter,
    dirs: Dirs,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers = answering(monkeypatch)
    only = (
        "version: 1\ndefault-account: me\naccounts:\n  me: {agent: claude, dir: ~/m}\n"
    )
    configure({"conf/accounts/me.json": unit()}, only)

    assert main(["account", "rm", "me", "--force"]) == CONFIG

    assert "'me' is the only account, and one is needed" in capsys.readouterr().err
    assert answers.asked == []
    assert text_of(dirs) == only


@pytest.mark.parametrize(
    ("name", "projects", "use"),
    [
        ("personal", (), "it is the default account"),
        (
            "work",
            (project("bar", "account: work"),),
            "project 'bar' uses it by default",
        ),
        (
            "work",
            (project("bar", "accounts: [personal, work]"),),
            "project 'bar' allows it",
        ),
    ],
)
def test_rm_of_an_account_in_use_needs_force(
    configure: ConfigWriter,
    dirs: Dirs,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    name: str,
    projects: tuple[str, ...],
    use: str,
) -> None:
    answers = answering(monkeypatch)
    configure(FILES, registry_text(*projects))
    before = text_of(dirs)

    assert main(["account", "rm", name]) == CONFIG

    err = capsys.readouterr().err
    assert f"account '{name}' is in use: {use}; give --force" in err
    assert answers.asked == []
    assert text_of(dirs) == before


def test_rm_force_takes_the_account_out_of_every_place_it_is_used(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers = answering(monkeypatch)
    configure(
        FILES,
        registry_text(
            project("bar", "account: personal", "accounts: [personal, work]"),
            project("baz", "accounts: [work, personal]  # two of them"),
            project("qux"),
        ),
    )
    answers.choose = "work"

    assert main(["account", "rm", "personal", "--force"]) == OK

    # With spare as the default, bar would not allow the account it falls back to.
    assert answers.asked == [
        "The default account in place of personal: ['work']",
        "Remove account personal? default=False",
    ]
    err = capsys.readouterr().err
    assert err.splitlines() == [
        "the default account becomes 'work'",
        "project 'bar' falls back to the default account 'work'",
        "project 'bar' no longer allows it",
        "project 'baz' no longer allows it",
    ]
    registry = load_registry(dirs.config_dir / REGISTRY_FILE, home)
    assert registry.default_account == "work"
    assert list(registry.accounts) == ["work", "spare"]
    assert registry.projects["bar"].account is None
    assert registry.projects["bar"].accounts == ("work",)
    assert registry.projects["baz"].accounts == ("work",)
    assert registry.projects["qux"].accounts is None
    assert "# two of them" in text_of(dirs)


def test_rm_force_refuses_a_project_left_with_no_account_before_asking(
    configure: ConfigWriter,
    dirs: Dirs,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers = answering(monkeypatch)
    configure(
        FILES,
        registry_text(
            project("bar", "account: work", "accounts: [work]"),
            project("baz", "account: work", "accounts: [work]"),
        ),
    )
    before = text_of(dirs)

    assert main(["account", "rm", "work", "--force"]) == CONFIG

    err = capsys.readouterr().err
    assert "account 'work' is the only one that project 'bar', 'baz' allows" in err
    assert answers.asked == []
    assert text_of(dirs) == before


def test_rm_force_refuses_a_result_that_is_not_a_valid_registry_before_asking(
    configure: ConfigWriter,
    dirs: Dirs,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers = answering(monkeypatch)
    # Without its account, bar falls back to personal, which it does not allow.
    configure(
        FILES, registry_text(project("bar", "account: work", "accounts: [work, spare]"))
    )
    before = text_of(dirs)

    assert main(["account", "rm", "work", "--force"]) == CONFIG

    err = capsys.readouterr().err
    assert "the default account 'personal' is not one of the project's accounts" in err
    assert answers.asked == []
    assert text_of(dirs) == before


def test_rm_force_cancelled_at_the_new_default_changes_nothing(
    configure: ConfigWriter,
    dirs: Dirs,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers = answering(monkeypatch)
    configure(FILES, registry_text())
    before = text_of(dirs)
    answers.choose = None

    assert main(["account", "rm", "personal", "--force"]) == INTERRUPTED

    assert "cancelled; nothing changed" in capsys.readouterr().err
    assert len(answers.asked) == 1
    assert text_of(dirs) == before


def test_rm_without_a_terminal_is_a_usage_error(
    configure: ConfigWriter,
    dirs: Dirs,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure(FILES, registry_text())
    before = text_of(dirs)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    assert main(["account", "rm", "work"]) == USAGE

    assert "atc account rm needs a terminal" in capsys.readouterr().err
    assert text_of(dirs) == before


def test_rm_warns_that_the_accounts_directory_is_left_behind(
    configure: ConfigWriter,
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers = answering(monkeypatch)
    configure(FILES, registry_text())
    (home / ".config" / "work").mkdir(parents=True)

    assert main(["account", "rm", "work"]) == OK

    err = capsys.readouterr().err
    assert f"warning: {home}/.config/work is still there, credentials included" in err
    assert answers.asked == ["Remove account work? default=False"]


def test_rm_says_nothing_of_a_directory_that_is_not_there(
    configure: ConfigWriter,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers = answering(monkeypatch)
    configure(FILES, registry_text())

    assert main(["account", "rm", "work"]) == OK

    assert "warning" not in capsys.readouterr().err
    assert len(answers.asked) == 1


def test_rm_force_keeps_the_comments_of_a_block_list_of_accounts(
    configure: ConfigWriter, dirs: Dirs, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = answering(monkeypatch)
    bar = project("bar", "accounts:", "  - personal  # mine", "  - work  # job")
    configure(FILES, registry_text(bar))

    assert main(["account", "rm", "work", "--force"]) == OK

    text = text_of(dirs)
    assert "    accounts:\n      - personal  # mine\n" in text
    assert "work" not in text


def test_rm_force_refuses_when_no_account_can_be_the_default_before_asking(
    configure: ConfigWriter,
    dirs: Dirs,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers = answering(monkeypatch)
    configure(
        FILES,
        registry_text(
            project("bar", "accounts: [personal, work]"),
            project("baz", "accounts: [personal, spare]"),
        ),
    )
    before = text_of(dirs)

    assert main(["account", "rm", "personal", "--force"]) == CONFIG

    err = capsys.readouterr().err
    assert "no other account can be the default in place of 'personal'" in err
    assert answers.asked == []
    assert text_of(dirs) == before


def test_rm_writes_nothing_when_the_file_changed_while_it_asked(
    configure: ConfigWriter,
    dirs: Dirs,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers = answering(monkeypatch)
    configure(FILES, registry_text())
    path = dirs.config_dir / REGISTRY_FILE
    edited = text_of(dirs) + "# edited meanwhile\n"

    def edit() -> None:
        _ = path.write_text(edited, encoding="utf-8")

    answers.meanwhile = edit

    assert main(["account", "rm", "work"]) == CONFIG

    assert "changed since it was read; nothing was removed" in capsys.readouterr().err
    assert text_of(dirs) == edited


def test_rm_force_of_an_account_not_in_use_only_asks_to_confirm(
    configure: ConfigWriter, dirs: Dirs, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers = answering(monkeypatch)
    configure(FILES, registry_text())

    assert main(["account", "rm", "spare", "--force"]) == OK

    assert answers.asked == ["Remove account spare? default=False"]
    assert "spare" not in text_of(dirs)


NEW = {"conf/accounts/new.json": unit()}


def test_add_creates_the_registry_with_the_account_as_its_default(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers = answering(monkeypatch)
    configure(NEW)
    path = dirs.config_dir / REGISTRY_FILE
    path.unlink()

    assert main(["account", "add", "new"]) == OK

    assert answers.asked == ["The directory of account new: default=~/.claude"]
    assert text_of(dirs) == (
        "version: 1\ndefault-account: new\naccounts:\n"
        "  new:\n    agent: claude\n    dir: ~/.config/new\n"
    )
    assert path.stat().st_mode & 0o777 == 0o600
    captured = capsys.readouterr()
    assert captured.out == f"new: added to {path}, as the default account\n"
    missing = (
        f"{home}/.config/new does not exist yet, and atc sync needs it: log in to "
        "the account first, which makes the agent create it"
    )
    assert captured.err.splitlines() == [missing, "then run atc sync"]
    assert answers.directories


def test_add_puts_the_account_after_the_others_and_keeps_the_rest(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers = answering(monkeypatch)
    configure({**FILES, **NEW}, registry_text())
    (home / ".config" / "new").mkdir(parents=True)
    before = text_of(dirs)

    assert main(["account", "add", "new"]) == OK

    assert answers.asked == [
        "The directory of account new: default=~/.config/new/claude"
    ]
    added = "  new:\n    agent: claude\n    dir: ~/.config/new\n"
    third = "  spare: {agent: claude, dir: ~/.config/spare}  # a third\n"
    assert text_of(dirs) == before.replace(third, third + added)
    captured = capsys.readouterr()
    assert captured.out == f"new: added to {dirs.config_dir / REGISTRY_FILE}\n"
    assert captured.err == (
        "next, run atc sync, for every account: each account's settings keep its "
        "sessions out of the other accounts' directories\n"
    )


@pytest.mark.parametrize(
    ("unit_option", "written"),
    [
        ([], None),
        (["-u", "accounts/new.json"], None),
        (["--unit", "accounts/job.json"], "accounts/job.json"),
    ],
)
def test_add_writes_the_unit_only_when_it_is_not_the_default(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    unit_option: list[str],
    written: str | None,
) -> None:
    _ = answering(monkeypatch)
    configure({**FILES, **NEW}, registry_text())

    assert main(["account", "add", "new", *unit_option]) == OK

    account = load_registry(dirs.config_dir / REGISTRY_FILE, home).accounts["new"]
    assert account.unit == (written or "accounts/new.json")
    assert ("unit:" in text_of(dirs).split("  new:")[1]) is (written is not None)


def test_add_of_a_unit_no_source_has_lists_the_account_units_before_asking(
    configure: ConfigWriter,
    dirs: Dirs,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers = answering(monkeypatch)
    configure(FILES, registry_text())
    before = text_of(dirs)

    assert main(["account", "add", "new"]) == CONFIG

    assert capsys.readouterr().err == (
        "error: 'accounts/new.json': no config source has it; the account units "
        "there are accounts/job.json, accounts/personal.json, accounts/spare.json\n"
    )
    assert answers.asked == []
    assert text_of(dirs) == before


def test_add_takes_a_unit_of_a_named_source(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers = answering(monkeypatch)
    both = {"a/conf/accounts/new.json": unit(), "b/conf/accounts/new.json": unit()}
    configure(both)
    (dirs.config_dir / REGISTRY_FILE).unlink()

    assert main(["account", "add", "new"]) == CONFIG
    assert "add an @name/ prefix to say which" in capsys.readouterr().err
    assert answers.asked == []

    assert main(["account", "add", "new", "-u", "@b/accounts/new.json"]) == OK
    account = load_registry(dirs.config_dir / REGISTRY_FILE, home).accounts["new"]
    assert account.unit == "@b/accounts/new.json"


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("New", "'New' is not a valid account name"),
        ("auto", "'auto' is reserved for -a auto"),
        ("work", "/accounts/work: an account of that name exists already"),
    ],
)
def test_add_refuses_a_name_before_asking(
    configure: ConfigWriter,
    dirs: Dirs,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    name: str,
    message: str,
) -> None:
    answers = answering(monkeypatch)
    configure({**FILES, **NEW}, registry_text())
    before = text_of(dirs)

    assert main(["account", "add", name]) == CONFIG

    assert message in capsys.readouterr().err
    assert answers.asked == []
    assert text_of(dirs) == before


def test_add_without_a_terminal_is_a_usage_error(
    configure: ConfigWriter,
    dirs: Dirs,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure({**FILES, **NEW}, registry_text())
    before = text_of(dirs)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)

    assert main(["account", "add", "new"]) == USAGE

    assert "atc account add needs a terminal" in capsys.readouterr().err
    assert text_of(dirs) == before


def test_add_knows_only_the_agents_there_are(
    configure: ConfigWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers = answering(monkeypatch)
    configure({**FILES, **NEW}, registry_text())

    assert main(["account", "add", "new", "--agent", "other"]) == USAGE
    assert answers.asked == []


def test_add_checks_the_directory_against_the_registrys_rules(
    configure: ConfigWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers = answering(monkeypatch)
    configure({**FILES, **NEW}, registry_text())

    assert main(["account", "add", "new"]) == OK

    check = answers.check
    assert check is not None
    assert check("~/new") is None
    assert check(" /abs/new/ ") is None
    assert check("relative") == "'relative': paths must be absolute, or start with ~/"
    assert check("$HOME/x") == "'$HOME/x': paths must be absolute, or start with ~/"
    assert check("") == "'': paths must be absolute, or start with ~/"
    assert check("~/.claude/") == "account 'personal' has that directory"
    assert check("~/.config/work") == "account 'work' has that directory"


@pytest.mark.parametrize(("typed", "written"), [("~/n/", "~/n"), (" /n ", "/n")])
def test_add_writes_the_directory_as_typed_without_a_trailing_slash(
    configure: ConfigWriter,
    dirs: Dirs,
    monkeypatch: pytest.MonkeyPatch,
    typed: str,
    written: str,
) -> None:
    answers = answering(monkeypatch)
    answers.path = typed
    configure({**FILES, **NEW}, registry_text())

    assert main(["account", "add", "new"]) == OK

    assert f"  new:\n    agent: claude\n    dir: {written}\n" in text_of(dirs)


@pytest.mark.parametrize("link", [True, False])
def test_add_offers_the_default_instructions_file_when_there_is_one(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    link: bool,
) -> None:
    answers = answering(monkeypatch)
    answers.confirms = {"Link": link}
    configure({**FILES, **NEW, "assets/AGENTS.in.md": "# {{account.name}}\n"})

    assert main(["account", "add", "new"]) == OK

    assert answers.asked[1:] == [
        "Link AGENTS.in.md as the account's instructions file? default=True"
    ]
    account = load_registry(dirs.config_dir / REGISTRY_FILE, home).accounts["new"]
    assert account.use_default_agents_md is link
    assert ("use-default-agents-md" in text_of(dirs)) is link


def test_add_offers_skills_to_pick_when_there_are_some(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = answering(monkeypatch)
    answers.confirms = {"Pick skills": True}
    answers.pick = ["review"]
    configure({**FILES, **NEW, **skill_files("review", "notes")}, registry_text())

    assert main(["account", "add", "new"]) == OK

    assert answers.asked[1:] == [
        "Pick skills for the account now? default=False",
        "Skills for new [('notes', False), ('review', False)]",
    ]
    account = load_registry(dirs.config_dir / REGISTRY_FILE, home).accounts["new"]
    assert account.skills == ("review",)


@pytest.mark.parametrize("picked", [False, True])
def test_add_writes_no_skills_unless_some_are_picked(
    configure: ConfigWriter,
    dirs: Dirs,
    monkeypatch: pytest.MonkeyPatch,
    picked: bool,
) -> None:
    answers = answering(monkeypatch)
    answers.confirms = {"Pick skills": picked}
    answers.pick = []
    configure({**FILES, **NEW, **skill_files("review")}, registry_text())

    assert main(["account", "add", "new"]) == OK

    assert len(answers.asked) == 2 + picked
    assert "skills" not in text_of(dirs)


def test_add_asks_nothing_of_instructions_or_skills_the_sources_lack(
    configure: ConfigWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers = answering(monkeypatch)
    configure({**FILES, **NEW}, registry_text())

    assert main(["account", "add", "new"]) == OK

    assert len(answers.asked) == 1


@pytest.mark.parametrize("cancelled", ["path", "Link", "Pick skills", "pick"])
def test_add_cancelled_at_any_question_writes_nothing(
    configure: ConfigWriter,
    dirs: Dirs,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    cancelled: str,
) -> None:
    answers = answering(monkeypatch)
    answers.confirms = {"Link": True, "Pick skills": True}
    answers.pick = ["review"]
    if cancelled == "path":
        answers.path = None
    elif cancelled == "pick":
        answers.pick = None
    else:
        answers.confirms[cancelled] = None
    files = {**FILES, **NEW, **skill_files("review"), "assets/AGENTS.in.md": "x\n"}
    configure(files, registry_text())
    before = text_of(dirs)

    assert main(["account", "add", "new"]) == INTERRUPTED

    assert "cancelled; nothing changed" in capsys.readouterr().err
    assert text_of(dirs) == before


def test_add_of_a_unit_its_source_lacks_lists_the_account_units(
    configure: ConfigWriter,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers = answering(monkeypatch)
    configure({"a/conf/accounts/new.json": unit(), "b/conf/accounts/old.json": unit()})

    assert main(["account", "add", "new", "-u", "@b/accounts/typo.json"]) == CONFIG

    err = capsys.readouterr().err
    assert "'@b/accounts/typo.json': no such unit; the account units there are" in err
    assert "@a/accounts/new.json, @b/accounts/old.json" in err
    assert answers.asked == []


def test_add_says_when_no_source_has_an_account_unit(
    configure: ConfigWriter,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _ = answering(monkeypatch)
    configure({"conf/common/base.json": unit()}, registry_text())

    assert main(["account", "add", "new"]) == CONFIG

    assert capsys.readouterr().err == (
        "error: 'accounts/new.json': no config source has it; no source has an "
        "account unit, under conf/accounts/\n"
    )


def test_add_refuses_an_invalid_registry_before_asking(
    configure: ConfigWriter,
    dirs: Dirs,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers = answering(monkeypatch)
    configure({**FILES, **NEW}, "version: 1\naccounts: {}\n")

    assert main(["account", "add", "new"]) == CONFIG

    assert "default-account" in capsys.readouterr().err
    assert answers.asked == []
    assert text_of(dirs) == "version: 1\naccounts: {}\n"


def test_add_creates_the_registry_through_a_link_that_leads_nowhere_yet(
    configure: ConfigWriter, dirs: Dirs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = answering(monkeypatch)
    configure(NEW)
    path = dirs.config_dir / REGISTRY_FILE
    path.unlink()
    target = tmp_path / "dotfiles" / "machine.yaml"
    target.parent.mkdir()
    path.symlink_to(target)

    assert main(["account", "add", "new"]) == OK

    assert path.is_symlink()
    assert "default-account: new" in target.read_text(encoding="utf-8")


def test_add_refuses_a_link_into_a_missing_directory_before_asking(
    configure: ConfigWriter,
    dirs: Dirs,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers = answering(monkeypatch)
    configure(NEW)
    path = dirs.config_dir / REGISTRY_FILE
    path.unlink()
    path.symlink_to(tmp_path / "nowhere" / "machine.yaml")

    assert main(["account", "add", "new"]) == CONFIG

    assert f"{path}: cannot be created: {tmp_path}/nowhere is not a directory" in (
        capsys.readouterr().err
    )
    assert answers.asked == []


def test_add_offers_the_instructions_file_the_registry_names(
    configure: ConfigWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers = answering(monkeypatch)
    files = {**FILES, **NEW, "assets/docs/MINE.md": "x\n", "assets/AGENTS.in.md": "y\n"}
    configure(files, "    default-agents-md: docs/MINE.md\n" + registry_text())

    assert main(["account", "add", "new"]) == OK

    assert answers.asked[1:] == [
        "Link docs/MINE.md as the account's instructions file? default=True"
    ]


def test_add_does_not_offer_an_instructions_file_several_sources_have(
    configure: ConfigWriter,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers = answering(monkeypatch)
    configure(
        {
            "a/conf/accounts/new.json": unit(),
            "a/assets/AGENTS.in.md": "a\n",
            "b/assets/AGENTS.in.md": "b\n",
        }
    )

    assert main(["account", "add", "new"]) == OK

    assert "warning: not offering AGENTS.in.md as the instructions file" in (
        capsys.readouterr().err
    )
    assert len(answers.asked) == 1
