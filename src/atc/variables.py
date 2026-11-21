"""The variables that a unit's references can name."""

from collections.abc import Mapping
from pathlib import Path

from atc.backends import backend_for
from atc.context import BuildContext
from atc.projectdirs import account_dirs, common_dir, memory_dir
from atc.registry import Account, Project, own_config_projects
from atc.sources import Source
from atc.state import SyncState
from atc.template import VarValue


def layer_variables(
    ctx: BuildContext, account: Account, project: Project | None = None
) -> dict[str, VarValue]:
    """
    Make the variables of a layer built for ``account``.

    These are every variable except the ones that depend on the unit being
    expanded (see :func:`with_source`). The ``project.*`` ones exist when a
    ``project`` is given, which is the project layer, and not otherwise. The
    named ``sources.<name>.*`` variables exist only in the multi layout. Their
    ``rendered`` is where ``atc sync`` renders the source's templated assets for
    ``account``.
    """
    state = SyncState(ctx.dirs.state_dir, account.name)
    variables: dict[str, VarValue] = {
        "home": str(ctx.home),
        "atc.config_dir": str(ctx.dirs.config_dir),
        "atc.conf_d": str(ctx.dirs.conf_d),
        "atc.state_dir": str(ctx.dirs.state_dir),
        "atc.runtime_dir": str(ctx.dirs.runtime_dir),
        "atc.data_dir": str(ctx.dirs.data_dir),
        "atc.install_dir": str(ctx.install_dir),
        "atc.python_dir": str(ctx.python_dir),
    }
    for source in ctx.sources.items:
        if source.name:
            variables.update(
                _source_variables(
                    f"sources.{source.name}", source, state.rendered(source)
                )
            )
    variables.update({f"vars.{name}": v for name, v in ctx.registry.vars.items()})

    own = backend_for(account.agent, ctx.home)
    variables.update(
        {
            "account.name": account.name,
            "account.agent": account.agent,
            "account.dir": str(account.dir),
            "account.files": _strings(own.files(account)),
            "account.secrets": _strings(_secrets(ctx, account)),
        }
    )
    variables.update({f"account.vars.{n}": v for n, v in account.vars.items()})
    if project is not None:
        variables["project.name"] = project.name
        variables["project.repo"] = str(project.repo)
        variables.update({f"project.vars.{n}": v for n, v in project.vars.items()})

    peers = [
        peer for peer in ctx.registry.accounts.values() if peer.name != account.name
    ]
    variables["peers.dir"] = _strings(_peer_dirs(ctx, account, peers))
    variables["peers.files"] = tuple(
        file
        for peer in peers
        for file in _strings(backend_for(peer.agent, ctx.home).files(peer))
    )
    return variables


def with_source(
    variables: Mapping[str, VarValue], source: Source, rendered: Path
) -> dict[str, VarValue]:
    """
    Add the ``source.*`` variables of ``source``, the one that holds a unit.

    ``rendered`` is where ``atc sync`` renders that source's templated assets for
    the layer's account.
    """
    return {
        **variables,
        "source.name": source.name,
        **_source_variables("source", source, rendered),
    }


def _source_variables(
    prefix: str, source: Source, rendered: Path
) -> dict[str, VarValue]:
    return {
        f"{prefix}.root": str(source.root),
        f"{prefix}.conf": str(source.conf),
        f"{prefix}.assets": str(source.assets),
        f"{prefix}.rendered": str(rendered),
    }


def _secrets(ctx: BuildContext, account: Account) -> tuple[Path, ...]:
    """
    Give the secrets of ``account``: its own, then those of its project directories.

    Of a project directory, only the files that exist are given, since the
    sandbox puts a placeholder where a denied path is missing.
    """
    backend = backend_for(account.agent, ctx.home)
    found = list(backend.secrets(account))
    for there in account_dirs(ctx.dirs, ctx.registry, account)[1:]:
        found.extend(path for path in backend.secrets(there) if path.exists())
    return tuple(found)


def _peer_dirs(
    ctx: BuildContext, account: Account, peers: list[Account]
) -> tuple[Path, ...]:
    """
    Give the directories that ``account``'s sessions are kept out of.

    Those are every other account's directory, then their project configuration
    directories that exist, then the auto memory and shared directories that
    exist of the projects with their own directories that ``account`` may not
    work on.
    """
    own = [peer.dir for peer in peers]
    projects = [
        there.dir
        for peer in peers
        for there in account_dirs(ctx.dirs, ctx.registry, peer)[1:]
    ]
    allowed = {p.name for p in own_config_projects(ctx.registry, account.name)}
    closed = [
        project
        for project in ctx.registry.projects.values()
        if project.own_config_dir and project.name not in allowed
    ]
    shared = [
        path
        for project in closed
        for path in (memory_dir(ctx.dirs, project), common_dir(ctx.dirs, project))
    ]
    return (*own, *projects, *(path for path in shared if path.is_dir()))


def _strings(paths: tuple[Path, ...]) -> tuple[str, ...]:
    return tuple(str(path) for path in paths)
