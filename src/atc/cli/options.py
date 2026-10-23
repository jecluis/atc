"""What the commands that read config sources have in common."""

import os
from pathlib import Path

import click

from atc.paths import Dirs, dirs_from_env
from atc.sources import Sources, discover


def _directory(
    _ctx: click.Context, _param: click.Parameter, value: str | None
) -> Path | None:
    if value is not None and not value:
        raise click.UsageError("--conf-d requires a directory")
    return None if value is None else Path(value)


conf_d_option = click.option(
    "--conf-d",
    "conf_d",
    metavar="DIR",
    type=click.Path(file_okay=False),
    callback=_directory,
    help=(
        "Read config sources from DIR instead of the default, which is the "
        "conf.d directory under $XDG_CONFIG_HOME/atc (~/.config/atc when unset)."
    ),
)
"""The ``--conf-d DIR`` option, passed to the command as ``conf_d: Path | None``."""


def current_dirs() -> Dirs:
    """Work out the directories atc uses from this process's environment."""
    return dirs_from_env(os.environ, Path.home())


def load_sources(conf_d: Path | None) -> Sources:
    """Discover the sources in ``conf_d``, or in the default directory if ``None``."""
    directory = conf_d if conf_d is not None else current_dirs().conf_d
    return discover(directory, uid=os.getuid())
