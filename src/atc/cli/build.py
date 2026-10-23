"""``atc build``: merge units and print the result."""

import os
from pathlib import Path

import click
import rich_click

from atc.paths import dirs_from_env
from atc.resolve import resolve
from atc.ui import Ui


@click.command(cls=rich_click.RichCommand, name="build")
@click.argument("units", nargs=-1, required=True, metavar="UNIT...")
@click.option(
    "--root",
    metavar="DIR",
    help=(
        "Read units from DIR instead of the default, which is the conf.d "
        "directory under $XDG_CONFIG_HOME/atc (~/.config/atc when unset)."
    ),
)
@click.option(
    "--lax",
    is_flag=True,
    help="Report settings keys Claude Code does not define as warnings, not errors.",
)
def build(units: tuple[str, ...], root: str | None, lax: bool) -> None:
    """
    Merge configuration units and print the result.

    Merges the named units, together with everything they import, and writes
    the resulting settings.local.json to standard output.

    Each UNIT is a path relative to the root, for example langs/rust.json.
    Units are merged left to right, except that a unit an earlier one already
    imported keeps the position it had.
    """
    if root is not None and not root:
        raise click.UsageError("--root requires a directory")

    root_dir = Path(root) if root else dirs_from_env(os.environ, Path.home()).conf_d
    if not root_dir.is_dir():
        raise click.UsageError(f"no configuration root at {root_dir}")

    ui = Ui.default()
    resolution = resolve(root_dir, units, lax=lax)
    for warning in resolution.warnings:
        ui.warning(warning)
    ui.json(resolution.settings)
