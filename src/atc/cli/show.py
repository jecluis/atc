"""``atc show``: print the settings atc builds."""

from pathlib import Path

import click
import rich_click

from atc.cli.options import conf_d_option, lax_option, load_build_context
from atc.layers import build_account_layer
from atc.ui import Ui


@click.group(cls=rich_click.RichGroup, name="show")
def show() -> None:
    """Print the settings atc builds."""


@show.command(cls=rich_click.RichCommand, name="account")
@click.argument("name", metavar="NAME")
@conf_d_option
@lax_option
def account(name: str, conf_d: Path | None, lax: bool) -> None:
    """
    Print the settings of one account.

    Merges the account's unit, together with everything it imports, and writes
    the result to standard output as JSON. NAME is an account from the machine
    registry, machine.yaml in atc's configuration directory.

    Only the JSON goes to standard output, so it is safe to redirect or pipe.
    Warnings and errors go to standard error.
    """
    ctx = load_build_context(conf_d)
    layer = build_account_layer(ctx, ctx.registry.account(name), lax=lax)

    ui = Ui.default()
    for warning in (*ctx.sources.warnings, *ctx.registry.warnings, *layer.warnings):
        ui.warning(warning)
    ui.json(layer.settings)
