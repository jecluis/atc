"""Planning a launch: which project and account, the environment, the command."""

import os
import shutil
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import NoReturn

import click
import pytest

from atc import git, guard
from atc.context import BuildContext
from atc.errors import (
    ConfigError,
    GitError,
    MergeError,
    PolicyError,
    RegistryError,
    UnitError,
)
from atc.git import run_git
from atc.jsonval import JsonObject, canonical
from atc.launch import (
    LaunchPlan,
    execute,
    find_project,
    plan_launch,
    read_settings_arg,
)
from atc.paths import Dirs
from tests.conftest import ContextBuilder
from tests.helpers import (
    StopError,
    git_repo,
    guarded,
    is_locked,
    make_source,
    packed_history,
    unit,
)

CLAUDE = "/opt/agents/claude"
ATC = Path("/opt/bin/atc")
FILES = {"conf/accounts/personal.json": unit({})}
BRAVO = "conf/accounts/bravo.json"
PROJECT = "conf/projects/bar.json"
REBASE = "Bash(git rebase:*)"
PUSH = "Bash(git push:*)"


def registry_text(
    repo: Path | None = None, *, top: str = "", snapshot: str | None = None
) -> str:
    projects = (
        f"projects:\n  bar:\n    repo: {repo}\n    account: bravo\n" if repo else ""
    )
    if repo and snapshot:
        projects += f"    snapshot: {snapshot}\n"
    return (
        f"{top}version: 1\n"
        "default-account: personal\n"
        "accounts:\n"
        "  personal: {agent: claude, dir: ~/.claude}\n"
        "  bravo: {agent: claude, dir: ~/.config/bravo/claude}\n"
    ) + projects


def found(name: str) -> str | None:
    return f"/opt/agents/{name}"


def plan(
    ctx: BuildContext,
    cwd: Path,
    *,
    env: Mapping[str, str] | None = None,
    account: str | None = None,
    settings: JsonObject | None = None,
    agent_args: Sequence[str] = (),
    which: Callable[[str], str | None] = found,
    self_path: Path = ATC,
    git_common_dir: Callable[[Path], Path | None] = git.common_dir,
    lax: bool = False,
    explain: bool = False,
    no_guard: bool = False,
) -> LaunchPlan:
    return plan_launch(
        ctx,
        cwd=cwd,
        env={} if env is None else env,
        account=account,
        settings=settings,
        agent_args=agent_args,
        self_path=self_path,
        which=which,
        git_common_dir=git_common_dir,
        lax=lax,
        explain=explain,
        no_guard=no_guard,
    )


def ignore(_message: str) -> None:
    """Take a warning that the test does not look at."""


def refuse_to_run(command: Sequence[str]) -> int:
    raise AssertionError(f"a test ran {command}")


@pytest.fixture
def plain(tmp_path: Path) -> Path:
    path = tmp_path / "plain"
    path.mkdir()
    return path


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return git_repo(make_source(tmp_path / "bar", {"README": "x"}))


def test_outside_git_there_is_no_project(
    build_context: ContextBuilder, plain: Path
) -> None:
    ctx = build_context(FILES, registry_text())

    result = plan(ctx, plain)

    assert result.common_dir is None
    assert result.project is None
    assert result.cwd == plain
    assert result.account.name == "personal"


def test_an_unregistered_repository_uses_the_default_account(
    build_context: ContextBuilder, tmp_path: Path, repo: Path
) -> None:
    other = git_repo(make_source(tmp_path / "other", {"README": "y"}))
    ctx = build_context(FILES, registry_text(repo))

    result = plan(ctx, other)

    assert result.common_dir == other / ".git"
    assert result.project is None
    assert result.account.name == "personal"


def test_a_registered_repository_uses_its_projects_account(
    build_context: ContextBuilder, repo: Path
) -> None:
    ctx = build_context(FILES, registry_text(repo))

    result = plan(ctx, repo)

    assert result.project is not None
    assert result.project.name == "bar"
    assert result.account.name == "bravo"
    assert plan(ctx, repo, account="personal").account.name == "personal"


def test_a_linked_worktree_finds_its_project(
    build_context: ContextBuilder, tmp_path: Path, repo: Path
) -> None:
    worktree = tmp_path / "wt"
    _ = run_git(["worktree", "add", "-q", "-b", "feature", str(worktree)], cwd=repo)
    ctx = build_context(FILES, registry_text(repo))

    result = plan(ctx, worktree)

    assert result.project is not None
    assert result.project.name == "bar"
    assert result.common_dir == repo / ".git"
    assert result.fragment == packed_history(repo)


def test_a_project_registered_by_its_worktree_matches_the_main_checkout(
    build_context: ContextBuilder, tmp_path: Path, repo: Path
) -> None:
    worktree = tmp_path / "wt"
    _ = run_git(["worktree", "add", "-q", "-b", "feature", str(worktree)], cwd=repo)
    ctx = build_context(FILES, registry_text(worktree))

    result = plan(ctx, repo)

    assert result.project is not None
    assert result.project.name == "bar"


def test_find_project_asks_nothing_outside_a_repository(
    build_context: ContextBuilder, repo: Path
) -> None:
    ctx = build_context(FILES, registry_text(repo))

    def refuse(_path: Path) -> NoReturn:
        raise AssertionError("git must not be asked")

    assert find_project(ctx.registry, None, git_common_dir=refuse, warn=ignore) is None


def test_find_project_skips_a_repo_that_is_not_a_repository(
    build_context: ContextBuilder, tmp_path: Path, repo: Path
) -> None:
    ctx = build_context(FILES, registry_text(tmp_path / "absent"))

    found_project = find_project(
        ctx.registry, repo / ".git", git_common_dir=lambda _: None, warn=ignore
    )

    assert found_project is None


def test_find_project_skips_a_repo_git_cannot_read_and_says_so(
    build_context: ContextBuilder, tmp_path: Path, repo: Path
) -> None:
    other = git_repo(make_source(tmp_path / "other", {"README": "y"}))
    text = registry_text(repo) + f"  broken:\n    repo: {other}\n"
    ctx = build_context(FILES, text)
    warnings: list[str] = []

    def common_dir(path: Path) -> Path | None:
        if path == other:
            raise GitError("fatal: detected dubious ownership")
        return git.common_dir(path)

    result = find_project(
        ctx.registry, repo / ".git", git_common_dir=common_dir, warn=warnings.append
    )

    assert result is not None
    assert result.name == "bar"
    assert len(warnings) == 1
    assert "project 'broken'" in warnings[0]
    assert str(other) in warnings[0]
    assert "dubious ownership" in warnings[0]


def test_a_registered_repository_git_cannot_read_is_a_warning_in_the_plan(
    build_context: ContextBuilder, tmp_path: Path
) -> None:
    other = git_repo(make_source(tmp_path / "other", {"README": "y"}))
    absent = tmp_path / "absent"
    ctx = build_context(FILES, registry_text(absent))

    result = plan(ctx, other)

    assert result.project is None
    assert result.account.name == "personal"
    assert len(result.warnings) == 1
    assert "project 'bar'" in result.warnings[0]
    assert str(absent) in result.warnings[0]


def test_a_directory_git_refuses_stops_the_plan(
    build_context: ContextBuilder, plain: Path
) -> None:
    ctx = build_context(FILES, registry_text())

    def refuse(_path: Path) -> NoReturn:
        raise GitError("fatal: detected dubious ownership")

    with pytest.raises(GitError, match="dubious ownership"):
        _ = plan(ctx, plain, git_common_dir=refuse)


def test_two_projects_for_one_repository_are_an_error(
    build_context: ContextBuilder, repo: Path
) -> None:
    text = registry_text(repo) + f"  again:\n    repo: {repo}\n"
    ctx = build_context(FILES, text)

    with pytest.raises(RegistryError, match=r"'bar' and 'again'.*same repository"):
        _ = plan(ctx, repo)


def test_atc_variables_are_replaced_or_removed(
    build_context: ContextBuilder, plain: Path, repo: Path
) -> None:
    ctx = build_context(FILES, registry_text(repo))
    state = str(ctx.dirs.state_dir)
    env = {
        "PATH": "/bin",
        "ATC_PROJECT": "stale",
        "ATC_FOO": "x",
        "ATC_ACCOUNT": "stale",
    }

    outside = plan(ctx, plain, env=env)

    assert outside.env == {
        "PATH": "/bin",
        "ATC_ACCOUNT": "personal",
        "ATC_STATE_DIR": state,
    }
    assert outside.env_changes == {
        "ATC_PROJECT": None,
        "ATC_FOO": None,
        "ATC_ACCOUNT": "personal",
        "ATC_STATE_DIR": state,
    }
    inside = plan(ctx, repo, env=env)
    assert inside.env["ATC_PROJECT"] == "bar"
    assert inside.env["ATC_ACCOUNT"] == "bravo"
    assert "ATC_FOO" not in inside.env


def test_the_environment_given_is_not_modified(
    build_context: ContextBuilder, plain: Path
) -> None:
    ctx = build_context(FILES, registry_text())
    env = {"ATC_FOO": "x"}

    _ = plan(ctx, plain, env=env)

    assert env == {"ATC_FOO": "x"}


def test_a_value_that_does_not_change_is_not_a_change(
    build_context: ContextBuilder, plain: Path
) -> None:
    ctx = build_context(FILES, registry_text())
    env = {"ATC_ACCOUNT": "personal", "ATC_STATE_DIR": str(ctx.dirs.state_dir)}

    assert plan(ctx, plain, env=env).env_changes == {}


def test_claude_config_dir_is_removed_for_the_default_dir_and_set_otherwise(
    build_context: ContextBuilder, plain: Path, home: Path
) -> None:
    ctx = build_context(FILES, registry_text())
    env = {"CLAUDE_CONFIG_DIR": "/stale"}

    default = plan(ctx, plain, env=env)
    other = plan(ctx, plain, env=env, account="bravo")

    assert "CLAUDE_CONFIG_DIR" not in default.env
    assert default.env_changes["CLAUDE_CONFIG_DIR"] is None
    selected = str(home / ".config" / "bravo" / "claude")
    assert other.env["CLAUDE_CONFIG_DIR"] == selected
    assert other.env_changes["CLAUDE_CONFIG_DIR"] == selected


def test_your_settings_become_the_fragment(
    build_context: ContextBuilder, plain: Path
) -> None:
    ctx = build_context(FILES, registry_text())
    settings: JsonObject = {"model": "opus", "env": {"A": "1"}}

    result = plan(ctx, plain, settings=settings, agent_args=["--resume", "x"])

    assert result.fragment == settings
    assert result.argv == [CLAUDE, "--settings", canonical(settings), "--resume", "x"]


def test_an_empty_fragment_adds_no_settings_flag(
    build_context: ContextBuilder, plain: Path
) -> None:
    ctx = build_context(FILES, registry_text())

    result = plan(ctx, plain, agent_args=["a prompt"])

    assert result.fragment == {}
    assert result.argv == [CLAUDE, "a prompt"]


@pytest.mark.parametrize("arg", ["--settings", "--settings=/x.json"])
def test_settings_after_double_dash_are_refused(
    build_context: ContextBuilder, plain: Path, arg: str
) -> None:
    ctx = build_context(FILES, registry_text())

    with pytest.raises(click.UsageError, match=r"only one --settings takes effect"):
        _ = plan(ctx, plain, agent_args=["--resume", arg, "x"])


def test_a_prompt_that_only_mentions_settings_is_not_refused(
    build_context: ContextBuilder, plain: Path
) -> None:
    ctx = build_context(FILES, registry_text())

    result = plan(ctx, plain, agent_args=["explain --settings", "--settingsfile"])

    assert result.argv[-2:] == ["explain --settings", "--settingsfile"]


def test_an_agent_binary_that_resolves_to_atc_is_refused(
    build_context: ContextBuilder, plain: Path, tmp_path: Path
) -> None:
    atc = make_source(tmp_path / "bin", {"atc": "#!/bin/sh\n"}) / "atc"
    (tmp_path / "bin" / "claude").symlink_to(atc)
    ctx = build_context(FILES, registry_text())

    with pytest.raises(ConfigError, match=r"claude.*resolves to atc"):
        _ = plan(
            ctx,
            plain,
            which=lambda name: str(tmp_path / "bin" / name),
            self_path=atc,
        )


def test_a_configured_binary_that_resolves_to_atc_is_refused_by_path(
    build_context: ContextBuilder, plain: Path, tmp_path: Path
) -> None:
    atc = make_source(tmp_path / "bin", {"atc": "#!/bin/sh\n"}) / "atc"
    atc.chmod(0o755)
    link = tmp_path / "bin" / "mine"
    link.symlink_to(atc)
    ctx = build_context(
        FILES, registry_text(top=f"agents: {{claude: {{bin: {link}}}}}\n")
    )

    with pytest.raises(ConfigError, match=rf"{link}.*resolves to atc"):
        _ = plan_launch(
            ctx,
            cwd=plain,
            env={},
            account=None,
            settings=None,
            agent_args=[],
            self_path=atc,
            lax=False,
            explain=False,
            no_guard=False,
        )


def test_a_configured_binary_found_on_path_that_is_atc_is_refused(
    build_context: ContextBuilder,
    plain: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    atc = make_source(tmp_path / "bin", {"atc": "#!/bin/sh\n"}) / "atc"
    atc.chmod(0o755)
    (tmp_path / "bin" / "mine").symlink_to(atc)
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    ctx = build_context(FILES, registry_text(top="agents: {claude: {bin: mine}}\n"))
    assert shutil.which("mine") == str(tmp_path / "bin" / "mine")

    with pytest.raises(ConfigError, match=r"mine.*resolves to atc"):
        _ = plan_launch(
            ctx,
            cwd=plain,
            env={},
            account=None,
            settings=None,
            agent_args=[],
            self_path=atc,
            lax=False,
            explain=False,
            no_guard=False,
        )


def test_a_configured_binary_can_start_with_a_tilde(
    build_context: ContextBuilder, plain: Path, home: Path
) -> None:
    ctx = build_context(
        FILES, registry_text(top="agents: {claude: {bin: ~/bin/claude}}\n")
    )
    asked: list[str] = []

    def which(name: str) -> str | None:
        asked.append(name)
        return name

    result = plan(ctx, plain, which=which)

    assert asked == [str(home / "bin" / "claude")]
    assert result.argv == [str(home / "bin" / "claude")]


def test_the_agent_binary_is_looked_up_by_the_agents_name(
    build_context: ContextBuilder, plain: Path
) -> None:
    ctx = build_context(FILES, registry_text())
    asked: list[str] = []

    def which(name: str) -> str | None:
        asked.append(name)
        return f"/usr/bin/{name}"

    result = plan(ctx, plain, which=which)

    assert asked == ["claude"]
    assert result.argv == ["/usr/bin/claude"]


def test_the_registry_can_name_the_agent_binary(
    build_context: ContextBuilder, plain: Path
) -> None:
    ctx = build_context(FILES, registry_text(top="agents: {claude: {bin: mine}}\n"))
    asked: list[str] = []

    def which(name: str) -> str | None:
        asked.append(name)
        return f"/opt/mine/{name}"

    result = plan(ctx, plain, which=which)

    assert asked == ["mine"]
    assert result.argv == ["/opt/mine/mine"]


def test_an_agent_binary_that_is_not_found_is_a_config_error(
    build_context: ContextBuilder, plain: Path
) -> None:
    ctx = build_context(FILES, registry_text())

    with pytest.raises(ConfigError, match=r"'claude' not found"):
        _ = plan(ctx, plain, which=lambda _: None)


def test_your_settings_cannot_set_allow_unsandboxed_commands(
    build_context: ContextBuilder, plain: Path
) -> None:
    ctx = build_context(FILES, registry_text())
    settings: JsonObject = {"sandbox": {"allowUnsandboxedCommands": False}}

    with pytest.raises(PolicyError, match=r"^--settings: .*session layer"):
        _ = plan(ctx, plain, settings=settings)


def test_fragment_order_is_project_then_your_settings(
    build_context: ContextBuilder, repo: Path
) -> None:
    project: JsonObject = {
        "model": "sonnet",
        "theme": "dark",
        "permissions": {"allow": ["Read"]},
    }
    ctx = build_context({**FILES, PROJECT: unit(project)}, registry_text(repo))
    settings: JsonObject = {"model": "opus", "permissions": {"allow": ["Edit"]}}

    result = plan(ctx, repo, settings=settings, no_guard=True)

    assert result.fragment == {
        "model": "opus",
        "theme": "dark",
        "permissions": {"allow": ["Read", "Edit"]},
    }
    assert result.argv == [CLAUDE, "--settings", canonical(result.fragment)]


def test_session_entries_sit_between_the_project_layer_and_your_settings(
    build_context: ContextBuilder, repo: Path
) -> None:
    project: JsonObject = {"permissions": {"deny": ["Bash(rm:*)"]}}
    ctx = build_context({**FILES, PROJECT: unit(project)}, registry_text(repo))
    settings: JsonObject = {"model": "opus", "permissions": {"deny": ["Bash(sudo:*)"]}}
    pack = repo / ".git" / "objects" / "pack"

    result = plan(ctx, repo, settings=settings)

    assert result.fragment == {
        "model": "opus",
        "sandbox": {"filesystem": {"denyWrite": [str(pack)]}},
        "permissions": {
            "deny": ["Bash(rm:*)", "Edit(/" + str(pack) + "/**)", "Bash(sudo:*)"]
        },
    }
    assert result.argv == [CLAUDE, "--settings", canonical(result.fragment)]


def test_your_settings_cannot_remove_the_session_entries(
    build_context: ContextBuilder, repo: Path
) -> None:
    ctx = build_context(FILES, registry_text(repo))
    settings: JsonObject = {
        "permissions": {"deny": []},
        "sandbox": {"filesystem": {"denyWrite": []}},
    }

    result = plan(ctx, repo, settings=settings)

    assert result.fragment == packed_history(repo)


def test_the_project_layer_is_built_for_the_account_that_is_chosen(
    build_context: ContextBuilder, repo: Path
) -> None:
    unit_with_account = unit({"env": {"WHO": "{{account.name}}"}})
    ctx = build_context({PROJECT: unit_with_account}, registry_text(repo))

    assert plan(ctx, repo).fragment["env"] == {"WHO": "bravo"}
    assert plan(ctx, repo, account="personal").fragment["env"] == {"WHO": "personal"}


def test_a_project_without_a_unit_contributes_nothing(
    build_context: ContextBuilder, repo: Path
) -> None:
    ctx = build_context(FILES, registry_text(repo))

    inside = plan(ctx, repo)

    assert inside.project is not None
    assert inside.fragment == packed_history(repo)
    assert inside.warnings == ()
    assert plan(ctx, repo, settings={"model": "opus"}).fragment == guarded(
        repo, {"model": "opus"}
    )
    unprotected = plan(ctx, repo, no_guard=True)
    assert unprotected.fragment == {}
    assert unprotected.argv == [CLAUDE]


def test_outside_a_project_there_is_no_project_layer(
    build_context: ContextBuilder, plain: Path, repo: Path
) -> None:
    ctx = build_context({PROJECT: unit({"model": "sonnet"})}, registry_text(repo))

    assert plan(ctx, plain).fragment == {}


def test_a_project_layer_that_cannot_be_built_stops_the_plan(
    build_context: ContextBuilder, repo: Path
) -> None:
    settings: JsonObject = {"sandbox": {"allowUnsandboxedCommands": True}}
    ctx = build_context({PROJECT: unit(settings)}, registry_text(repo))

    with pytest.raises(PolicyError, match=r"^projects/bar.json: .*project layer"):
        _ = plan(ctx, repo)


def test_a_setting_that_is_another_type_in_your_settings_is_an_error(
    build_context: ContextBuilder, repo: Path
) -> None:
    ctx = build_context({PROJECT: unit({"model": "sonnet"})}, registry_text(repo))

    with pytest.raises(MergeError, match=r"^session layer: .*at /model"):
        _ = plan(ctx, repo, settings={"model": 1})


def test_lax_turns_unknown_project_keys_into_warnings(
    build_context: ContextBuilder, repo: Path
) -> None:
    ctx = build_context({PROJECT: unit({"modle": "opus"})}, registry_text(repo))

    with pytest.raises(UnitError, match=r"projects/bar.json: unknown settings key"):
        _ = plan(ctx, repo)
    result = plan(ctx, repo, lax=True)

    assert result.fragment == guarded(repo, {"modle": "opus"})
    assert result.warnings == ("projects/bar.json: unknown settings key(s): modle",)


def test_rule_warnings_are_computed_only_when_explaining(
    build_context: ContextBuilder, repo: Path
) -> None:
    files = {
        BRAVO: unit({"permissions": {"deny": [REBASE]}}),
        PROJECT: unit({"permissions": {"allow": [REBASE]}}),
    }
    ctx = build_context(files, registry_text(repo))

    quiet = plan(ctx, repo)
    explained = plan(ctx, repo, explain=True)

    assert quiet.rule_warnings == ()
    assert [warning.message() for warning in explained.rule_warnings] == [
        (
            f"allow {REBASE} (projects/bar.json) never applies: "
            f"deny {REBASE} (accounts/bravo.json) takes precedence"
        )
    ]
    assert explained.fragment == quiet.fragment
    assert quiet.fragment == guarded(repo, {"permissions": {"allow": [REBASE]}})
    assert explained.warnings == quiet.warnings == ()


def test_rule_warnings_look_at_the_account_the_project_and_your_settings_together(
    build_context: ContextBuilder, repo: Path
) -> None:
    files = {
        BRAVO: unit({"permissions": {"allow": [PUSH]}}),
        PROJECT: unit({"permissions": {"allow": [REBASE]}}),
    }
    ctx = build_context(files, registry_text(repo))
    settings: JsonObject = {"permissions": {"deny": [PUSH, REBASE]}}

    result = plan(ctx, repo, settings=settings, explain=True)

    assert [warning.message() for warning in result.rule_warnings] == [
        (
            f"allow {PUSH} (accounts/bravo.json) never applies: "
            f"deny {PUSH} (--settings) takes precedence"
        ),
        (
            f"allow {REBASE} (projects/bar.json) never applies: "
            f"deny {REBASE} (--settings) takes precedence"
        ),
    ]


def test_rule_warnings_outside_a_project_check_the_account_alone(
    build_context: ContextBuilder, plain: Path
) -> None:
    permissions: JsonObject = {"allow": ["Read(.env)"], "deny": ["Read"]}
    ctx = build_context(
        {"conf/accounts/personal.json": unit({"permissions": permissions})},
        registry_text(),
    )

    result = plan(ctx, plain, explain=True)

    assert [warning.message() for warning in result.rule_warnings] == [
        (
            "allow Read(.env) (accounts/personal.json) never applies: "
            "deny Read (accounts/personal.json) takes precedence"
        )
    ]


def test_a_broken_account_unit_does_not_block_a_plain_launch(
    build_context: ContextBuilder, repo: Path
) -> None:
    files = {
        BRAVO: unit({"autoMemoryDirectory": "/m"}),
        PROJECT: unit({"model": "opus"}),
    }
    ctx = build_context(files, registry_text(repo))

    result = plan(ctx, repo)
    explained = plan(ctx, repo, explain=True)

    assert result.fragment == explained.fragment == guarded(repo, {"model": "opus"})
    assert result.warnings == ()
    assert result.rule_warnings == ()
    assert len(explained.warnings) == 1
    skipped = "rule check skipped for the account layer: "
    assert explained.warnings[0].startswith(
        f"{skipped}accounts/bravo.json: /autoMemoryDirectory"
    )


def test_an_account_without_a_unit_does_not_block_a_plain_launch(
    build_context: ContextBuilder, plain: Path
) -> None:
    ctx = build_context({"conf/accounts/other.json": unit({})}, registry_text())

    result = plan(ctx, plain)
    explained = plan(ctx, plain, explain=True)

    assert result.warnings == ()
    assert result.rule_warnings == explained.rule_warnings == ()
    assert len(explained.warnings) == 1
    assert explained.warnings[0].startswith("rule check skipped for the account layer")
    assert "account 'personal'" in explained.warnings[0]
    assert "no such unit" in explained.warnings[0]


def test_an_unknown_key_in_the_account_unit_skips_the_check_unless_lax(
    build_context: ContextBuilder, plain: Path
) -> None:
    files = {"conf/accounts/personal.json": unit({"modle": "opus"})}
    ctx = build_context(files, registry_text())

    strict = plan(ctx, plain, explain=True)
    lax = plan(ctx, plain, explain=True, lax=True)

    assert len(strict.warnings) == 1
    assert strict.warnings[0].startswith("rule check skipped for the account layer: ")
    assert "unknown settings key(s): modle" in strict.warnings[0]
    assert all("skipped" not in warning for warning in lax.warnings)


def test_the_rules_of_the_project_and_your_settings_are_checked_without_the_account(
    build_context: ContextBuilder, repo: Path
) -> None:
    files = {
        BRAVO: unit({"autoMemoryDirectory": "/m"}),
        PROJECT: unit({"permissions": {"allow": [REBASE]}}),
    }
    ctx = build_context(files, registry_text(repo))
    settings: JsonObject = {"permissions": {"deny": [REBASE]}}

    result = plan(ctx, repo, settings=settings, explain=True)

    assert [warning.message() for warning in result.rule_warnings] == [
        (
            f"allow {REBASE} (projects/bar.json) never applies: "
            f"deny {REBASE} (--settings) takes precedence"
        )
    ]
    assert len(result.warnings) == 1
    assert "rule check skipped for the account layer" in result.warnings[0]


def test_a_project_without_a_unit_is_checked_with_the_account_and_your_settings(
    build_context: ContextBuilder, repo: Path
) -> None:
    files = {BRAVO: unit({"permissions": {"allow": [PUSH]}})}
    ctx = build_context(files, registry_text(repo))
    settings: JsonObject = {"permissions": {"deny": [PUSH]}}

    result = plan(ctx, repo, settings=settings, explain=True)

    assert result.project is not None
    assert result.warnings == ()
    assert [warning.message() for warning in result.rule_warnings] == [
        (
            f"allow {PUSH} (accounts/bravo.json) never applies: "
            f"deny {PUSH} (--settings) takes precedence"
        )
    ]


def test_an_unknown_account_is_a_config_error(
    build_context: ContextBuilder, plain: Path
) -> None:
    ctx = build_context(FILES, registry_text())

    with pytest.raises(RegistryError, match="no account named 'nope'"):
        _ = plan(ctx, plain, account="nope")


def test_the_warnings_of_loading_are_in_the_plan(
    build_context: ContextBuilder, plain: Path
) -> None:
    text = registry_text().replace(
        "dir: ~/.claude}",
        "dir: ~/.claude, use-default-agents-md: true, agents-md-file: /x}",
    )
    ctx = build_context(FILES, text)

    assert plan(ctx, plain).warnings == ctx.registry.warnings != ()


def test_read_settings_arg_takes_json_text() -> None:
    assert read_settings_arg('{"a": {"b": [1]}}') == {"a": {"b": [1]}}
    assert read_settings_arg("{}") == {}


def test_read_settings_arg_takes_a_file(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    _ = path.write_text('{"model": "opus"}', encoding="utf-8")

    assert read_settings_arg(str(path)) == {"model": "opus"}


def test_read_settings_arg_is_strict_and_does_not_expand_templates() -> None:
    assert read_settings_arg('{"a": "{{home}}"}') == {"a": "{{home}}"}
    for text in ('{"a": 1, "a": 2}', '{"a": NaN}', "{nope"):
        with pytest.raises(UnitError, match=r"^--settings: invalid JSON"):
            _ = read_settings_arg(text)


def test_read_settings_arg_names_a_file_that_cannot_be_read(tmp_path: Path) -> None:
    with pytest.raises(UnitError, match=r"absent.json: cannot be read"):
        _ = read_settings_arg(str(tmp_path / "absent.json"))


def test_read_settings_arg_wants_an_object(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    _ = path.write_text("[1]", encoding="utf-8")

    with pytest.raises(UnitError, match=r"s.json: expected a JSON object, found array"):
        _ = read_settings_arg(str(path))


def test_execute_replaces_the_process_with_the_planned_command(
    build_context: ContextBuilder,
    plain: Path,
    dirs: Dirs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = build_context(FILES, registry_text())
    result = plan(ctx, plain, env={"A": "1"}, agent_args=["--resume"])
    calls: list[tuple[str, list[str], Mapping[str, str]]] = []

    def execvpe(file: str, args: list[str], env: Mapping[str, str]) -> NoReturn:
        calls.append((file, args, env))
        raise StopError

    monkeypatch.setattr(os, "execvpe", execvpe)

    with pytest.raises(StopError):
        execute(result, dirs)

    assert calls == [(CLAUDE, [CLAUDE, "--resume"], result.env)]


def test_execute_reports_a_binary_that_cannot_be_started(
    build_context: ContextBuilder,
    plain: Path,
    dirs: Dirs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = build_context(FILES, registry_text())
    result = plan(ctx, plain)

    def execvpe(_file: str, _args: list[str], _env: Mapping[str, str]) -> NoReturn:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(os, "execvpe", execvpe)

    with pytest.raises(ConfigError, match=rf"cannot start {CLAUDE}: .*denied"):
        execute(result, dirs)


def test_a_guarded_plan_turns_gc_off_after_the_environments_own_settings(
    build_context: ContextBuilder, repo: Path
) -> None:
    ctx = build_context(FILES, registry_text(repo))
    env = {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "user.name",
        "GIT_CONFIG_VALUE_0": "x",
    }

    result = plan(ctx, repo, env=env)

    assert result.guarded
    assert result.env_changes["GIT_CONFIG_COUNT"] == "3"
    assert result.env_changes["GIT_CONFIG_KEY_1"] == "gc.auto"
    assert result.env_changes["GIT_CONFIG_KEY_2"] == "maintenance.auto"
    assert "GIT_CONFIG_KEY_0" not in result.env_changes


def test_a_count_git_would_refuse_stops_a_guarded_plan_only(
    build_context: ContextBuilder, repo: Path
) -> None:
    ctx = build_context(FILES, registry_text(repo))
    env = {"GIT_CONFIG_COUNT": "many"}

    with pytest.raises(ConfigError, match="GIT_CONFIG_COUNT"):
        _ = plan(ctx, repo, env=env)
    assert plan(ctx, repo, env=env, no_guard=True).env["GIT_CONFIG_COUNT"] == "many"


def test_no_guard_leaves_out_entries_gc_and_lock_and_warns(
    build_context: ContextBuilder,
    repo: Path,
    dirs: Dirs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = build_context(FILES, registry_text(repo, snapshot="bravo"))

    result = plan(ctx, repo, no_guard=True)

    assert not result.guarded
    assert result.fragment == {}
    assert result.argv == [CLAUDE]
    assert not any(name.startswith("GIT_CONFIG") for name in result.env)
    assert len(result.warnings) == 1
    assert result.warnings[0].startswith("--no-guard: ")
    assert "packed history" in result.warnings[0]

    def execvpe(_file: str, _args: list[str], _env: Mapping[str, str]) -> NoReturn:
        raise StopError

    monkeypatch.setattr(os, "execvpe", execvpe)
    with pytest.raises(StopError):
        execute(result, dirs, run_snapshot=lambda _command: 0)
    assert not dirs.state_dir.exists()
    assert not dirs.runtime_dir.exists()


def test_no_guard_still_takes_the_projects_snapshot(
    build_context: ContextBuilder,
    repo: Path,
    dirs: Dirs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = build_context(FILES, registry_text(repo, snapshot="bravo"))
    result = plan(ctx, repo, no_guard=True)
    commands: list[list[str]] = []

    def snapshot(command: Sequence[str]) -> int:
        commands.append(list(command))
        return 0

    def execvpe(_file: str, _args: list[str], _env: Mapping[str, str]) -> NoReturn:
        raise StopError

    monkeypatch.setattr(os, "execvpe", execvpe)

    with pytest.raises(StopError):
        execute(result, dirs, run_snapshot=snapshot)

    assert result.snapshot == "bravo"
    assert [(command[2], command[-1]) for command in commands] == [
        ("bravo", f"atc: {repo}")
    ]


def test_outside_a_repository_is_unguarded_without_a_warning(
    build_context: ContextBuilder,
    plain: Path,
    repo: Path,
    dirs: Dirs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = build_context(FILES, registry_text(repo, snapshot="bravo"))

    def execvpe(_file: str, _args: list[str], _env: Mapping[str, str]) -> NoReturn:
        raise StopError

    monkeypatch.setattr(os, "execvpe", execvpe)
    for no_guard in (False, True):
        result = plan(ctx, plain, no_guard=no_guard)

        assert not result.guarded
        assert result.snapshot is None
        assert result.fragment == {}
        assert result.warnings == ()
        assert not any(name.startswith("GIT_CONFIG") for name in result.env)
        with pytest.raises(StopError):
            execute(result, dirs, run_snapshot=refuse_to_run)
    assert not dirs.state_dir.exists()
    assert not dirs.runtime_dir.exists()


def test_the_projects_snapshot_goes_in_the_plan(
    build_context: ContextBuilder, tmp_path: Path, repo: Path
) -> None:
    other = git_repo(make_source(tmp_path / "other", {"README": "y"}))
    ctx = build_context(FILES, registry_text(repo, snapshot="bravo"))

    assert plan(ctx, repo).snapshot == "bravo"
    assert plan(ctx, repo, no_guard=True).snapshot == "bravo"
    assert plan(ctx, repo, account="personal").snapshot == "bravo"
    assert plan(ctx, other).snapshot is None


def test_the_session_entries_are_checked_with_the_rules_that_can_not_apply(
    build_context: ContextBuilder, repo: Path
) -> None:
    ctx = build_context(FILES, registry_text(repo))
    rule = "Edit(/" + str(repo / ".git" / "objects" / "pack") + "/**)"
    settings: JsonObject = {"permissions": {"allow": [rule]}}

    result = plan(ctx, repo, settings=settings, explain=True)

    dead = f"allow {rule} (--settings) never applies: "
    assert [warning.message() for warning in result.rule_warnings] == [
        dead + f"deny {rule} (session entries) takes precedence"
    ]


@pytest.fixture
def lock_fds(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[int]]:
    """Record the session locks that ``execute`` opens, and close them afterwards."""
    opened: list[int] = []
    real = guard.open_session_lock

    def record(
        dirs: Dirs, common: Path, *, timeout: float = 60.0
    ) -> tuple[int | None, str | None]:
        descriptor, warning = real(dirs, common, timeout=timeout)
        if descriptor is not None:
            opened.append(descriptor)
        return descriptor, warning

    monkeypatch.setattr(guard, "open_session_lock", record)
    yield opened
    for descriptor in opened:
        os.close(descriptor)


def test_a_guarded_session_registers_locks_and_snapshots_before_it_starts(
    build_context: ContextBuilder,
    repo: Path,
    dirs: Dirs,
    lock_fds: list[int],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ctx = build_context(FILES, registry_text(repo, snapshot="bravo"))
    result = plan(ctx, repo)
    common = repo / ".git"
    lock = dirs.runtime_dir / f"{guard.repo_key(common)}.lock"
    events: list[str] = []

    def snapshot(command: Sequence[str]) -> int:
        events.append("snapshot")
        assert (command[2], command[-1]) == ("bravo", f"atc: {repo}")
        assert (dirs.state_dir / "repos").read_text(encoding="utf-8") == f"{common}\n"
        assert is_locked(lock)
        return 0

    def execvpe(file: str, args: list[str], env: Mapping[str, str]) -> NoReturn:
        events.append("exec")
        assert (file, args, env) == (CLAUDE, result.argv, result.env)
        assert is_locked(lock)
        assert os.get_inheritable(lock_fds[0])
        raise StopError

    monkeypatch.setattr(os, "execvpe", execvpe)

    with pytest.raises(StopError):
        execute(result, dirs, run_snapshot=snapshot)

    assert events == ["snapshot", "exec"]
    assert len(lock_fds) == 1
    assert capsys.readouterr().err == ""


def test_the_agent_starts_after_a_failed_snapshot(
    build_context: ContextBuilder,
    repo: Path,
    dirs: Dirs,
    lock_fds: list[int],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ctx = build_context(FILES, registry_text(repo, snapshot="bravo"))
    result = plan(ctx, repo)
    started: list[str] = []

    def execvpe(file: str, _args: list[str], _env: Mapping[str, str]) -> NoReturn:
        started.append(file)
        raise StopError

    monkeypatch.setattr(os, "execvpe", execvpe)

    with pytest.raises(StopError):
        execute(result, dirs, run_snapshot=lambda _command: 1)

    assert started == [CLAUDE]
    assert len(lock_fds) == 1
    err = capsys.readouterr().err
    assert err.startswith("warning: snapper snapshot failed")
    assert err.count("warning:") == 1


def test_a_lock_that_cannot_be_taken_warns_and_continues(
    build_context: ContextBuilder,
    repo: Path,
    dirs: Dirs,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ctx = build_context(FILES, registry_text(repo))
    result = plan(ctx, repo)
    started: list[str] = []

    def no_lock(
        _dirs: Dirs, _common: Path, *, timeout: float = 60.0
    ) -> tuple[int | None, str | None]:
        return None, f"the lock is not held for {timeout:g} seconds"

    def execvpe(file: str, _args: list[str], _env: Mapping[str, str]) -> NoReturn:
        started.append(file)
        raise StopError

    monkeypatch.setattr(guard, "open_session_lock", no_lock)
    monkeypatch.setattr(os, "execvpe", execvpe)

    with pytest.raises(StopError):
        execute(result, dirs, run_snapshot=refuse_to_run)

    assert started == [CLAUDE]
    assert capsys.readouterr().err == "warning: the lock is not held for 60 seconds\n"


def test_packed_history_that_appears_while_waiting_for_the_lock_is_a_warning(
    build_context: ContextBuilder,
    repo: Path,
    dirs: Dirs,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ctx = build_context(FILES, registry_text(repo))
    result = plan(ctx, repo)
    refs = repo / ".git" / "packed-refs"
    started: list[str] = []

    def maintenance_ends(
        _dirs: Dirs, _common: Path, *, _timeout: float = 60.0
    ) -> tuple[int | None, str | None]:
        _ = refs.write_text("", encoding="utf-8")
        return None, None

    def execvpe(file: str, _args: list[str], _env: Mapping[str, str]) -> NoReturn:
        started.append(file)
        raise StopError

    monkeypatch.setattr(guard, "open_session_lock", maintenance_ends)
    monkeypatch.setattr(os, "execvpe", execvpe)

    with pytest.raises(StopError):
        execute(result, dirs, run_snapshot=refuse_to_run)

    assert started == [CLAUDE]
    err = capsys.readouterr().err
    assert err.startswith("warning: ")
    assert str(refs) in err
    assert "not protected" in err
    assert "restart" in err
    assert err.count("warning:") == 1


def test_a_repository_list_that_cannot_be_written_is_a_config_error(
    build_context: ContextBuilder,
    repo: Path,
    dirs: Dirs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = build_context(FILES, registry_text(repo))
    result = plan(ctx, repo)
    dirs.state_dir.parent.mkdir(parents=True, exist_ok=True)
    _ = dirs.state_dir.write_text("not a directory", encoding="utf-8")

    def execvpe(_file: str, _args: list[str], _env: Mapping[str, str]) -> NoReturn:
        raise AssertionError("the agent started without the repository registered")

    monkeypatch.setattr(os, "execvpe", execvpe)

    with pytest.raises(ConfigError, match=rf"cannot register.*{dirs.state_dir}"):
        execute(result, dirs, run_snapshot=refuse_to_run)
