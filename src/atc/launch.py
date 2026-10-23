"""Planning how to start an agent in the right account, and starting it."""

import os
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

import click

from atc import git
from atc.backends import backend_for
from atc.context import BuildContext
from atc.errors import ConfigError, GitError, RegistryError, UnitError
from atc.jsonval import JsonObject, json_kind, load_json, parse_json
from atc.layers import session_layer
from atc.registry import Account, Project, Registry, choose_account

SETTINGS_OPTION = "--settings"
ENV_PREFIX = "ATC_"
"""Every variable that starts with this is atc's, and is replaced or removed."""


@dataclass(frozen=True, slots=True)
class LaunchPlan:
    """
    How one session starts.

    ``common_dir`` is the git directory that the repository of ``cwd`` shares
    with its worktrees, ``None`` outside a repository, and ``project`` is the
    registry's project for it, if there is one. ``fragment`` is the settings for
    this session alone, and ``argv`` the command that starts the agent with
    them. ``env`` is the environment the agent gets, and ``env_changes`` is how
    it differs from the one atc was given: a variable's new value, or ``None``
    for one that is removed. ``warnings`` are problems that did not stop atc.
    """

    cwd: Path
    common_dir: Path | None
    project: Project | None
    account: Account
    fragment: JsonObject
    env: dict[str, str]
    env_changes: dict[str, str | None]
    argv: list[str]
    warnings: tuple[str, ...]


def find_project(
    registry: Registry,
    common: Path | None,
    *,
    git_common_dir: Callable[[Path], Path | None],
    warn: Callable[[str], None],
) -> Project | None:
    """
    Find the project whose repository shares the git directory ``common``.

    ``git_common_dir`` gives that directory for each project's ``repo``, so a
    project matches from any of its worktrees. A project whose repository git
    cannot read is left out, and ``warn`` is given a message that says so.
    ``None`` means ``common`` is not in a repository, or no project has it.

    Raises:
        RegistryError: several projects are the same repository.

    """
    if common is None:
        return None
    matches: list[Project] = []
    for project in registry.projects.values():
        try:
            shared = git_common_dir(project.repo)
        except GitError as exc:
            message = (
                f"project '{project.name}' is left out: "
                f"git cannot read its repository {project.repo}: {exc}"
            )
            warn(message)
            continue
        if shared == common:
            matches.append(project)
    if len(matches) > 1:
        names = " and ".join(f"'{project.name}'" for project in matches)
        message = f"{registry.path}: projects {names} name the same repository"
        raise RegistryError(message)
    return matches[0] if matches else None


def read_settings_arg(value: str) -> JsonObject:
    """
    Read the settings given to ``--settings``: JSON text or the path of a file.

    A value that starts with ``{`` is the JSON itself. The JSON is strict, and
    its strings are not templates.

    Raises:
        UnitError: the file cannot be read, or what is read is not a JSON object.

    """
    if value.startswith("{"):
        origin, document = SETTINGS_OPTION, parse_json(value, SETTINGS_OPTION)
    else:
        origin, document = value, load_json(Path(value))
    if not isinstance(document, dict):
        kind = json_kind(document)
        raise UnitError(f"{origin}: expected a JSON object, found {kind}")
    return document


def plan_launch(
    ctx: BuildContext,
    *,
    cwd: Path,
    env: Mapping[str, str],
    account: str | None,
    settings: JsonObject | None,
    agent_args: Sequence[str],
    self_path: Path,
    which: Callable[[str], str | None] = shutil.which,
    git_common_dir: Callable[[Path], Path | None] = git.common_dir,
) -> LaunchPlan:
    """
    Plan how to start the agent in ``cwd``, with the environment ``env``.

    The account is ``account`` if given, else the project's, else the default.
    ``settings`` are the settings for this session, and ``agent_args`` go to
    the agent. ``self_path`` is atc's own executable, which the agent's binary
    must not be. ``which`` finds a binary on ``PATH``.

    Raises:
        click.UsageError: ``agent_args`` has a ``--settings``.
        GitError: git cannot tell whether ``cwd`` is in a repository.
        RegistryError: there is no such account, the project does not allow it,
            or several projects are the same repository.
        PolicyError: ``settings`` set a key the session layer must not.
        ConfigError: the agent's binary is not found, or is atc.

    """
    _refuse_settings(agent_args)
    layer = None if settings is None else session_layer(settings, SETTINGS_OPTION)
    fragment: JsonObject = {} if layer is None else layer.settings

    common = git_common_dir(cwd)
    warnings: list[str] = [*ctx.sources.warnings, *ctx.registry.warnings]
    project = find_project(
        ctx.registry, common, git_common_dir=git_common_dir, warn=warnings.append
    )
    chosen = choose_account(ctx.registry, project, account)
    backend = backend_for(chosen.agent, ctx.home)
    selected = backend.environment(chosen, _atc_environment(env, ctx, chosen, project))
    binary = _agent_binary(ctx.registry, chosen.agent, self_path, which)
    return LaunchPlan(
        cwd=cwd,
        common_dir=common,
        project=project,
        account=chosen,
        fragment=fragment,
        env=selected,
        env_changes=_changes(env, selected),
        argv=backend.argv(binary, fragment, agent_args),
        warnings=(*warnings, *(() if layer is None else layer.warnings)),
    )


def execute(plan: LaunchPlan) -> NoReturn:
    """
    Replace atc with the agent, started as ``plan`` says.

    Raises:
        ConfigError: the agent cannot be started.

    """
    try:
        os.execvpe(plan.argv[0], plan.argv, plan.env)  # noqa: S606 - the planned agent
    except OSError as exc:
        raise ConfigError(f"cannot start {plan.argv[0]}: {exc}") from exc


def _refuse_settings(agent_args: Sequence[str]) -> None:
    """Refuse a ``--settings`` among the agent's arguments."""
    for arg in agent_args:
        if arg == SETTINGS_OPTION or arg.startswith(f"{SETTINGS_OPTION}="):
            message = (
                f"{SETTINGS_OPTION} after -- is refused: only one {SETTINGS_OPTION} "
                f"takes effect, so the settings atc passes would be lost. Give "
                f"{SETTINGS_OPTION} to atc itself, before --"
            )
            raise click.UsageError(message)


def _atc_environment(
    env: Mapping[str, str], ctx: BuildContext, account: Account, project: Project | None
) -> dict[str, str]:
    """Take ``env`` without atc's own variables, and add the ones for this session."""
    selected = {
        name: value for name, value in env.items() if not name.startswith(ENV_PREFIX)
    }
    selected["ATC_ACCOUNT"] = account.name
    selected["ATC_STATE_DIR"] = str(ctx.dirs.state_dir)
    if project is not None:
        selected["ATC_PROJECT"] = project.name
    return selected


def _changes(
    before: Mapping[str, str], after: Mapping[str, str]
) -> dict[str, str | None]:
    """Say how ``after`` differs from ``before``: new values, ``None`` if removed."""
    return {
        name: after.get(name)
        for name in sorted({*before, *after})
        if after.get(name) != before.get(name)
    }


def _agent_binary(
    registry: Registry,
    agent: str,
    self_path: Path,
    which: Callable[[str], str | None],
) -> str:
    """Find the agent's binary, which must not be atc."""
    name = registry.agent_bins.get(agent, agent)
    found = which(name)
    if found is None:
        message = (
            f"agent binary '{name}' not found; put it on PATH, or set "
            f"agents.{agent}.bin in {registry.path}"
        )
        raise ConfigError(message)
    if Path(found).resolve() == self_path.resolve():
        message = (
            f"{found}: the {agent} binary resolves to atc itself; set "
            f"agents.{agent}.bin in {registry.path} to the real one"
        )
        raise ConfigError(message)
    return found
