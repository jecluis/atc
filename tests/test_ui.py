"""Terminal output: markup safety, colour policy, and the JSON contract."""

import json
import re

import pytest
from rich.console import Console

from atc.jsonval import JsonObject, dumps
from atc.ui import Ui

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
