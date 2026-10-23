"""``atc skills ls``: what each account has of every skill, and what else is there."""

from itertools import takewhile
from pathlib import Path

import pytest

from atc.cli import main
from atc.exitcodes import CONFIG, OK
from atc.paths import Dirs
from tests.conftest import ConfigWriter
from tests.helpers import registry, skill_files, skill_md, unit

PERSONAL = "conf/accounts/personal.json"
WORK = "conf/accounts/work.json"
WORK_ACCOUNT = (
    "  work:\n    agent: claude\n    dir: ~/.config/work\n    skills: [review, draft]\n"
)
FILES = {PERSONAL: unit(), WORK: unit(), **skill_files("review", "notes", "draft")}


@pytest.fixture(autouse=True)
def wide_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMNS", "400")


def rows_of(out: str) -> list[dict[str, str]]:
    """Read the table in ``out``: one dict per row, keyed by column heading."""
    lines = out.splitlines()
    first = next(i for i, line in enumerate(lines) if line.split()[:1] == ["Name"])
    headings = lines[first].split()
    starts = [lines[first].index(heading) for heading in headings]
    ends = [*starts[1:], None]
    body = takewhile(str.strip, lines[first + 2 :])
    return [
        {
            heading: line[start:end].strip()
            for heading, start, end in zip(headings, starts, ends, strict=True)
        }
        for line in body
    ]


def set_up(configure: ConfigWriter, home: Path) -> None:
    """Make two accounts that select skills, and the skills they choose from."""
    for directory in (home / ".claude", home / ".config" / "work"):
        directory.mkdir(parents=True)
    configure(FILES, registry("skills: [review, notes]") + WORK_ACCOUNT)


def test_skills_ls_shows_each_accounts_state(
    configure: ConfigWriter,
    home: Path,
    dirs: Dirs,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    set_up(configure, home)
    skills = home / ".claude" / "skills"
    checkout = tmp_path / "checkout" / "notes"
    checkout.mkdir(parents=True)
    for name in ("synced", ".trash"):
        (skills / name).mkdir(parents=True)
    (skills / "notes").symlink_to(checkout)
    _ = main(["sync", "personal"])
    _ = capsys.readouterr()

    status = main(["skills", "ls"])

    out = capsys.readouterr().out
    assert status == OK
    assert rows_of(out) == [
        {
            "Name": "draft",
            "Source": "-",
            "Description": "The draft skill",
            "personal": "",
            "work": "selected",
        },
        {
            "Name": "notes",
            "Source": "-",
            "Description": "The notes skill",
            "personal": "conflict",
            "work": "",
        },
        {
            "Name": "review",
            "Source": "-",
            "Description": "The review skill",
            "personal": "linked",
            "work": "selected",
        },
    ]
    assert out.endswith(
        f"personal: not managed by atc\n  .trash/\n  notes -> {checkout}\n  synced/\n"
    )
    assert (skills / "review").readlink() == dirs.conf_d / "skills" / "review"
    assert not (home / ".config" / "work" / "skills").exists()


def test_skills_ls_takes_the_accounts_to_show(
    configure: ConfigWriter, home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    set_up(configure, home)

    assert main(["skills", "ls", "work"]) == OK

    rows = rows_of(capsys.readouterr().out)
    assert [(row["Name"], row["work"]) for row in rows] == [
        ("draft", "selected"),
        ("notes", ""),
        ("review", "selected"),
    ]
    assert all("personal" not in row for row in rows)


def test_skills_ls_names_the_source_and_cuts_a_long_description(
    configure: ConfigWriter, home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (home / ".claude").mkdir()
    long = "Review a change before it lands, " * 4
    configure(
        {
            "personal/conf/accounts/personal.json": unit(),
            "personal/skills/review/SKILL.md": skill_md("review", f'"{long}"'),
            "bravo/skills/review/SKILL.md": skill_md("review", "Bravo's review"),
        },
        registry('skills: ["@bravo/review"]'),
    )

    assert main(["skills", "ls"]) == OK

    first, second = rows_of(capsys.readouterr().out)
    assert (first["Source"], first["personal"]) == ("bravo", "selected")
    assert first["Description"] == "Bravo's review"
    assert (second["Source"], second["personal"]) == ("personal", "")
    assert len(second["Description"]) == 60
    assert second["Description"] == long.strip()[:59] + "\u2026"


def test_skills_ls_shows_what_is_wrong_with_a_skill_on_standard_error(
    configure: ConfigWriter, home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (home / ".claude").mkdir()
    configure(
        {PERSONAL: unit(), "skills/review/SKILL.md": skill_md("other")}, registry()
    )

    assert main(["skills", "ls"]) == OK

    captured = capsys.readouterr()
    assert "warning: " in captured.err
    assert "'other' differs from the directory name 'review'" in captured.err
    assert "warning" not in captured.out


def test_skills_ls_reports_an_account_whose_directory_is_missing(
    configure: ConfigWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(FILES, registry("skills: [review]"))

    status = main(["skills", "ls"])

    captured = capsys.readouterr()
    assert status == CONFIG
    assert captured.out == ""
    assert "error: account 'personal': " in captured.err


def test_skills_ls_reports_an_unknown_account(
    configure: ConfigWriter, home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    set_up(configure, home)

    assert main(["skills", "ls", "nobody"]) == CONFIG

    assert "no account named 'nobody'" in capsys.readouterr().err


def test_src_check_fails_when_a_selected_skill_is_gone(
    configure: ConfigWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(
        {PERSONAL: unit(), "skills/review/SKILL.md": skill_md("review")},
        registry("skills: [review, ghost]"),
    )

    status = main(["src", "check"])

    err = capsys.readouterr().err
    assert status == CONFIG
    assert "account 'personal': skills: 'ghost': no such skill" in err
    assert "ok:" not in err
