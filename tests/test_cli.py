"""The command line: output shape, streams, and exit statuses."""

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from atc.cli import main
from atc.exitcodes import CONFIG, OK, USAGE
from atc.jsonval import JsonObject
from atc.paths import Dirs
from tests.helpers import make_source, unit


def configure(dirs: Dirs, units: Mapping[str, JsonObject]) -> None:
    """Make the single-layout source atc reads by default, holding ``units``."""
    _ = make_source(dirs.conf_d, {f"conf/{name}": doc for name, doc in units.items()})


def test_build_prints_the_merged_settings(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(
        dirs, {"rust.json": unit({"permissions": {"allow": ["Bash(cargo test *)"]}})}
    )

    status = main(["build", "rust.json"])

    captured = capsys.readouterr()
    assert status == OK
    assert json.loads(captured.out) == {
        "permissions": {"allow": ["Bash(cargo test *)"]}
    }
    assert captured.err == ""


def test_output_has_sorted_keys_and_a_trailing_newline(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(dirs, {"a.json": unit({"theme": "dark", "model": "opus"})})

    _ = main(["build", "a.json"])

    out = capsys.readouterr().out
    assert out == '{\n  "model": "opus",\n  "theme": "dark"\n}\n'


def test_output_does_not_depend_on_import_order(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(
        dirs,
        {
            "a.json": unit({"theme": "dark"}),
            "b.json": unit({"model": "opus"}),
            "one.json": unit(imports=["a.json", "b.json"]),
            "two.json": unit(imports=["b.json", "a.json"]),
        },
    )

    _ = main(["build", "one.json"])
    first = capsys.readouterr().out
    _ = main(["build", "two.json"])
    second = capsys.readouterr().out

    assert first == second


def test_identical_input_produces_identical_output(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(dirs, {"a.json": unit({"permissions": {"allow": ["b", "a"]}})})

    _ = main(["build", "a.json"])
    first = capsys.readouterr().out
    _ = main(["build", "a.json"])
    second = capsys.readouterr().out

    assert first == second


def test_a_config_error_keeps_standard_output_clean(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(dirs, {"a.json": unit(imports=["nowhere.json"])})

    status = main(["build", "a.json"])

    captured = capsys.readouterr()
    assert status == CONFIG
    assert captured.out == ""
    assert "nowhere.json" in captured.err


def test_a_missing_conf_d_is_a_config_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = main(["build", "--conf-d", str(tmp_path / "absent"), "a.json"])

    assert status == CONFIG
    assert "git clone" in capsys.readouterr().err


def test_conf_d_option_reads_units_from_another_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    elsewhere = make_source(
        tmp_path / "elsewhere", {"conf/a.json": unit({"model": "opus"})}
    )

    status = main(["build", "--conf-d", str(elsewhere), "a.json"])

    assert status == OK
    assert json.loads(capsys.readouterr().out) == {"model": "opus"}


def test_lax_warns_on_standard_error_and_still_builds(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(dirs, {"a.json": unit({"permisions": {}})})

    status = main(["build", "--lax", "a.json"])

    captured = capsys.readouterr()
    assert status == OK
    assert json.loads(captured.out) == {"permisions": {}}
    assert "permisions" in captured.err


def test_several_units_merge_left_to_right(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(
        dirs,
        {"a.json": unit({"model": "sonnet"}), "b.json": unit({"model": "opus"})},
    )

    _ = main(["build", "a.json", "b.json"])

    assert json.loads(capsys.readouterr().out) == {"model": "opus"}


def test_build_resolves_units_across_sources(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = make_source(
        dirs.conf_d / "personal",
        {"conf/common/base.json": unit({"model": "sonnet", "theme": "dark"})},
    )
    _ = make_source(
        dirs.conf_d / "bravo",
        {
            "conf/projects/x.json": unit(
                {"model": "opus"}, imports=["@personal/common/base.json"]
            )
        },
    )

    status = main(["build", "projects/x.json"])

    assert status == OK
    assert json.loads(capsys.readouterr().out) == {"model": "opus", "theme": "dark"}


def test_build_takes_a_unit_qualified_with_its_source(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("personal", "bravo"):
        _ = make_source(
            dirs.conf_d / name, {"conf/common/x.json": unit({"model": name})}
        )

    assert main(["build", "common/x.json"]) == CONFIG
    assert "@name/" in capsys.readouterr().err

    assert main(["build", "@bravo/common/x.json"]) == OK
    assert json.loads(capsys.readouterr().out) == {"model": "bravo"}


def test_build_reports_a_unit_no_source_has(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(dirs, {"a.json": unit()})

    status = main(["build", "missing.json"])

    captured = capsys.readouterr()
    assert status == CONFIG
    assert captured.out == ""
    assert captured.err.startswith("error:")
    assert "'missing.json': no such unit" in captured.err


def test_build_warns_about_directories_that_are_not_sources(
    dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = make_source(dirs.conf_d / "personal", {"conf/a.json": unit({"model": "opus"})})
    _ = make_source(dirs.conf_d / "stray", {"notes.md": "x"})

    status = main(["build", "a.json"])

    captured = capsys.readouterr()
    assert status == OK
    assert json.loads(captured.out) == {"model": "opus"}
    assert "warning:" in captured.err
    assert "stray" in captured.err


def test_an_empty_conf_d_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    status = main(["build", "--conf-d", "", "a.json"])

    assert status == USAGE
    assert "--conf-d requires a directory" in capsys.readouterr().err


def test_the_root_option_is_gone(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["build", "--root", "x", "a.json"]) == USAGE
    assert "--root" in capsys.readouterr().err


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
