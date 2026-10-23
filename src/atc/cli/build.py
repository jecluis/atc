"""``atc build``: merge units and print the result."""

from pathlib import Path

import click
import rich_click

from atc.cli.options import conf_d_option, load_sources
from atc.errors import PathError
from atc.refs import Located, resolve_registry
from atc.resolve import resolve
from atc.ui import Ui


@click.command(cls=rich_click.RichCommand, name="build")
@click.argument("units", nargs=-1, required=True, metavar="UNIT...")
@conf_d_option
@click.option(
    "--lax",
    is_flag=True,
    help="Report settings keys Claude Code does not define as warnings, not errors.",
)
def build(units: tuple[str, ...], conf_d: Path | None, lax: bool) -> None:
    """
    Merge configuration units and print the result.

    Merges the named units, together with everything they import, and writes
    the resulting settings.local.json to standard output.

    Each UNIT is a path relative to the conf directory of a config source, for
    example langs/rust.json. If several sources have the unit, write
    @name/langs/rust.json to say which. Units are merged left to right, except
    that a unit an earlier one already imported keeps the position it had.
    """
    sources = load_sources(conf_d)
    roots: list[Located] = []
    for reference in units:
        root = resolve_registry(sources, reference)
        if root is None:
            raise PathError(f"'{reference}': no such unit")
        roots.append(root)

    ui = Ui.default()
    resolution = resolve(sources, roots, lax=lax)
    for warning in sources.warnings + resolution.warnings:
        ui.warning(warning)
    ui.json(resolution.settings)
