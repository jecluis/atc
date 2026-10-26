"""``atc vars``: list the variables of the registry."""

import re

import pytest

from atc.cli import main
from atc.exitcodes import OK
from atc.paths import Dirs
from atc.registry import REGISTRY_FILE
from tests.conftest import ConfigWriter
from tests.helpers import VARIABLE_FILES, VARIABLE_REGISTRY, unit


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
    assert "8 variables are undefined\n" in captured.err


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
