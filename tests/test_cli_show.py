"""``atc show account``, and ``atc src check`` building each account's layer."""

import json
from pathlib import Path

import pytest

from atc.cli import main
from atc.exitcodes import CONFIG, OK, USAGE
from atc.jsonval import JsonObject, dumps
from tests.conftest import ConfigWriter, RegistryWriter
from tests.helpers import MINIMAL_REGISTRY, make_source, unit

PERSONAL = "conf/accounts/personal.json"


def test_show_account_prints_canonical_json(
    configure: ConfigWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    expected: JsonObject = {
        "model": "opus",
        "permissions": {"allow": ["Bash(cargo test *)"]},
        "theme": "dark",
    }
    configure(
        {
            PERSONAL: unit(
                {"model": "opus", "permissions": expected["permissions"]},
                imports=["common/base.json"],
            ),
            "conf/common/base.json": unit({"model": "sonnet", "theme": "dark"}),
        }
    )

    status = main(["show", "account", "personal"])

    captured = capsys.readouterr()
    assert status == OK
    assert captured.out == dumps(expected)
    assert captured.err == ""


def test_output_has_sorted_keys_and_a_trailing_newline(
    configure: ConfigWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    configure({PERSONAL: unit({"theme": "dark", "model": "opus"})})

    _ = main(["show", "account", "personal"])

    out = capsys.readouterr().out
    assert out == '{\n  "model": "opus",\n  "theme": "dark"\n}\n'


def test_identical_input_produces_identical_output(
    configure: ConfigWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    configure({PERSONAL: unit({"permissions": {"allow": ["b", "a"]}})})

    _ = main(["show", "account", "personal"])
    first = capsys.readouterr().out
    _ = main(["show", "account", "personal"])
    second = capsys.readouterr().out

    assert first == second


def test_a_config_error_exits_1_with_an_error_line(
    configure: ConfigWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    configure({PERSONAL: unit({"autoMemoryDirectory": "/m"})})

    assert main(["show", "account", "personal"]) == CONFIG

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error:")
    assert "accounts/personal.json: /autoMemoryDirectory" in captured.err


def test_show_unknown_account_is_a_config_error(
    configure: ConfigWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(
        {PERSONAL: unit()},
        MINIMAL_REGISTRY + "      work: {agent: claude, dir: ~/.config/work}\n",
    )

    assert main(["show", "account", "nope"]) == CONFIG

    err = capsys.readouterr().err
    assert "'nope'" in err
    assert "personal, work" in err


def test_show_lax_warns_about_unknown_keys(
    configure: ConfigWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    configure({PERSONAL: unit({"permisions": {}})})

    status = main(["show", "account", "personal", "--lax"])

    captured = capsys.readouterr()
    assert status == OK
    assert json.loads(captured.out) == {"permisions": {}}
    assert "warning:" in captured.err
    assert "permisions" in captured.err


def test_show_warns_about_directories_that_are_not_sources(
    configure: ConfigWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(
        {
            "personal/conf/accounts/personal.json": unit({"model": "x"}),
            "stray/notes.md": "x",
        }
    )

    status = main(["show", "account", "personal"])

    captured = capsys.readouterr()
    assert status == OK
    assert json.loads(captured.out) == {"model": "x"}
    assert "warning:" in captured.err
    assert "stray" in captured.err


def test_conf_d_option_reads_units_from_another_directory(
    tmp_path: Path,
    write_registry: RegistryWriter,
    capsys: pytest.CaptureFixture[str],
) -> None:
    elsewhere = make_source(tmp_path / "elsewhere", {PERSONAL: unit({"model": "opus"})})
    _ = write_registry(MINIMAL_REGISTRY)

    status = main(["show", "account", "personal", "--conf-d", str(elsewhere)])

    assert status == OK
    assert json.loads(capsys.readouterr().out) == {"model": "opus"}


def test_an_empty_conf_d_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["show", "account", "personal", "--conf-d", ""]) == USAGE
    assert "--conf-d requires a directory" in capsys.readouterr().err


def test_src_check_builds_every_account_layer(
    configure: ConfigWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    configure({PERSONAL: unit({"autoMemoryDirectory": "/m"})})

    status = main(["src", "check"])

    captured = capsys.readouterr()
    assert status == CONFIG
    assert captured.out == ""
    assert "accounts/personal.json: /autoMemoryDirectory" in captured.err
    assert "ok:" not in captured.err


def test_show_account_expands_templates(
    configure: ConfigWriter, capsys: pytest.CaptureFixture[str], home: Path
) -> None:
    configure({PERSONAL: unit({"env": {"ACCOUNT_DIR": "{{account.dir}}"}})})

    status = main(["show", "account", "personal"])

    assert status == OK
    assert json.loads(capsys.readouterr().out) == {
        "env": {"ACCOUNT_DIR": str(home / ".claude")}
    }


def test_show_account_reports_an_unknown_reference(
    configure: ConfigWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    configure({PERSONAL: unit({"model": "{{nope}}"})})

    assert main(["show", "account", "personal"]) == CONFIG

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "/model: unknown reference {{nope}}" in captured.err
