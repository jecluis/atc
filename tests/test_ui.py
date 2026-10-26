"""Terminal output: markup safety, colour policy, and the JSON contract."""

import json
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import final

import click
import pytest
import questionary
from rich.console import Console

from atc.cli.options import worktrees_table
from atc.jsonval import JsonObject, dumps
from atc.ui import PickChoice, Ui, need_terminal, shown
from atc.worktree import Worktree

YELLOW = "\x1b[33m"
RED = "\x1b[31m"
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def terminal_ui() -> Ui:
    return Ui(Console(force_terminal=True), Console(stderr=True, force_terminal=True))


def test_messages_are_not_read_as_markup(capsys: pytest.CaptureFixture[str]) -> None:
    Ui.default().error("[red]x[/red]")
    assert "[red]x[/red]" in capsys.readouterr().err


def test_error_and_warning_name_themselves_on_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    ui = Ui.default()
    ui.error("broken")
    ui.warning("odd")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: broken\nwarning: odd\n"


def test_info_is_plain_text_on_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    Ui.default().info("ok: [bold]2[/bold] units")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "ok: [bold]2[/bold] units\n"


def test_line_is_plain_text_on_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    Ui.default().line("a: [bold]x[/bold] " + "y" * 120)
    captured = capsys.readouterr()
    assert captured.out == "a: [bold]x[/bold] " + "y" * 120 + "\n"
    assert captured.err == ""


def test_a_long_diagnostic_stays_on_one_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    message = "/very/long/path/" * 20
    Ui.default().error(message)
    assert capsys.readouterr().err == f"error: {message}\n"


def test_json_is_canonical_when_stdout_is_not_a_terminal(
    capsys: pytest.CaptureFixture[str],
) -> None:
    Ui.default().json({"b": 1, "a": [True]})
    assert capsys.readouterr().out == dumps({"b": 1, "a": [True]})


def test_json_is_highlighted_on_a_terminal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    terminal_ui().json({"b": 1, "a": [True]})
    assert "\x1b[" in capsys.readouterr().out


def test_json_on_a_narrow_terminal_is_not_wrapped(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    value: JsonObject = {"key": "x" * 110, "other": ["y" * 60]}
    ui = Ui(
        Console(force_terminal=True, width=40),
        Console(stderr=True, force_terminal=True),
    )
    ui.json(value)
    assert json.loads(ANSI.sub("", capsys.readouterr().out)) == value


def test_diagnostics_are_coloured_on_a_terminal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    ui = terminal_ui()
    ui.warning("w")
    ui.error("e")
    err = capsys.readouterr().err
    assert YELLOW in err
    assert RED in err


def test_no_color_is_honoured(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "xterm-256color")
    terminal_ui().warning("w")
    err = capsys.readouterr().err
    assert YELLOW not in err
    assert "warning:" in err


DIFF = ["--- a (live)", "+++ a (new)", "@@ -1 +1 @@", '-"[red]x"', '+"[bold]y"']


def test_diff_lines_are_plain_text_when_stdout_is_not_a_terminal(
    capsys: pytest.CaptureFixture[str],
) -> None:
    Ui.default().diff(DIFF)
    captured = capsys.readouterr()
    assert captured.out == "".join(f"{line}\n" for line in DIFF)
    assert captured.err == ""


def test_diff_lines_are_coloured_on_a_terminal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    terminal_ui().diff(DIFF)
    out = capsys.readouterr().out
    assert RED in out
    assert "\x1b[32m" in out
    assert ANSI.sub("", out) == "".join(f"{line}\n" for line in DIFF)


def test_diff_shows_control_characters_as_escapes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    terminal_ui().diff(["+a\x1b[2Jb\x07"])
    out = ANSI.sub("", capsys.readouterr().out)
    assert out == "+a\\x1b[2Jb\\x07\n"


def test_error_and_warning_show_control_characters_as_escapes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    ui = terminal_ui()
    ui.error("a\x1b[2Jb\x07")
    ui.warning("c\x1b]0;title\x07")

    err = capsys.readouterr().err
    assert ANSI.sub("", err) == "error: a\\x1b[2Jb\\x07\nwarning: c\\x1b]0;title\\x07\n"


def test_info_shows_control_characters_as_escapes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    Ui.default().info("a\x1b[2Jb")

    assert capsys.readouterr().err == "a\\x1b[2Jb\n"


def test_table_cells_show_control_characters_as_escapes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    trees = [Worktree(Path("/w/a\x1b[2Jb"), "main", "0123456789")]

    Ui.default().table(worktrees_table(trees))

    out = capsys.readouterr().out
    assert "\x1b" not in out
    assert "/w/a\\x1b[2Jb" in out


def test_shown_text_is_neither_markup_nor_control_characters() -> None:
    text = shown("[red]a\x1b[2Jb\tc", "dim")

    assert text.plain == "[red]a\\x1b[2Jb\tc"
    assert text.style == "dim"


def test_line_shows_control_characters_as_escapes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    Ui.default().line("personal: failed (git: a\x1b[2Jb\x07)")

    assert capsys.readouterr().out == "personal: failed (git: a\\x1b[2Jb\\x07)\n"


def test_dim_is_plain_text_on_stdout_when_it_is_not_a_terminal(
    capsys: pytest.CaptureFixture[str],
) -> None:
    Ui.default().dim("  notes -> [bold]x[/bold]")
    captured = capsys.readouterr()
    assert captured.out == "  notes -> [bold]x[/bold]\n"
    assert captured.err == ""


def test_dim_is_dimmed_on_a_terminal_and_shows_control_characters(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    terminal_ui().dim("a\x1b[2Jb")
    out = capsys.readouterr().out
    assert "\x1b[2m" in out
    assert ANSI.sub("", out) == "a\\x1b[2Jb\n"


def test_verdict_is_plain_text_on_stdout_when_it_is_not_a_terminal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    ui = Ui.default()
    ui.verdict(True, "a [bold]is[/bold] fine")
    ui.verdict(False, "b is not")
    captured = capsys.readouterr()
    assert captured.out == "PASS  a [bold]is[/bold] fine\nFAIL  b is not\n"
    assert captured.err == ""


def test_verdict_is_green_or_red_on_a_terminal_and_shows_control_characters(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    ui = terminal_ui()
    ui.verdict(True, "a\x1b[2Jb")
    ui.verdict(False, "c")
    out = capsys.readouterr().out
    assert "\x1b[32mPASS\x1b[0m" in out
    assert "\x1b[31mFAIL\x1b[0m" in out
    assert ANSI.sub("", out) == "PASS  a\\x1b[2Jb\nFAIL  c\n"


type Answer = list[str] | str | bool | BaseException | None
"""
What a question gives: the values ticked, the choice, yes or no, an interrupt,
or no answer.
"""

type Asked = list[tuple[str, list[tuple[str, str, bool]]]]
"""What a picker was asked: its title, then each line's label, value and tick."""


@final
class FakeQuestion:
    """What a questionary question builds, answering without a terminal."""

    def __init__(self, answer: Answer) -> None:
        self.answer = answer

    def unsafe_ask(self) -> list[str] | str | bool | None:
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


def answering(answer: Answer) -> Callable[..., FakeQuestion]:
    """Make a stand-in for a questionary question builder, answering ``answer``."""

    def build(*_args: object, **_kwargs: object) -> FakeQuestion:
        return FakeQuestion(answer)

    return build


def ask_through(
    monkeypatch: pytest.MonkeyPatch, answer: Answer, asked: Asked | None = None
) -> None:
    """Make stdin and stdout terminals, and have the picker answer ``answer``."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

    def checkbox(title: str, choices: list[questionary.Choice]) -> FakeQuestion:
        if asked is not None:
            lines = [(str(c.title), str(c.value), bool(c.checked)) for c in choices]
            asked.append((title, lines))
        return FakeQuestion(answer)

    monkeypatch.setattr(questionary, "checkbox", checkbox)


CHOICES = [PickChoice("a", "a  x  The a", checked=True), PickChoice("b", "b  y", False)]


def test_pick_gives_the_values_that_were_ticked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: Asked = []
    ask_through(monkeypatch, ["b", "a"], asked)

    assert Ui.default().pick("Skills", CHOICES) == ["b", "a"]
    assert asked == [("Skills", [("a  x  The a", "a", True), ("b  y", "b", False)])]


def test_pick_can_be_confirmed_with_nothing_ticked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ask_through(monkeypatch, [])

    assert Ui.default().pick("Skills", CHOICES) == []


@pytest.mark.parametrize("answer", [None, KeyboardInterrupt()])
def test_pick_gives_none_when_it_is_cancelled(
    monkeypatch: pytest.MonkeyPatch, answer: Answer
) -> None:
    ask_through(monkeypatch, answer)

    assert Ui.default().pick("Skills", CHOICES) is None


@pytest.mark.parametrize("terminal", ["stdin", "stdout"])
def test_pick_needs_both_stdin_and_stdout_to_be_terminals(
    monkeypatch: pytest.MonkeyPatch, terminal: str
) -> None:
    ask_through(monkeypatch, ["a"])
    stream = sys.stdin if terminal == "stdin" else sys.stdout
    monkeypatch.setattr(stream, "isatty", lambda: False)

    with pytest.raises(click.UsageError, match="needs a terminal; use atc skills add"):
        _ = Ui.default().pick("Skills", CHOICES)


@pytest.mark.parametrize("answer", [True, False])
def test_confirm_gives_the_answer_and_offers_the_default(
    monkeypatch: pytest.MonkeyPatch, answer: bool
) -> None:
    asked: list[tuple[str, bool]] = []

    def confirm(question: str, default: bool) -> FakeQuestion:
        asked.append((question, default))
        return FakeQuestion(answer)

    monkeypatch.setattr(questionary, "confirm", confirm)

    assert Ui.default().confirm("Remove it?") is answer
    assert asked == [("Remove it?", False)]


@pytest.mark.parametrize("answer", [None, KeyboardInterrupt()])
def test_confirm_gives_none_when_it_is_cancelled(
    monkeypatch: pytest.MonkeyPatch, answer: Answer
) -> None:
    monkeypatch.setattr(questionary, "confirm", answering(answer))

    assert Ui.default().confirm("Remove it?", default=True) is None


def test_choose_gives_the_choice_made(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[tuple[str, list[str]]] = []

    def select(question: str, choices: list[str]) -> FakeQuestion:
        asked.append((question, choices))
        return FakeQuestion("b")

    monkeypatch.setattr(questionary, "select", select)

    assert Ui.default().choose("Which?", ("a", "b")) == "b"
    assert asked == [("Which?", ["a", "b"])]


@pytest.mark.parametrize("answer", [None, KeyboardInterrupt()])
def test_choose_gives_none_when_it_is_cancelled(
    monkeypatch: pytest.MonkeyPatch, answer: Answer
) -> None:
    monkeypatch.setattr(questionary, "select", answering(answer))

    assert Ui.default().choose("Which?", ["a"]) is None


@pytest.mark.parametrize("terminal", ["stdin", "stdout"])
def test_need_terminal_needs_both_stdin_and_stdout(
    monkeypatch: pytest.MonkeyPatch, terminal: str
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    need_terminal("atc thing")
    stream = sys.stdin if terminal == "stdin" else sys.stdout
    monkeypatch.setattr(stream, "isatty", lambda: False)

    with pytest.raises(click.UsageError, match=r"^atc thing needs a terminal$"):
        need_terminal("atc thing")
    with pytest.raises(click.UsageError, match=r"needs a terminal; do it later$"):
        need_terminal("atc thing", "do it later")


def test_ask_path_gives_the_answer_and_checks_it_with_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[tuple[str, str, Callable[[str], bool | str], bool]] = []

    def path(
        question: str,
        default: str,
        validate: Callable[[str], bool | str],
        only_directories: bool,
    ) -> FakeQuestion:
        asked.append((question, default, validate, only_directories))
        return FakeQuestion("~/x")

    monkeypatch.setattr(questionary, "path", path)

    def check(text: str) -> str | None:
        return None if text.startswith("~") else "start with ~"

    ui = Ui.default()
    assert ui.ask_path("Where?", default="~/d", check=check, directories=True) == "~/x"
    ((question, default, validate, directories),) = asked
    assert (question, default, directories) == ("Where?", "~/d", True)
    assert validate("~/y") is True
    assert validate("y") == "start with ~"


@pytest.mark.parametrize("answer", [None, KeyboardInterrupt()])
def test_ask_path_gives_none_when_it_is_cancelled(
    monkeypatch: pytest.MonkeyPatch, answer: Answer
) -> None:
    monkeypatch.setattr(questionary, "path", answering(answer))

    assert Ui.default().ask_path("Where?") is None
