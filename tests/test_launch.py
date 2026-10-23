"""Planning a launch: which project and account, the environment, the command."""

import os
import shutil
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import NoReturn

import click
import pytest

from atc import git
from atc.context import BuildContext
from atc.errors import ConfigError, GitError, PolicyError, RegistryError, UnitError
from atc.git import run_git
from atc.jsonval import JsonObject, canonical
from atc.launch import (
    LaunchPlan,
    execute,
    find_project,
    plan_launch,
    read_settings_arg,
)
from tests.conftest import ContextBuilder
from tests.helpers import StopError, git_repo, make_source, unit

CLAUDE = "/opt/agents/claude"
ATC = Path("/opt/bin/atc")
FILES = {"conf/accounts/personal.json": unit({})}


def registry_text(repo: Path | None = None, *, top: str = "") -> str:
    projects = (
        f"projects:\n  bar:\n    repo: {repo}\n    account: bravo\n" if repo else ""
    )
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
    )


def ignore(_message: str) -> None:
    """Take a warning that the test does not look at."""


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
    build_context: ContextBuilder, plain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = build_context(FILES, registry_text())
    result = plan(ctx, plain, env={"A": "1"}, agent_args=["--resume"])
    calls: list[tuple[str, list[str], Mapping[str, str]]] = []

    def execvpe(file: str, args: list[str], env: Mapping[str, str]) -> NoReturn:
        calls.append((file, args, env))
        raise StopError

    monkeypatch.setattr(os, "execvpe", execvpe)

    with pytest.raises(StopError):
        execute(result)

    assert calls == [(CLAUDE, [CLAUDE, "--resume"], result.env)]


def test_execute_reports_a_binary_that_cannot_be_started(
    build_context: ContextBuilder, plain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = build_context(FILES, registry_text())
    result = plan(ctx, plain)

    def execvpe(_file: str, _args: list[str], _env: Mapping[str, str]) -> NoReturn:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(os, "execvpe", execvpe)

    with pytest.raises(ConfigError, match=rf"cannot start {CLAUDE}: .*denied"):
        execute(result)
