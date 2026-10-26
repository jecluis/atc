"""``atc vars``: list, define and remove the variables of the registry."""

import re
import sys
from pathlib import Path

import pytest

from atc.cli import main
from atc.cli.values import RUN_NOTE, SYNC_NOTE
from atc.exitcodes import CONFIG, INTERRUPTED, OK, USAGE
from atc.paths import Dirs
from atc.registry import REGISTRY_FILE, load_registry
from tests.conftest import ConfigWriter
from tests.helpers import VARIABLE_FILES, VARIABLE_REGISTRY, answering, unit


@pytest.fixture(autouse=True)
def wide_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMNS", "400")


def rows_of(out: str) -> list[list[str]]:
    """Read the table of `atc vars ls` by the columns of its heading."""
    lines = out.splitlines()
    start = next(i for i, line in enumerate(lines) if line.split()[:1] == ["Variable"])
    # A heading is words with single spaces between, such as "Used by".
    starts = [m.start() for m in re.finditer(r"\S+(?: \S+)*", lines[start])]
    ends = [*starts[1:], None]
    return [
        [line[a:b].strip() for a, b in zip(starts, ends, strict=True)]
        for line in lines[start + 2 :]
        if line.strip()
    ]


def test_ls_shows_each_variable_with_its_owner_value_and_users(
    configure: ConfigWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(VARIABLE_FILES, VARIABLE_REGISTRY)

    status = main(["vars", "ls"])

    captured = capsys.readouterr()
    assert status == OK
    rows = rows_of(captured.out)
    assert rows[:5] == [
        ["vars.root", "machine", "/r", "common/base.json, status.in.sh"],
        ["vars.host", "machine", "undefined", "projects/bar.json"],
        ["vars.alpha", "machine", "a", "-"],
        ["vars.zeta", "machine", "z", "-"],
        ["account.vars.scratch", "account personal", "s", "common/base.json"],
    ]
    assert rows[8][:3] == ["account.vars.scratch", "account work", "undefined"]
    assert rows[-1] == ["project.vars.extra", "project baz", "e", "-"]
    assert len(rows) == 14
    assert "8 variables are undefined; atc vars set asks for them" in captured.err


def test_ls_names_three_users_and_counts_the_rest(
    configure: ConfigWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    files = {
        "conf/accounts/personal.json": unit(
            imports=[f"common/{n}.json" for n in "abcde"]
        ),
        **{
            f"conf/common/{n}.json": unit({"env": {n.upper(): "{{vars.x}}"}})
            for n in "abcde"
        },
    }
    registry = """\
        version: 1
        vars: {x: '1'}
        default-account: personal
        accounts:
          personal: {agent: claude, dir: ~/.claude}
    """
    configure(files, registry)

    assert main(["vars", "ls"]) == OK

    (row,) = rows_of(capsys.readouterr().out)
    assert row[3] == "common/a.json, common/b.json, common/c.json and 2 more"


def test_ls_with_nothing_undefined_counts_nothing(
    configure: ConfigWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    registry = """\
        version: 1
        vars: {x: '1'}
        default-account: personal
        accounts:
          personal: {agent: claude, dir: ~/.claude}
    """
    configure({"conf/accounts/personal.json": unit()}, registry)

    assert main(["vars", "ls"]) == OK

    captured = capsys.readouterr()
    assert rows_of(captured.out) == [["vars.x", "machine", "1", "-"]]
    assert "undefined" not in captured.err


def test_ls_with_no_variables_says_so(
    configure: ConfigWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    configure({"conf/accounts/personal.json": unit()})

    assert main(["vars", "ls"]) == OK

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no variables are defined or used" in captured.err


def test_ls_without_a_registry_says_how_to_make_one(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["vars", "ls"]) == OK

    captured = capsys.readouterr()
    assert captured.out == ""
    path = dirs.config_dir / REGISTRY_FILE
    assert f"no variables: {path} does not exist; atc account add creates it" in (
        captured.err
    )


def test_ls_warns_about_units_it_cannot_read_and_lists_the_rest(
    configure: ConfigWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    registry = """\
        version: 1
        vars: {x: '1'}
        default-account: personal
        accounts:
          personal: {agent: claude, dir: ~/.claude, unit: accounts/gone.json}
    """
    configure({"conf/accounts/other.json": unit()}, registry)

    assert main(["vars", "ls"]) == OK

    captured = capsys.readouterr()
    assert rows_of(captured.out) == [["vars.x", "machine", "1", "-"]]
    assert "account 'personal': the variables of its units are left out" in (
        captured.err
    )


def registry_path(dirs: Dirs) -> Path:
    return dirs.config_dir / REGISTRY_FILE


def test_set_writes_a_machine_wide_variable_before_the_accounts(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure(
        VARIABLE_FILES,
        VARIABLE_REGISTRY.replace("    vars: {root: /r, zeta: z, alpha: a}\n", ""),
    )

    assert main(["vars", "set", "host", "example.org"]) == OK

    captured = capsys.readouterr()
    path = registry_path(dirs)
    assert captured.out == f"vars.host: set to example.org for machine in {path}\n"
    assert "warning: nothing uses" not in captured.err
    # Only a project's unit uses it, so the next run is what it takes.
    assert RUN_NOTE in captured.err
    assert SYNC_NOTE not in captured.err
    text = path.read_text(encoding="utf-8")
    assert "default-account: personal\nvars:\n  host: example.org\naccounts:\n" in (
        text
    )
    assert load_registry(path, home).vars == {"host": "example.org"}


def test_set_says_what_a_replaced_value_was(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure(VARIABLE_FILES, VARIABLE_REGISTRY)

    assert main(["vars", "set", "scratch", "~/s", "-a", "personal"]) == OK

    path = registry_path(dirs)
    where = f"for account personal in {path}"
    expected = f"account.vars.scratch: set to {home}/s {where}, was s\n"
    assert capsys.readouterr().out == expected
    registry = load_registry(path, home)
    assert registry.accounts["personal"].vars == {"scratch": f"{home}/s"}


def test_set_for_a_project_says_it_takes_effect_at_the_next_run(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure(VARIABLE_FILES, VARIABLE_REGISTRY)

    assert main(["vars", "set", "port", "8080", "-p", "bar"]) == OK

    err = capsys.readouterr().err
    assert RUN_NOTE in err
    assert "atc sync" not in err
    assert load_registry(registry_path(dirs), home).projects["bar"].vars == {
        "port": "8080"
    }


def test_set_warns_about_a_variable_nothing_uses_and_writes_it(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure(VARIABLE_FILES, VARIABLE_REGISTRY)

    assert main(["vars", "set", "scrach", "x", "-a", "work"]) == OK

    err = capsys.readouterr().err
    assert "nothing uses account.vars.scrach for account work; is the name right?" in (
        err
    )
    assert SYNC_NOTE not in err
    assert RUN_NOTE not in err
    registry = load_registry(registry_path(dirs), home)
    assert registry.accounts["work"].vars == {"scrach": "x"}


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["Bad", "x"], "'Bad' is not a valid variable name"),
        (["ok", "a {{b}}"], "the value must not contain '{{'"),
        (["ok", " "], "a value is needed"),
        (["ok", "x", "-a", "personal", "-p", "bar"], "give -a or -p, not both"),
    ],
)
def test_set_refuses_what_cannot_be_written_as_a_usage_error(
    configure: ConfigWriter,
    dirs: Dirs,
    capsys: pytest.CaptureFixture[str],
    args: list[str],
    message: str,
) -> None:
    configure(VARIABLE_FILES, VARIABLE_REGISTRY)
    before = registry_path(dirs).read_text(encoding="utf-8")

    assert main(["vars", "set", *args]) == USAGE

    assert message in capsys.readouterr().err
    assert registry_path(dirs).read_text(encoding="utf-8") == before


def test_set_for_an_unknown_account_names_the_accounts(
    configure: ConfigWriter, dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(VARIABLE_FILES, VARIABLE_REGISTRY)
    before = registry_path(dirs).read_text(encoding="utf-8")

    assert main(["vars", "set", "x", "1", "-a", "ghost"]) == CONFIG

    assert "no account named 'ghost'" in capsys.readouterr().err
    assert registry_path(dirs).read_text(encoding="utf-8") == before


def test_set_without_a_value_asks_from_the_current_one(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure(VARIABLE_FILES, VARIABLE_REGISTRY)
    given = answering(monkeypatch)
    given.path = "~/scratch"

    assert main(["vars", "set", "scratch", "-a", "personal"]) == OK

    assert given.asked == ["account.vars.scratch for account personal: default=s"]
    assert given.check is not None
    assert given.check("") == "a value is needed"
    registry = load_registry(registry_path(dirs), home)
    assert registry.accounts["personal"].vars == {"scratch": f"{home}/scratch"}


def test_set_without_a_value_or_a_terminal_is_a_usage_error(
    configure: ConfigWriter,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure(VARIABLE_FILES, VARIABLE_REGISTRY)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    assert main(["vars", "set", "scratch", "-a", "personal"]) == USAGE

    assert "give the VALUE" in capsys.readouterr().err


def test_set_without_a_name_asks_for_each_undefined_one_and_skips_empty_answers(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure(VARIABLE_FILES, VARIABLE_REGISTRY)
    given = answering(monkeypatch)
    given.path = ""
    given.paths = {"vars.host ": "h", "account.vars.notes for account work": "n"}

    assert main(["vars", "set"]) == OK

    asked = [
        ("vars.host", "machine", "projects/bar.json"),
        ("account.vars.notes", "account personal", "status.in.sh"),
        ("account.vars.cache", "account personal", "projects/bar.json"),
        ("account.vars.other", "account personal", "projects/baz.json"),
        ("account.vars.scratch", "account work", "accounts/work.json"),
        ("account.vars.notes", "account work", "status.in.sh"),
        ("account.vars.cache", "account work", "projects/bar.json"),
        ("project.vars.port", "project bar", "projects/bar.json"),
    ]
    assert given.asked == [
        f"{reference} for {owner}, used by {users} (empty to skip): default="
        for reference, owner, users in asked
    ]
    assert given.check is not None
    assert given.check("") is None
    assert given.check("{{x}}") == "the value must not contain '{{'"
    captured = capsys.readouterr()
    path = registry_path(dirs)
    assert captured.out == (
        f"vars.host: set to h for machine in {path}\n"
        f"account.vars.notes: set to n for account work in {path}\n"
    )
    # A project's unit uses the one, a templated asset the other.
    assert RUN_NOTE in captured.err
    assert SYNC_NOTE in captured.err
    assert "6 variables are still undefined" in captured.err
    registry = load_registry(registry_path(dirs), home)
    assert registry.vars["host"] == "h"
    assert registry.accounts["work"].vars == {"notes": "n"}


def test_set_without_a_name_for_one_owner_asks_only_for_its_variables(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure(VARIABLE_FILES, VARIABLE_REGISTRY)
    given = answering(monkeypatch)
    given.path = "1"

    assert main(["vars", "set", "-p", "bar"]) == OK

    assert [q.split(" for ")[0] for q in given.asked] == ["project.vars.port"]
    assert load_registry(registry_path(dirs), home).projects["bar"].vars == {
        "port": "1"
    }


def test_set_without_a_name_cancelled_writes_nothing(
    configure: ConfigWriter,
    dirs: Dirs,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure(VARIABLE_FILES, VARIABLE_REGISTRY)
    before = registry_path(dirs).read_text(encoding="utf-8")
    given = answering(monkeypatch)
    given.path = "x"
    given.paths = {"account.vars.cache for account work": None}

    assert main(["vars", "set"]) == INTERRUPTED

    assert "cancelled; nothing changed" in capsys.readouterr().err
    assert registry_path(dirs).read_text(encoding="utf-8") == before


def test_set_without_a_name_with_nothing_undefined_says_so(
    configure: ConfigWriter,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure({"conf/accounts/personal.json": unit()})
    given = answering(monkeypatch)

    assert main(["vars", "set"]) == OK

    assert given.asked == []
    assert "no variable is undefined" in capsys.readouterr().err


def test_rm_removes_a_variable_nothing_uses_once_confirmed(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure(VARIABLE_FILES, VARIABLE_REGISTRY)
    given = answering(monkeypatch)

    assert main(["vars", "rm", "extra", "-p", "baz"]) == OK

    assert given.asked == ["Remove extra from project baz? default=False"]
    path = registry_path(dirs)
    assert capsys.readouterr().out == (
        f"project.vars.extra: removed from project baz in {path}\n"
    )
    assert load_registry(path, home).projects["baz"].vars == {"port": "1"}


def test_rm_refuses_a_variable_in_use_without_force(
    configure: ConfigWriter,
    dirs: Dirs,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure(VARIABLE_FILES, VARIABLE_REGISTRY)
    before = registry_path(dirs).read_text(encoding="utf-8")
    given = answering(monkeypatch)

    assert main(["vars", "rm", "root"]) == CONFIG

    err = capsys.readouterr().err
    assert "vars.root for machine is used by common/base.json, status.in.sh" in err
    assert "give --force to remove it anyway" in err
    assert given.asked == []
    assert registry_path(dirs).read_text(encoding="utf-8") == before


def test_rm_with_force_removes_a_variable_in_use_and_says_what_loses_it(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure(VARIABLE_FILES, VARIABLE_REGISTRY)
    _ = answering(monkeypatch)

    assert main(["vars", "rm", "scratch", "-a", "personal", "--force"]) == OK

    err = capsys.readouterr().err
    assert "common/base.json will find account.vars.scratch undefined" in err
    assert SYNC_NOTE in err
    assert RUN_NOTE not in err
    registry = load_registry(registry_path(dirs), home)
    assert registry.accounts["personal"].vars == {}


def test_rm_of_a_variable_not_defined_there_lists_the_ones_that_are(
    configure: ConfigWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(VARIABLE_FILES, VARIABLE_REGISTRY)

    assert main(["vars", "rm", "nope", "-p", "baz"]) == CONFIG

    assert "no variable named 'nope'; the variables there are port, extra" in (
        capsys.readouterr().err
    )


@pytest.mark.parametrize(("answer", "status"), [(False, OK), (None, INTERRUPTED)])
def test_rm_answered_no_or_cancelled_changes_nothing(
    configure: ConfigWriter,
    dirs: Dirs,
    monkeypatch: pytest.MonkeyPatch,
    answer: bool | None,
    status: int,
) -> None:
    configure(VARIABLE_FILES, VARIABLE_REGISTRY)
    before = registry_path(dirs).read_text(encoding="utf-8")
    given = answering(monkeypatch)
    given.confirm = answer

    assert main(["vars", "rm", "zeta"]) == status

    assert registry_path(dirs).read_text(encoding="utf-8") == before


def test_rm_without_a_terminal_is_a_usage_error(
    configure: ConfigWriter,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure(VARIABLE_FILES, VARIABLE_REGISTRY)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    assert main(["vars", "rm", "zeta"]) == USAGE

    assert "atc vars rm needs a terminal" in capsys.readouterr().err


def test_set_without_a_name_or_a_terminal_is_a_usage_error(
    configure: ConfigWriter,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure(VARIABLE_FILES, VARIABLE_REGISTRY)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    assert main(["vars", "set"]) == USAGE

    assert "give the NAME and the VALUE" in capsys.readouterr().err


def test_set_without_a_value_cancelled_writes_nothing(
    configure: ConfigWriter,
    dirs: Dirs,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure(VARIABLE_FILES, VARIABLE_REGISTRY)
    before = registry_path(dirs).read_text(encoding="utf-8")
    given = answering(monkeypatch)
    given.path = None

    assert main(["vars", "set", "scratch", "-a", "work"]) == INTERRUPTED

    assert "cancelled; nothing changed" in capsys.readouterr().err
    assert registry_path(dirs).read_text(encoding="utf-8") == before


def test_set_without_a_name_for_an_unknown_account_names_the_accounts(
    configure: ConfigWriter,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure(VARIABLE_FILES, VARIABLE_REGISTRY)
    given = answering(monkeypatch)

    assert main(["vars", "set", "-a", "ghost"]) == CONFIG

    assert "no account named 'ghost'" in capsys.readouterr().err
    assert given.asked == []


def test_rm_of_the_last_machine_wide_variable_takes_vars_with_it(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = """\
        version: 1
        # machine-wide
        vars: {only: x}
        default-account: personal
        accounts:
          personal: {agent: claude, dir: ~/.claude}
    """
    configure({"conf/accounts/personal.json": unit()}, registry)
    _ = answering(monkeypatch)

    assert main(["vars", "rm", "only"]) == OK

    assert registry_path(dirs).read_text(encoding="utf-8") == (
        "version: 1\ndefault-account: personal\naccounts:\n"
        "  personal: {agent: claude, dir: ~/.claude}\n"
    )
    assert load_registry(registry_path(dirs), home).vars == {}


def test_rm_writes_nothing_when_the_file_changed_while_it_asked(
    configure: ConfigWriter,
    dirs: Dirs,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure(VARIABLE_FILES, VARIABLE_REGISTRY)
    path = registry_path(dirs)
    changed = path.read_text(encoding="utf-8") + "# edited meanwhile\n"
    given = answering(monkeypatch)

    def edit() -> None:
        _ = path.write_text(changed, encoding="utf-8")

    given.meanwhile = edit

    assert main(["vars", "rm", "zeta"]) == CONFIG

    assert "changed since it was read; nothing was removed" in capsys.readouterr().err
    assert path.read_text(encoding="utf-8") == changed


def test_rm_refuses_when_what_uses_it_cannot_all_be_known(
    configure: ConfigWriter,
    dirs: Dirs,
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry = """\
        version: 1
        vars: {x: '1'}
        default-account: personal
        accounts:
          personal: {agent: claude, dir: ~/.claude, unit: accounts/gone.json}
    """
    configure({"conf/accounts/other.json": unit()}, registry)
    before = registry_path(dirs).read_text(encoding="utf-8")
    given = answering(monkeypatch)

    assert main(["vars", "rm", "x"]) == CONFIG

    err = capsys.readouterr().err
    assert "account 'personal': the variables of its units are left out" in err
    assert "what uses vars.x for machine cannot all be known" in err
    assert given.asked == []
    assert registry_path(dirs).read_text(encoding="utf-8") == before

    assert main(["vars", "rm", "x", "--force"]) == OK

    assert load_registry(registry_path(dirs), home).vars == {}


def test_set_of_a_variable_accounts_and_projects_use_names_both_notes(
    configure: ConfigWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    files = {
        **VARIABLE_FILES,
        "conf/projects/baz.json": unit({"env": {"R": "{{vars.root}}"}}),
    }
    configure(files, VARIABLE_REGISTRY)

    assert main(["vars", "set", "root", "/new"]) == OK

    err = capsys.readouterr().err
    assert SYNC_NOTE in err
    assert RUN_NOTE in err


def test_set_says_both_notes_when_what_uses_it_is_unknown(
    configure: ConfigWriter, dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(VARIABLE_FILES, VARIABLE_REGISTRY)
    _ = (dirs.conf_d / "conf").rename(dirs.conf_d / "elsewhere")
    _ = (dirs.conf_d / "assets").rename(dirs.conf_d / "spare")

    assert main(["vars", "set", "root", "/new"]) == OK

    err = capsys.readouterr().err
    assert "cannot tell what uses the variable" in err
    assert SYNC_NOTE in err
    assert RUN_NOTE in err


def test_set_of_a_variable_only_an_accounts_unit_uses_names_atc_sync(
    configure: ConfigWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(VARIABLE_FILES, VARIABLE_REGISTRY)

    assert main(["vars", "set", "scratch", "/s", "-a", "work"]) == OK

    err = capsys.readouterr().err
    assert SYNC_NOTE in err
    assert RUN_NOTE not in err


def test_set_says_both_notes_when_some_units_cannot_be_read(
    configure: ConfigWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    registry = VARIABLE_REGISTRY.replace(
        "unit: accounts/work.json", "unit: accounts/gone.json"
    )
    configure(VARIABLE_FILES, registry)

    assert main(["vars", "set", "host", "h"]) == OK

    err = capsys.readouterr().err
    assert "account 'work': the variables of its units are left out" in err
    assert SYNC_NOTE in err
    assert RUN_NOTE in err
