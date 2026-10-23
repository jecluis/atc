"""Terminal output: markup safety, colour policy, and the JSON contract."""

import json
import re
import sys
from typing import final

import click
import pytest
import questionary
from rich.console import Console

from atc.jsonval import JsonObject, dumps
from atc.ui import PickChoice, Ui

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


type Answer = list[str] | BaseException | None
"""What a picker gives: the values ticked, an interrupt, or no answer."""

type Asked = list[tuple[str, list[tuple[str, str, bool]]]]
"""What a picker was asked: its title, then each line's label, value and tick."""


@final
class FakeQuestion:
    """What `questionary.checkbox` builds, answering without a terminal."""

    def __init__(self, answer: Answer) -> None:
        self.answer = answer

    def unsafe_ask(self) -> list[str] | None:
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


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
