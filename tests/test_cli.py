"""The command line as a whole: usage errors and the version."""

import pytest

from atc.cli import main
from atc.exitcodes import USAGE


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
