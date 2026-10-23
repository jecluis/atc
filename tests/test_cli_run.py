"""``atc run``, and the rule that makes it the default command."""

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NoReturn, Protocol

import pytest

from atc import launch
from atc.cli import main
from atc.exitcodes import CONFIG, OK, USAGE
from atc.jsonval import JsonObject, canonical
from atc.launch import LaunchPlan
from atc.paths import Dirs
from tests.conftest import ConfigWriter
from tests.helpers import StopError, git_repo, make_source, unit

FILES = {
    "conf/accounts/personal.json": unit({}),
    "conf/accounts/bravo.json": unit({}),
}
PROJECT = "conf/projects/bar.json"
REBASE = "Bash(git rebase:*)"
DASH = "\N{EN DASH}"
REGISTRY = """\
version: 1
default-account: personal
agents: {{claude: {{bin: {agent}}}}}
accounts:
  personal: {{agent: claude, dir: ~/.claude}}
  bravo: {{agent: claude, dir: ~/.config/bravo/claude}}
projects:
  bar: {{repo: {repo}, account: bravo}}
"""
WARNING_REGISTRY = """\
version: 1
default-account: personal
agents: {{claude: {{bin: {agent}}}}}
accounts:
  personal:
    agent: claude
    dir: ~/.claude
    use-default-agents-md: true
    agents-md-file: /x
"""


@pytest.fixture(autouse=True)
def never_start_a_process(monkeypatch: pytest.MonkeyPatch) -> None:
    def execvpe(file: str, args: list[str], _env: Mapping[str, str]) -> NoReturn:
        raise AssertionError(f"a test started {file} {args}")

    monkeypatch.setattr(os, "execvpe", execvpe)


@pytest.fixture(autouse=True)
def wide_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the output wide enough that rich does not wrap a path."""
    monkeypatch.setenv("COLUMNS", "1000")


@pytest.fixture
def executed(monkeypatch: pytest.MonkeyPatch) -> list[LaunchPlan]:
    """Stand in for ``execute``: record the plan and stop the command."""
    plans: list[LaunchPlan] = []

    def record(plan: LaunchPlan) -> NoReturn:
        plans.append(plan)
        raise StopError

    monkeypatch.setattr(launch, "execute", record)
    return plans


@pytest.fixture
def agent(tmp_path: Path) -> Path:
    """Make an executable that stands for the agent. No test runs it."""
    path = make_source(tmp_path / "bin", {"claude": "#!/bin/sh\n"}) / "claude"
    path.chmod(0o755)
    return path


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return git_repo(make_source(tmp_path / "bar", {"README": "x"}))


@pytest.fixture(autouse=True)
def setup(
    configure: ConfigWriter,
    agent: Path,
    repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Write a registry with two accounts and the project ``bar``; work outside it."""
    configure(FILES, REGISTRY.format(agent=agent, repo=repo))
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return work


def start(executed: Sequence[LaunchPlan], args: Sequence[str]) -> LaunchPlan:
    """Run atc to the point of starting the agent, and return what it would start."""
    with pytest.raises(StopError):
        _ = main(list(args))
    assert len(executed) == 1
    return executed[0]


def test_no_arguments_means_run(executed: list[LaunchPlan], agent: Path) -> None:
    plan = start(executed, [])

    assert plan.argv == [str(agent)]
    assert plan.account.name == "personal"


def test_a_leading_option_means_run(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["-a", "bravo", "--print"]) == OK

    out = capsys.readouterr().out
    assert "bravo" in out
    assert "argv" in out


def test_help_is_the_groups_help(
    executed: list[LaunchPlan],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--help"]) == OK

    out = capsys.readouterr().out
    assert "Commands" in out
    assert "run" in out
    assert "sync" in out
    assert executed == []


def test_run_has_its_own_help(
    executed: list[LaunchPlan], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["run", "--help"]) == OK

    out = capsys.readouterr().out
    for option in (
        "--account",
        "--settings",
        "--print",
        "--quiet",
        "--lax",
        "--conf-d",
    ):
        assert option in out
    assert executed == []


def test_a_typo_never_starts_a_session(
    executed: list[LaunchPlan], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["shwo"]) == USAGE

    assert "No such command" in capsys.readouterr().err
    assert executed == []


def test_a_leading_double_dash_means_run_with_agent_arguments(
    executed: list[LaunchPlan],
) -> None:
    plan = start(executed, ["--", "--resume", "x"])

    assert plan.argv[-2:] == ["--resume", "x"]
    assert plan.argv[0].endswith("claude")


def test_a_prompt_after_a_leading_double_dash_is_one_agent_argument(
    executed: list[LaunchPlan], agent: Path
) -> None:
    plan = start(executed, ["--", "a prompt"])

    assert plan.argv == [str(agent), "a prompt"]


def test_atcs_own_words_after_double_dash_are_the_agents(
    executed: list[LaunchPlan], agent: Path
) -> None:
    plan = start(executed, ["--", "run", "--print", "-a", "x", "--help", "--", "--"])

    assert plan.argv == [str(agent), "run", "--print", "-a", "x", "--help", "--", "--"]
    assert plan.account.name == "personal"


def test_an_unknown_option_before_double_dash_is_an_error(
    executed: list[LaunchPlan], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--resume"]) == USAGE
    assert main(["run", "-a", "personal", "--resume", "--", "x"]) == USAGE

    assert "No such option" in capsys.readouterr().err
    assert executed == []


def test_a_word_before_double_dash_is_an_error(
    executed: list[LaunchPlan], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["run", "shwo"]) == USAGE
    assert main(["-a", "personal", "a prompt"]) == USAGE

    err = capsys.readouterr().err
    assert "unexpected extra argument (a prompt)" in err
    assert "[-- AGENT-ARGS]..." in err
    assert executed == []


def test_arguments_after_double_dash_reach_the_agent(
    agent: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--print", "--", "--resume", "x"]) == OK

    argv = next(line for line in capsys.readouterr().out.splitlines() if "argv" in line)
    assert argv.endswith(f"{agent} --resume x")


def test_print_shows_the_plan_and_does_not_execute(
    repo: Path,
    agent: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    dirs: Dirs,
) -> None:
    def execute(plan: LaunchPlan) -> NoReturn:
        pytest.fail(f"--print executed {plan.argv}")

    monkeypatch.setattr(launch, "execute", execute)
    monkeypatch.setenv("ATC_FOO", "stale")
    monkeypatch.chdir(repo)

    assert main(["--print", "--settings", '{"model": "opus"}']) == OK

    captured = capsys.readouterr()
    assert captured.err == ""
    out = captured.out
    assert "bar" in out
    assert "bravo" in out
    assert str(repo / ".git") in out
    assert "ATC_ACCOUNT=bravo" in out
    assert "ATC_PROJECT=bar" in out
    assert f"ATC_STATE_DIR={dirs.state_dir}" in out
    assert "ATC_FOO" in out
    assert "CLAUDE_CONFIG_DIR=" in out
    assert f"{agent} --settings" in out
    assert out.endswith('{\n  "model": "opus"\n}\n')


def test_print_outside_a_project_says_so(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--print"]) == OK

    out = capsys.readouterr().out
    assert f"project: {DASH}" in out
    assert f"repository: {DASH}" in out
    assert out.endswith("{}\n")


def test_print_text_is_never_read_as_markup(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--print", "--", "[red]x[/red]"]) == OK

    assert "[red]x[/red]" in capsys.readouterr().out


def test_conf_d_requires_print(
    executed: list[LaunchPlan],
    dirs: Dirs,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--conf-d", str(dirs.conf_d)]) == USAGE

    assert "--print" in capsys.readouterr().err
    assert executed == []


def test_conf_d_reads_the_sources_from_the_directory_with_print(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    other = make_source(tmp_path / "other", {"conf/accounts/personal.json": unit({})})

    assert main(["--print", "--conf-d", str(other)]) == OK

    assert "personal" in capsys.readouterr().out


def test_the_announcement_names_the_project_and_account(
    repo: Path,
    executed: list[LaunchPlan],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(repo)

    _ = start(executed, [])

    assert capsys.readouterr().err == "atc: project bar · account bravo\n"


def test_the_announcement_outside_a_project(
    executed: list[LaunchPlan], capsys: pytest.CaptureFixture[str]
) -> None:
    _ = start(executed, ["-a", "personal"])

    assert capsys.readouterr().err == f"atc: project {DASH} · account personal\n"


def test_quiet_turns_the_announcement_off(
    executed: list[LaunchPlan], capsys: pytest.CaptureFixture[str]
) -> None:
    _ = start(executed, ["-q"])

    assert capsys.readouterr().err == ""


def test_the_settings_option_takes_a_file_or_json(
    executed: list[LaunchPlan], tmp_path: Path
) -> None:
    path = tmp_path / "s.json"
    _ = path.write_text('{"theme": "dark"}', encoding="utf-8")

    from_json = start(executed, ["--settings", '{"model": "opus"}'])
    executed.clear()
    from_file = start(executed, ["--settings", str(path)])

    assert from_json.fragment == {"model": "opus"}
    assert from_json.argv[1:3] == ["--settings", canonical({"model": "opus"})]
    assert from_file.fragment == {"theme": "dark"}


def test_settings_after_double_dash_are_refused(
    executed: list[LaunchPlan], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--settings", "{}", "--", "--settings", "{}"]) == USAGE
    assert main(["--", "--settings={}"]) == USAGE

    assert "after --" in capsys.readouterr().err
    assert executed == []


def test_a_settings_value_that_is_not_json_is_a_config_error(
    executed: list[LaunchPlan], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--settings", "{nope"]) == CONFIG

    assert capsys.readouterr().err.startswith("error: --settings: invalid JSON")
    assert executed == []


def test_a_policy_refusal_is_a_config_error(
    executed: list[LaunchPlan], capsys: pytest.CaptureFixture[str]
) -> None:
    settings = '{"sandbox": {"allowUnsandboxedCommands": false}}'

    assert main(["--settings", settings]) == CONFIG

    assert "session layer" in capsys.readouterr().err
    assert executed == []


def test_an_unknown_account_is_a_config_error(
    executed: list[LaunchPlan], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["-a", "nope"]) == CONFIG

    assert "no account named 'nope'" in capsys.readouterr().err
    assert executed == []


def test_warnings_from_the_registry_are_printed(
    configure: ConfigWriter,
    agent: Path,
    executed: list[LaunchPlan],
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure(FILES, WARNING_REGISTRY.format(agent=agent))

    _ = start(executed, [])

    err = capsys.readouterr().err
    assert err.startswith("warning: ")
    assert err.endswith(f"atc: project {DASH} · account personal\n")


def test_dash_h_is_help_for_the_group_and_for_run(
    executed: list[LaunchPlan], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["-h"]) == OK
    assert "Commands" in capsys.readouterr().out

    assert main(["run", "-h"]) == OK
    assert "--account" in capsys.readouterr().out
    assert executed == []


def test_dash_h_after_double_dash_is_the_agents(
    executed: list[LaunchPlan], agent: Path
) -> None:
    plan = start(executed, ["--", "-h"])

    assert plan.argv == [str(agent), "-h"]


def test_a_directory_git_refuses_is_a_config_error(
    executed: list[LaunchPlan],
    repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(repo)
    broken = tmp_path / "gitconfig"
    _ = broken.write_text("[core\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(broken))

    assert main(["--print"]) == CONFIG

    err = capsys.readouterr().err
    assert err.startswith("error: ")
    assert "bad config" in err
    assert executed == []


def test_a_registered_repository_git_cannot_read_is_a_warning(
    configure: ConfigWriter,
    agent: Path,
    repo: Path,
    tmp_path: Path,
    executed: list[LaunchPlan],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure(FILES, REGISTRY.format(agent=agent, repo=tmp_path / "absent"))
    monkeypatch.chdir(repo)

    plan = start(executed, [])

    err = capsys.readouterr().err
    assert plan.project is None
    assert err.startswith("warning: ")
    assert "project 'bar'" in err
    assert err.endswith(f"atc: project {DASH} · account personal\n")


class ProjectConfigurer(Protocol):
    def __call__(
        self, project: JsonObject, *, bravo: JsonObject | None = None
    ) -> None: ...


@pytest.fixture
def configure_project(
    configure: ConfigWriter, agent: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> ProjectConfigurer:
    """Give ``bar`` the unit ``project``, and ``bravo`` ``bravo``; work in ``bar``."""

    def write(project: JsonObject, *, bravo: JsonObject | None = None) -> None:
        files = {**FILES, PROJECT: project}
        if bravo is not None:
            files["conf/accounts/bravo.json"] = bravo
        configure(files, REGISTRY.format(agent=agent, repo=repo))
        monkeypatch.chdir(repo)

    return write


def test_the_projects_settings_start_the_session(
    executed: list[LaunchPlan], configure_project: ProjectConfigurer
) -> None:
    configure_project(unit({"model": "sonnet", "theme": "dark"}))

    plan = start(executed, ["--settings", '{"model": "opus"}'])

    assert plan.fragment == {"model": "opus", "theme": "dark"}
    assert plan.argv[1:3] == ["--settings", canonical(plan.fragment)]


def test_print_shows_the_projects_settings_in_the_fragment(
    configure_project: ProjectConfigurer, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_project(unit({"env": {"WHO": "{{account.name}}"}}))

    assert main(["--print"]) == OK

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.endswith('{\n  "env": {\n    "WHO": "bravo"\n  }\n}\n')


def test_a_project_layer_that_cannot_be_built_is_a_config_error(
    executed: list[LaunchPlan],
    configure_project: ProjectConfigurer,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_project(unit({"sandbox": {"allowUnsandboxedCommands": True}}))

    assert main([]) == CONFIG

    err = capsys.readouterr().err
    assert err.startswith("error: projects/bar.json: /sandbox/allowUnsandboxedCommands")
    assert executed == []


def test_lax_turns_unknown_project_keys_into_warnings(
    executed: list[LaunchPlan],
    configure_project: ProjectConfigurer,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_project(unit({"modle": "opus"}))

    assert main([]) == CONFIG
    assert "unknown settings key(s): modle" in capsys.readouterr().err
    assert executed == []

    plan = start(executed, ["--lax"])

    assert plan.fragment == {"modle": "opus"}
    assert capsys.readouterr().err == (
        "warning: projects/bar.json: unknown settings key(s): modle\n"
        "atc: project bar · account bravo\n"
    )


def test_print_warns_about_a_rule_that_never_applies(
    configure_project: ProjectConfigurer, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_project(
        unit({"permissions": {"allow": [REBASE]}}),
        bravo=unit({"permissions": {"deny": [REBASE]}}),
    )

    assert main(["--print"]) == OK

    captured = capsys.readouterr()
    assert captured.err == (
        f"warning: allow {REBASE} (projects/bar.json) never applies: "
        f"deny {REBASE} (accounts/bravo.json) takes precedence\n"
    )
    assert f'"allow": [\n      "{REBASE}"' in captured.out


def test_print_checks_the_rules_of_your_settings_too(
    configure_project: ProjectConfigurer, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_project(unit({"permissions": {"allow": [REBASE]}}))
    settings = json.dumps({"permissions": {"deny": [REBASE]}})

    assert main(["--print", "--settings", settings]) == OK

    assert capsys.readouterr().err == (
        f"warning: allow {REBASE} (projects/bar.json) never applies: "
        f"deny {REBASE} (--settings) takes precedence\n"
    )


def test_a_launch_says_nothing_about_rules(
    executed: list[LaunchPlan],
    configure_project: ProjectConfigurer,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_project(
        unit({"permissions": {"allow": [REBASE]}}),
        bravo=unit({"permissions": {"deny": [REBASE]}}),
    )

    plan = start(executed, [])

    assert plan.rule_warnings == ()
    assert capsys.readouterr().err == "atc: project bar · account bravo\n"


def test_a_broken_account_unit_stops_neither_print_nor_a_launch(
    executed: list[LaunchPlan],
    configure_project: ProjectConfigurer,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_project(
        unit({"model": "opus"}), bravo=unit({"autoMemoryDirectory": "/m"})
    )

    plan = start(executed, [])

    assert plan.fragment == {"model": "opus"}
    assert capsys.readouterr().err == "atc: project bar · account bravo\n"
    assert main(["--print"]) == OK
    captured = capsys.readouterr()
    skipped = "warning: rule check skipped for the account layer: "
    assert captured.err.startswith(
        f"{skipped}accounts/bravo.json: /autoMemoryDirectory"
    )
    assert captured.err.count("warning:") == 1
    assert "argv: " in captured.out
    assert captured.out.endswith('{\n  "model": "opus"\n}\n')
