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
from atc.layers import (
    build_account_layer,
    build_project_layer,
    merge_fragment,
    session_layer,
)
from atc.registry import Account, Project, Registry, choose_account
from atc.rules import RuleWarning, dead_rules

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
    ``rule_warnings`` are the permission rules that can never apply, found only
    when the plan was made to explain itself.
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
    rule_warnings: tuple[RuleWarning, ...]


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
    lax: bool,
    explain: bool,
) -> LaunchPlan:
    """
    Plan how to start the agent in ``cwd``, with the environment ``env``.

    The account is ``account`` if given, else the project's, else the default.
    The settings for the session are those of the project's unit, if it has
    one, with ``settings`` merged over them. ``agent_args`` go to the agent.
    ``self_path`` is atc's own executable, which the agent's binary must not
    be. ``which`` finds a binary on ``PATH``. Set ``lax`` to report unknown
    settings keys in the project's units as warnings instead of errors.

    Set ``explain`` to find the rules that can never apply, in the account's
    settings, the project's and ``settings`` together. Only then is the
    account's unit built. The agent reads the account's synced settings, so a
    launch has no need of the unit. If it cannot be built, even then, the plan
    has a warning that says why, and only the other rules are checked.

    Raises:
        click.UsageError: ``agent_args`` has a ``--settings``.
        GitError: git cannot tell whether ``cwd`` is in a repository.
        RegistryError: there is no such account, the project does not allow it,
            or several projects are the same repository.
        PolicyError: ``settings`` set a key the session layer must not, or a
            unit sets one that its layer must not.
        MergeError: ``settings`` give a key another type than the project does.
        ConfigError: the agent's binary is not found, or is atc, or a unit of
            the project cannot be built.

    """
    _refuse_settings(agent_args)
    session = None if settings is None else session_layer(settings, SETTINGS_OPTION)

    common = git_common_dir(cwd)
    warnings: list[str] = [*ctx.sources.warnings, *ctx.registry.warnings]
    project = find_project(
        ctx.registry, common, git_common_dir=git_common_dir, warn=warnings.append
    )
    chosen = choose_account(ctx.registry, project, account)
    project_layer = (
        None if project is None else build_project_layer(ctx, project, chosen, lax=lax)
    )
    layers = [layer for layer in (project_layer, session) if layer is not None]
    fragment = merge_fragment(layers)
    warnings.extend(warning for layer in layers for warning in layer.warnings)
    checked = layers
    if explain:
        try:
            checked = [build_account_layer(ctx, chosen, lax=lax), *layers]
        except ConfigError as exc:
            warnings.append(f"rule check skipped for the account layer: {exc}")
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
        warnings=tuple(warnings),
        rule_warnings=dead_rules(checked) if explain else (),
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
