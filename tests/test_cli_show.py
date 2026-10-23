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


PROJECTS = (
    MINIMAL_REGISTRY
    + "      work: {agent: claude, dir: ~/.config/work}\n"
    + "    projects:\n"
    + "      bar: {repo: /r, account: work, accounts: [work, personal]}\n"
    + "      strict: {repo: /s, account: work, accounts: [work]}\n"
    + "      bare: {repo: /b}\n"
)
PROJECT_UNIT = unit({"env": {"WHO": "{{account.name}}", "WHAT": "{{project.name}}"}})


def test_show_project_uses_the_projects_default_account(
    configure: ConfigWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    configure({"conf/projects/bar.json": PROJECT_UNIT}, PROJECTS)

    status = main(["show", "project", "bar"])

    captured = capsys.readouterr()
    assert status == OK
    assert json.loads(captured.out) == {"env": {"WHO": "work", "WHAT": "bar"}}
    assert captured.err == ""


def test_show_project_option_a_picks_another_allowed_account(
    configure: ConfigWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    configure({"conf/projects/bar.json": PROJECT_UNIT}, PROJECTS)

    status = main(["show", "project", "bar", "-a", "personal"])

    assert status == OK
    assert json.loads(capsys.readouterr().out)["env"]["WHO"] == "personal"


def test_show_project_refuses_an_account_the_project_does_not_allow(
    configure: ConfigWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    configure({"conf/projects/strict.json": PROJECT_UNIT}, PROJECTS)

    assert main(["show", "project", "strict", "-a", "personal"]) == CONFIG

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "project 'strict' does not allow account 'personal'" in captured.err


def test_show_unknown_project_lists_the_projects(
    configure: ConfigWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    configure({PERSONAL: unit()}, PROJECTS)

    assert main(["show", "project", "nope"]) == CONFIG

    assert "bar, strict, bare" in capsys.readouterr().err


def test_show_project_without_a_unit_prints_no_settings_and_says_so(
    configure: ConfigWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    configure({"conf/projects/bar.json": PROJECT_UNIT}, PROJECTS)

    status = main(["show", "project", "bare"])

    captured = capsys.readouterr()
    assert status == OK
    assert json.loads(captured.out) == {}
    assert "warning: project 'bare' has no project layer" in captured.err
    assert "projects/bare.json" in captured.err


def test_show_project_reports_a_policy_error_naming_the_unit(
    configure: ConfigWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    settings: JsonObject = {"sandbox": {"allowUnsandboxedCommands": True}}
    configure({"conf/projects/bar.json": unit(settings)}, PROJECTS)

    assert main(["show", "project", "bar"]) == CONFIG

    err = capsys.readouterr().err
    assert "projects/bar.json: /sandbox/allowUnsandboxedCommands" in err


def test_src_check_builds_every_project_with_each_allowed_account(
    configure: ConfigWriter, capsys: pytest.CaptureFixture[str]
) -> None:
    settings: JsonObject = {"sandbox": {"allowUnsandboxedCommands": True}}
    configure(
        {
            PERSONAL: unit(),
            "conf/accounts/work.json": unit(),
            "conf/projects/bar.json": unit(settings),
        },
        PROJECTS,
    )

    status = main(["src", "check"])

    captured = capsys.readouterr()
    assert status == CONFIG
    assert captured.out == ""
    assert "projects/bar.json: /sandbox/allowUnsandboxedCommands" in captured.err
    assert "ok:" not in captured.err
