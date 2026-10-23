"""``atc show``: print the settings atc builds."""

from pathlib import Path

import click
import rich_click

from atc.cli.options import conf_d_option, lax_option, load_build_context
from atc.layers import build_account_layer, build_project_layer
from atc.registry import choose_account
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


@show.command(cls=rich_click.RichCommand, name="project")
@click.argument("name", metavar="NAME")
@click.option(
    "-a",
    "--account",
    "account_name",
    metavar="ACCOUNT",
    help=(
        "Build the settings for ACCOUNT instead of the project's own account, "
        "or the default account when the project names none."
    ),
)
@conf_d_option
@lax_option
def project(
    name: str, account_name: str | None, conf_d: Path | None, lax: bool
) -> None:
    """
    Print the settings of one project.

    Merges the project's unit, together with everything it imports, as it is
    for the account that works on the project, and writes the result to standard
    output as JSON. NAME is a project from the machine registry, machine.yaml in
    atc's configuration directory. The account is ACCOUNT if given, otherwise
    the project's account, otherwise the registry's default account. It must be
    one the project allows.

    A project with no unit, because the registry names none and
    projects/NAME.json does not exist, has no settings of its own: this prints
    an empty object and says so.

    Only the JSON goes to standard output, so it is safe to redirect or pipe.
    Warnings and errors go to standard error.
    """
    ctx = load_build_context(conf_d)
    found = ctx.registry.project(name)
    chosen = choose_account(ctx.registry, found, account_name)
    layer = build_project_layer(ctx, found, chosen, lax=lax)

    ui = Ui.default()
    for warning in (*ctx.sources.warnings, *ctx.registry.warnings):
        ui.warning(warning)
    if layer is None:
        ui.warning(
            f"project '{name}' has no project layer: {found.unit} does not exist"
        )
        ui.json({})
        return
    for warning in layer.warnings:
        ui.warning(warning)
    ui.json(layer.settings)
