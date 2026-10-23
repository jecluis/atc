"""Planning how to start an agent in the right account, and starting it."""

import os
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

import click

from atc import git, guard
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
from atc.paths import Dirs
from atc.registry import AUTO_ACCOUNT, Account, Project, Registry, choose_account
from atc.rules import RuleWarning, dead_rules
from atc.ui import Ui
from atc.usage import UsageRecord, choose_auto, read_usage

SETTINGS_OPTION = "--settings"
SESSION_ENTRIES = "session entries"
"""What names the settings atc adds for a guarded session, in messages."""
NO_GUARD_WARNING = (
    "--no-guard: the repository's packed history is not write-protected in this "
    "session, and repository maintenance can run while it is open"
)
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
    ``notes`` say what atc decided that the person should be told, as ``-a
    auto`` does when it picks an account. ``rule_warnings`` are the permission
    rules that can never apply, found only when the plan was made to explain
    itself.

    ``guarded`` says the session protects the repository's packed history: the
    fragment and the environment already hold what that takes, and
    :func:`execute` registers the repository and holds its lock. It is false
    outside a repository, and with ``no_guard``. ``protected`` are the paths of
    that history that existed when the fragment was made. ``snapshot`` is the
    snapper config of the project, whether or not the session is guarded: the
    snapshot is made just before the agent starts.
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
    notes: tuple[str, ...]
    rule_warnings: tuple[RuleWarning, ...]
    guarded: bool
    protected: tuple[Path, ...]
    snapshot: str | None


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
    no_guard: bool,
) -> LaunchPlan:
    """
    Plan how to start the agent in ``cwd``, with the environment ``env``.

    The account is ``account`` if given, else the project's, else the default.
    ``account`` may be ``"auto"``: if the project lists ``accounts``, the least
    used of them, by what :func:`atc.usage.read_usage` finds. The plan's notes
    say which was chosen and why, and its warnings have any problem with a
    record. With no such project there is nothing to choose between, so ``auto``
    is as if no account was given, and no record is read.

    The settings for the session are those of the project's unit, if it has
    one, then the agent's entries that protect the repository's packed history,
    then ``settings``, each merged over the ones before. ``agent_args`` go to
    the agent. ``self_path`` is atc's own executable, which the agent's binary
    must not be. ``which`` finds a binary on ``PATH``. Set ``lax`` to report
    unknown settings keys in the project's units as warnings instead of errors.

    Inside a repository the session is guarded: it gets those entries, and git's
    automatic gc is turned off in its environment. Set ``no_guard`` to leave
    both out, with a warning. Outside a repository there is nothing to guard.
    The plan has the project's snapshot config either way.

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
            the project cannot be built, or ``env`` has a ``GIT_CONFIG_COUNT``
            that is not a number.

    """
    _refuse_settings(agent_args)
    session = None if settings is None else session_layer(settings, SETTINGS_OPTION)

    common = git_common_dir(cwd)
    warnings: list[str] = [*ctx.sources.warnings, *ctx.registry.warnings]
    project = find_project(
        ctx.registry, common, git_common_dir=git_common_dir, warn=warnings.append
    )
    name, notes = _resolve_auto(ctx, project, account, warnings)
    chosen = choose_account(ctx.registry, project, name)
    backend = backend_for(chosen.agent, ctx.home)
    project_layer = (
        None if project is None else build_project_layer(ctx, project, chosen, lax=lax)
    )
    entries = None
    protected: tuple[Path, ...] = ()
    if common is not None and not no_guard:
        protected = guard.protected_paths(common)
        entries = session_layer(backend.session_fragment(common), SESSION_ENTRIES)
    layers = [layer for layer in (project_layer, entries, session) if layer is not None]
    fragment = merge_fragment(layers)
    warnings.extend(warning for layer in layers for warning in layer.warnings)
    if common is not None and no_guard:
        warnings.append(NO_GUARD_WARNING)
    checked = layers
    if explain:
        try:
            checked = [build_account_layer(ctx, chosen, lax=lax), *layers]
        except ConfigError as exc:
            warnings.append(f"rule check skipped for the account layer: {exc}")
    selected = backend.environment(chosen, _atc_environment(env, ctx, chosen, project))
    guarded = entries is not None
    if guarded:
        selected = guard.with_gc_disabled(selected)
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
        notes=notes,
        rule_warnings=dead_rules(checked) if explain else (),
        guarded=guarded,
        protected=protected,
        snapshot=None if project is None else project.snapshot,
    )


def execute(
    plan: LaunchPlan,
    dirs: Dirs,
    *,
    run_snapshot: Callable[[Sequence[str]], int] = guard.run_command,
) -> NoReturn:
    """
    Replace atc with the agent, started as ``plan`` says.

    A guarded plan first adds its repository to the list in ``dirs.state_dir``,
    then takes a shared lock on it. The lock stays with the agent, and with every
    process the agent starts. If packed history appeared while the lock was
    awaited, a warning says the session does not protect it. Then, guarded or
    not, the snapshot is made if the plan has one. A lock or a snapshot that
    cannot be had is a warning, and the agent starts anyway. ``run_snapshot``
    runs the snapshot command.

    Raises:
        ConfigError: the repository cannot be added to the list, or the agent
            cannot be started.

    """
    ui = Ui.default()
    if plan.guarded and plan.common_dir is not None:
        _prepare_guard(plan, plan.common_dir, dirs, ui)
    if plan.snapshot is not None:
        warning = guard.take_snapshot(plan.snapshot, plan.cwd, run=run_snapshot)
        if warning is not None:
            ui.warning(warning)
    try:
        os.execvpe(plan.argv[0], plan.argv, plan.env)  # noqa: S606 - the planned agent
    except OSError as exc:
        raise ConfigError(f"cannot start {plan.argv[0]}: {exc}") from exc


def _prepare_guard(plan: LaunchPlan, common: Path, dirs: Dirs, ui: Ui) -> None:
    """Register the repository and take its lock, warning through ``ui``."""
    try:
        guard.register_repo(dirs, common)
    except OSError as exc:
        raise ConfigError(f"cannot register {common}: {exc}") from exc
    # The descriptor is not closed: it stays open through the exec, with the lock.
    _, warning = guard.open_session_lock(dirs, common)
    if warning is not None:
        ui.warning(warning)
    appeared = [p for p in guard.protected_paths(common) if p not in plan.protected]
    if appeared:
        names = ", ".join(str(path) for path in appeared)
        message = (
            f"packed history appeared while atc was starting: {names}; this "
            "session starts with it not protected, so restart it to protect it"
        )
        ui.warning(message)


def _resolve_auto(
    ctx: BuildContext,
    project: Project | None,
    account: str | None,
    warnings: list[str],
) -> tuple[str | None, tuple[str, ...]]:
    """
    Turn ``account`` into the name to pick, which is the least used under ``auto``.

    Returns that name, and the notes that say what was decided. Anything but
    ``auto`` is returned as it is, with no notes. ``auto`` is ``None``, so that
    the project's account and then the default apply, when ``project`` leaves no
    choice. Otherwise the problems with the records are added to ``warnings``,
    and the notes say the choice and why.
    """
    if account != AUTO_ACCOUNT:
        return account, ()
    if project is None or not project.accounts:
        return None, ()
    candidates = tuple(dict.fromkeys(project.accounts))
    records: dict[str, UsageRecord | None] = {}
    for name in candidates:
        records[name], warning = read_usage(ctx.dirs, name)
        if warning is not None:
            warnings.append(warning)
    chosen, reason = choose_auto(candidates, records, datetime.now(UTC))
    return chosen, (f"-a {AUTO_ACCOUNT} chose {chosen}: {reason}",)


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
