"""``atc skills ls``: what each account has of every skill, and what else is there."""

import sys
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import takewhile
from pathlib import Path

import pytest

from atc.cli import main
from atc.exitcodes import CHECK, CONFIG, INTERRUPTED, OK, USAGE
from atc.paths import Dirs
from atc.registry import REGISTRY_FILE, load_registry
from atc.ui import PickChoice, Ui
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


FRESH = {PERSONAL: unit(), **skill_files("git-commits", "notes", "review")}
HEADER = "# my machine\n"


@dataclass(frozen=True, slots=True)
class Machine:
    """The registry, the sources and the home of a machine with one account."""

    path: Path
    home: Path
    sources: Path

    @property
    def text(self) -> str:
        return self.path.read_text(encoding="utf-8")

    @property
    def selected(self) -> tuple[str, ...]:
        return load_registry(self.path, self.home).accounts["personal"].skills

    @property
    def skills(self) -> Path:
        return self.home / ".claude" / "skills"

    def links(self) -> dict[str, Path]:
        """Read the links in the skills directory: their targets, by name."""
        if not self.skills.exists():
            return {}
        return {entry.name: entry.readlink() for entry in sorted(self.skills.iterdir())}


@pytest.fixture
def machine(dirs: Dirs, home: Path) -> Machine:
    (home / ".claude").mkdir()
    return Machine(dirs.config_dir / REGISTRY_FILE, home, dirs.conf_d)


def pick_gives(
    monkeypatch: pytest.MonkeyPatch, answer: list[str] | None
) -> list[tuple[str, list[PickChoice]]]:
    """Make the picker answer ``answer``; the list fills with what it was asked."""
    asked: list[tuple[str, list[PickChoice]]] = []

    def fake(_self: Ui, title: str, choices: Sequence[PickChoice]) -> list[str] | None:
        asked.append((title, list(choices)))
        return answer

    monkeypatch.setattr(Ui, "pick", fake)
    return asked


def test_add_then_rm_round_trips_the_selection(
    configure: ConfigWriter, machine: Machine, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(FRESH, registry("skills: [review]", top=HEADER))
    before = machine.text

    assert main(["skills", "add", "personal", "notes", "git-commits"]) == OK

    out = capsys.readouterr().out
    assert machine.selected == ("review", "notes", "git-commits")
    assert machine.links() == {
        "review": machine.sources / "skills" / "review",
        "notes": machine.sources / "skills" / "notes",
        "git-commits": machine.sources / "skills" / "git-commits",
    }
    assert f"personal: skills written to {machine.path}\n" in out
    assert f"personal: notes: (none) → {machine.sources}/skills/notes (changed)" in out

    assert main(["skills", "rm", "personal", "git-commits", "notes"]) == OK

    out = capsys.readouterr().out
    assert machine.text == before
    assert list(machine.links()) == ["review"]
    assert f"personal: notes: {machine.sources}/skills/notes → (none) (changed)" in out


def test_add_edits_a_registry_that_is_a_symbolic_link_through_it(
    configure: ConfigWriter, machine: Machine, tmp_path: Path
) -> None:
    configure(FRESH, registry("skills: [review]", top=HEADER))
    dotfiles = tmp_path / "dotfiles"
    dotfiles.mkdir()
    real = dotfiles / "machine.yaml"
    _ = machine.path.replace(real)
    machine.path.symlink_to(real)

    assert main(["skills", "add", "personal", "notes"]) == OK

    assert machine.path.readlink() == real
    assert machine.selected == ("review", "notes")
    assert real.read_text(encoding="utf-8").startswith(HEADER)


def test_add_and_rm_leave_the_accounts_settings_alone(
    configure: ConfigWriter, machine: Machine
) -> None:
    configure(FRESH, registry())

    assert main(["skills", "add", "personal", "review"]) == OK
    assert main(["skills", "rm", "personal", "review"]) == OK

    assert sorted(entry.name for entry in (machine.home / ".claude").iterdir()) == [
        "skills"
    ]


def test_adding_a_skill_that_is_selected_changes_nothing(
    configure: ConfigWriter, machine: Machine, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(FRESH, registry("skills: ['review']", top=HEADER))
    assert main(["skills", "add", "personal", "review"]) == OK
    before = machine.text
    _ = capsys.readouterr()

    assert main(["skills", "add", "personal", "review"]) == OK

    assert machine.text == before
    assert capsys.readouterr().out == "personal: skills unchanged\n"


def test_adding_an_unknown_skill_exits_1_and_changes_nothing(
    configure: ConfigWriter, machine: Machine, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(FRESH, registry("skills: [review]", top=HEADER))
    before = machine.text

    status = main(["skills", "add", "personal", "notes", "ghost"])

    assert status == CONFIG
    assert "error: 'ghost': no such skill" in capsys.readouterr().err
    assert machine.text == before
    assert machine.links() == {}


def test_removing_a_name_that_is_neither_a_skill_nor_selected_is_an_error(
    configure: ConfigWriter, machine: Machine, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(FRESH, registry("skills: [review]", top=HEADER))
    before = machine.text

    assert main(["skills", "rm", "personal", "typo"]) == CONFIG

    assert "error: 'typo': no such skill" in capsys.readouterr().err
    assert machine.text == before


def test_a_selected_entry_that_names_no_skill_can_be_removed(
    configure: ConfigWriter, machine: Machine
) -> None:
    configure(FRESH, registry("skills: [review, ghost]"))

    assert main(["skills", "rm", "personal", "ghost"]) == OK

    assert machine.selected == ("review",)


def test_removing_a_skill_that_is_not_selected_changes_nothing(
    configure: ConfigWriter, machine: Machine, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(FRESH, registry("skills: [review]", top=HEADER))
    assert main(["sync", "personal"]) == OK
    before = machine.text
    _ = capsys.readouterr()

    assert main(["skills", "rm", "personal", "notes"]) == OK

    assert machine.text == before
    assert capsys.readouterr().out == "personal: skills unchanged\n"


def test_a_link_in_the_way_is_a_conflict_that_take_over_replaces(
    configure: ConfigWriter,
    machine: Machine,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure(FRESH, registry())
    elsewhere = tmp_path / "checkout" / "review"
    elsewhere.mkdir(parents=True)
    machine.skills.mkdir()
    (machine.skills / "review").symlink_to(elsewhere)

    assert main(["skills", "add", "personal", "review"]) == CHECK

    out = capsys.readouterr().out
    assert "personal: review: (not made by atc) → " in out
    assert "(conflict; --take-over replaces a file or link, never a directory)" in out
    assert machine.selected == ("review",)
    assert machine.links() == {"review": elsewhere}

    assert main(["skills", "add", "personal", "review", "--take-over"]) == OK

    assert machine.links() == {"review": machine.sources / "skills" / "review"}
    assert "personal: review: (another link) → " in capsys.readouterr().out


def test_two_selected_skills_of_one_name_change_nothing(
    configure: ConfigWriter, machine: Machine, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(
        {
            "personal/conf/accounts/personal.json": unit(),
            "personal/skills/review/SKILL.md": skill_md("review"),
            "bravo/skills/review/SKILL.md": skill_md("review"),
        },
        registry("skills: ['@personal/review']"),
    )
    before = machine.text

    assert main(["skills", "add", "personal", "@bravo/review"]) == CONFIG

    assert "both named 'review'; select one of them" in capsys.readouterr().err
    assert machine.text == before
    assert machine.links() == {}


@pytest.mark.usefixtures("machine")
def test_an_account_that_does_not_exist_is_an_error(
    configure: ConfigWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(FRESH, registry())

    for command in ("add", "rm"):
        assert main(["skills", command, "nobody", "review"]) == CONFIG
    assert main(["skills", "pick", "nobody"]) == CONFIG

    assert capsys.readouterr().err.count("no account named 'nobody'") == 3


def test_an_account_whose_directory_is_missing_is_not_written(
    configure: ConfigWriter, dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(FRESH, registry(top=HEADER))
    path = dirs.config_dir / REGISTRY_FILE
    before = path.read_text(encoding="utf-8")

    assert main(["skills", "add", "personal", "review"]) == CONFIG

    assert "error: account 'personal': " in capsys.readouterr().err
    assert path.read_text(encoding="utf-8") == before


def test_pick_writes_the_selection_and_links_it(
    configure: ConfigWriter,
    machine: Machine,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure(FRESH, registry("skills: [review]", top=HEADER))
    assert main(["sync", "personal"]) == OK
    asked = pick_gives(monkeypatch, ["git-commits"])
    _ = capsys.readouterr()

    assert main(["skills", "pick", "personal"]) == OK

    assert [
        (title, [(c.value, c.checked) for c in found]) for title, found in asked
    ] == [
        (
            "Skills for personal",
            [("git-commits", False), ("notes", False), ("review", True)],
        )
    ]
    assert machine.selected == ("git-commits",)
    assert machine.text.startswith(HEADER)
    assert machine.links() == {
        "git-commits": machine.sources / "skills" / "git-commits"
    }
    out = capsys.readouterr().out
    assert f"personal: skills written to {machine.path}\n" in out
    assert "personal: review: " in out
    assert "personal: git-commits: (none) → " in out


def test_pick_does_not_write_the_settings(
    configure: ConfigWriter, machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(FRESH, registry())
    _ = pick_gives(monkeypatch, ["review"])

    assert main(["skills", "pick", "personal"]) == OK

    assert not (machine.home / ".claude" / "settings.json").exists()


def test_pick_keeps_the_entries_it_is_not_changing_as_written(
    configure: ConfigWriter, machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(FRESH, registry("skills: [review, notes]", top=HEADER))
    before = machine.text
    _ = pick_gives(monkeypatch, ["notes", "review"])

    assert main(["skills", "pick", "personal"]) == OK

    assert machine.text == before
    assert sorted(machine.links()) == ["notes", "review"]

    _ = pick_gives(monkeypatch, ["git-commits", "notes", "review"])

    assert main(["skills", "pick", "personal"]) == OK

    assert machine.selected == ("review", "notes", "git-commits")


def test_pick_with_nothing_ticked_empties_the_selection(
    configure: ConfigWriter, machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(FRESH, registry("skills: [review]"))
    _ = pick_gives(monkeypatch, [])

    assert main(["skills", "pick", "personal"]) == OK

    assert machine.selected == ()
    assert machine.links() == {}


def test_pick_passes_take_over_on(
    configure: ConfigWriter,
    machine: Machine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure(FRESH, registry())
    machine.skills.mkdir()
    (machine.skills / "review").symlink_to(tmp_path)
    _ = pick_gives(monkeypatch, ["review"])

    assert main(["skills", "pick", "personal"]) == CHECK
    assert main(["skills", "pick", "personal", "--take-over"]) == OK

    assert machine.links() == {"review": machine.sources / "skills" / "review"}


def test_cancelling_the_picker_changes_nothing(
    configure: ConfigWriter,
    machine: Machine,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure(FRESH, registry("skills: [review]", top=HEADER))
    before = machine.text
    _ = pick_gives(monkeypatch, None)

    assert main(["skills", "pick", "personal"]) == INTERRUPTED

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "cancelled; nothing changed\n"
    assert machine.text == before
    assert machine.links() == {}


def test_pick_without_a_terminal_is_a_usage_error(
    configure: ConfigWriter,
    machine: Machine,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure(FRESH, registry("skills: [review]"))
    before = machine.text
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)

    assert main(["skills", "pick", "personal"]) == USAGE

    err = capsys.readouterr().err
    assert (
        "atc skills pick needs a terminal; use atc skills add or atc skills rm" in err
    )
    assert machine.text == before


@pytest.mark.usefixtures("machine")
def test_pick_with_no_skills_to_choose_from_is_an_error(
    configure: ConfigWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    configure({PERSONAL: unit()}, registry())

    assert main(["skills", "pick", "personal"]) == CONFIG

    assert "error: the config sources have no skills to pick from" in (
        capsys.readouterr().err
    )


@pytest.mark.usefixtures("machine")
def test_the_picker_lines_read_name_source_and_description(
    configure: ConfigWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    long = "Review a change before it lands, " * 4
    configure(
        {
            PERSONAL: unit(),
            "skills/review/SKILL.md": skill_md("review", f'"{long}"'),
            "skills/git-commits/SKILL.md": skill_md("git-commits", "Commits"),
            "skills/bare/SKILL.md": skill_md("bare"),
        },
        registry(),
    )
    asked = pick_gives(monkeypatch, [])

    assert main(["skills", "pick", "personal"]) == OK

    assert [choice.label for choice in asked[0][1]] == [
        "bare         -",
        "git-commits  -  Commits",
        f"review       -  {long.strip()[:59]}…",
    ]


def test_an_ambiguous_name_is_written_with_its_source(
    configure: ConfigWriter, machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(
        {
            "personal/conf/accounts/personal.json": unit(),
            "personal/skills/review/SKILL.md": skill_md("review", "Mine"),
            "personal/skills/notes/SKILL.md": skill_md("notes", "Notes"),
            "bravo/skills/review/SKILL.md": skill_md("review", "Bravo's"),
        },
        registry(),
    )

    assert main(["skills", "add", "personal", "review"]) == CONFIG
    assert main(["skills", "add", "personal", "notes", "@bravo/review"]) == OK

    assert machine.selected == ("notes", "@bravo/review")
    assert machine.links() == {
        "notes": machine.sources / "personal" / "skills" / "notes",
        "review": machine.sources / "bravo" / "skills" / "review",
    }

    asked = pick_gives(monkeypatch, ["@personal/review", "notes"])

    assert main(["skills", "pick", "personal"]) == OK

    assert [(c.value, c.label, c.checked) for c in asked[0][1]] == [
        ("notes", "notes   personal  Notes", True),
        ("@bravo/review", "review  bravo     Bravo's", True),
        ("@personal/review", "review  personal  Mine", False),
    ]
    assert machine.selected == ("notes", "@personal/review")
    assert (
        machine.links()["review"] == machine.sources / "personal" / "skills" / "review"
    )
