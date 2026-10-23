"""The variables that a unit's references can name."""

from collections.abc import Mapping
from pathlib import Path

from atc.backends import backend_for
from atc.context import BuildContext
from atc.registry import Account, Project
from atc.sources import Source
from atc.template import VarValue


def layer_variables(
    ctx: BuildContext, account: Account, project: Project | None = None
) -> dict[str, VarValue]:
    """
    Make the variables of a layer built for ``account``.

    These are every variable except the ones that depend on the unit being
    expanded (see :func:`with_source`). The ``project.*`` ones exist when a
    ``project`` is given, which is the project layer, and not otherwise. The
    named ``sources.<name>.*`` variables exist only in the multi layout.
    """
    variables: dict[str, VarValue] = {
        "home": str(ctx.home),
        "atc.config_dir": str(ctx.dirs.config_dir),
        "atc.conf_d": str(ctx.dirs.conf_d),
        "atc.state_dir": str(ctx.dirs.state_dir),
        "atc.runtime_dir": str(ctx.dirs.runtime_dir),
        "atc.install_dir": str(ctx.install_dir),
        "atc.python_dir": str(ctx.python_dir),
    }
    for source in ctx.sources.items:
        if source.name:
            variables.update(_source_variables(f"sources.{source.name}", source))
    variables.update({f"vars.{name}": v for name, v in ctx.registry.vars.items()})

    own = backend_for(account.agent, ctx.home)
    variables.update(
        {
            "account.name": account.name,
            "account.agent": account.agent,
            "account.dir": str(account.dir),
            "account.files": _strings(own.files(account)),
            "account.secrets": _strings(own.secrets(account)),
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
    variables["peers.dir"] = tuple(str(peer.dir) for peer in peers)
    variables["peers.files"] = tuple(
        file
        for peer in peers
        for file in _strings(backend_for(peer.agent, ctx.home).files(peer))
    )
    return variables


def with_source(
    variables: Mapping[str, VarValue], source: Source
) -> dict[str, VarValue]:
    """Add the ``source.*`` variables of ``source``, the one that holds a unit."""
    return {
        **variables,
        "source.name": source.name,
        **_source_variables("source", source),
    }


def _source_variables(prefix: str, source: Source) -> dict[str, VarValue]:
    return {
        f"{prefix}.root": str(source.root),
        f"{prefix}.conf": str(source.conf),
        f"{prefix}.assets": str(source.assets),
    }


def _strings(paths: tuple[Path, ...]) -> tuple[str, ...]:
    return tuple(str(path) for path in paths)
