"""The command line as a whole: usage errors and the version."""

from importlib import import_module
from pathlib import Path
from typing import NoReturn

import click
import pytest

from atc.cli import main
from atc.exitcodes import INTERRUPTED, USAGE


def test_no_command_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as exit_info:
        _ = main([])

    assert exit_info.value.code == USAGE


def test_unknown_command_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["shwo"]) == USAGE
    assert "No such command" in capsys.readouterr().err


def test_show_without_a_subcommand_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as exit_info:
        _ = main(["show"])

    assert exit_info.value.code == USAGE


def test_version_names_the_program(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version"]) == 0
    assert "atc" in capsys.readouterr().out


@pytest.mark.parametrize("interrupt", [click.Abort(), KeyboardInterrupt()])
def test_an_interrupt_is_a_short_message_and_status_130(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    interrupt: BaseException,
) -> None:
    def interrupted(_conf_d: Path | None) -> NoReturn:
        raise interrupt

    # `atc.cli.skills` names the command group in the package, not the module.
    monkeypatch.setattr(
        import_module("atc.cli.skills"), "load_build_context", interrupted
    )

    assert main(["skills", "ls"]) == INTERRUPTED

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == "aborted"
