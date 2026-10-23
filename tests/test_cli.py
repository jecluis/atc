"""The command line: output shape, streams, and exit statuses."""

import json
from pathlib import Path

import pytest

from atc.cli import main
from atc.exitcodes import CONFIG, OK, USAGE
from tests.conftest import UnitWriter


def test_build_prints_the_merged_settings(
    root: Path, write_unit: UnitWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = write_unit(
        "rust.json",
        {"version": 1, "settings": {"permissions": {"allow": ["Bash(cargo test *)"]}}},
    )

    status = main(["build", "--root", str(root), "rust.json"])

    captured = capsys.readouterr()
    assert status == OK
    assert json.loads(captured.out) == {
        "permissions": {"allow": ["Bash(cargo test *)"]}
    }
    assert captured.err == ""


def test_output_has_sorted_keys_and_a_trailing_newline(
    root: Path, write_unit: UnitWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = write_unit(
        "a.json", {"version": 1, "settings": {"theme": "dark", "model": "opus"}}
    )

    _ = main(["build", "--root", str(root), "a.json"])

    out = capsys.readouterr().out
    assert out == '{\n  "model": "opus",\n  "theme": "dark"\n}\n'


def test_output_does_not_depend_on_import_order(
    root: Path, write_unit: UnitWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = write_unit("a.json", {"version": 1, "settings": {"theme": "dark"}})
    _ = write_unit("b.json", {"version": 1, "settings": {"model": "opus"}})
    _ = write_unit("one.json", {"version": 1, "imports": ["a.json", "b.json"]})
    _ = write_unit("two.json", {"version": 1, "imports": ["b.json", "a.json"]})

    _ = main(["build", "--root", str(root), "one.json"])
    first = capsys.readouterr().out
    _ = main(["build", "--root", str(root), "two.json"])
    second = capsys.readouterr().out

    assert first == second


def test_identical_input_produces_identical_output(
    root: Path, write_unit: UnitWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = write_unit(
        "a.json", {"version": 1, "settings": {"permissions": {"allow": ["b", "a"]}}}
    )

    _ = main(["build", "--root", str(root), "a.json"])
    first = capsys.readouterr().out
    _ = main(["build", "--root", str(root), "a.json"])
    second = capsys.readouterr().out

    assert first == second


def test_a_config_error_keeps_standard_output_clean(
    root: Path, write_unit: UnitWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = write_unit("a.json", {"version": 1, "imports": ["nowhere.json"]})

    status = main(["build", "--root", str(root), "a.json"])

    captured = capsys.readouterr()
    assert status == CONFIG
    assert captured.out == ""
    assert "nowhere.json" in captured.err


def test_a_missing_root_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = main(["build", "--root", str(tmp_path / "absent"), "a.json"])

    assert status == USAGE
    assert "no configuration root" in capsys.readouterr().err


def test_lax_warns_on_standard_error_and_still_builds(
    root: Path, write_unit: UnitWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = write_unit("a.json", {"version": 1, "settings": {"permisions": {}}})

    status = main(["build", "--root", str(root), "--lax", "a.json"])

    captured = capsys.readouterr()
    assert status == OK
    assert json.loads(captured.out) == {"permisions": {}}
    assert "permisions" in captured.err


def test_several_units_merge_left_to_right(
    root: Path, write_unit: UnitWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = write_unit("a.json", {"version": 1, "settings": {"model": "sonnet"}})
    _ = write_unit("b.json", {"version": 1, "settings": {"model": "opus"}})

    _ = main(["build", "--root", str(root), "a.json", "b.json"])

    assert json.loads(capsys.readouterr().out) == {"model": "opus"}


def test_an_empty_root_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    status = main(["build", "--root", "", "a.json"])

    assert status == USAGE
    assert "--root requires a directory" in capsys.readouterr().err


def test_no_command_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as exit_info:
        _ = main([])

    assert exit_info.value.code == USAGE


def test_unknown_command_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["bulid"]) == 2
    assert "No such command" in capsys.readouterr().err


def test_version_names_the_program(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version"]) == 0
    assert "atc" in capsys.readouterr().out


def test_a_config_error_exits_1_with_an_error_line(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["build", "missing.json", "--root", str(root)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error:")
