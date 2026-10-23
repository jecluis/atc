"""The atc command line."""

from collections.abc import Callable, Sequence
from typing import cast, override

import click
import rich_click

from atc import exitcodes
from atc.cli.check import check
from atc.cli.maint import maint
from atc.cli.options import HELP_OPTIONS
from atc.cli.prj import prj
from atc.cli.run import run
from atc.cli.show import show
from atc.cli.skills import skills
from atc.cli.src import src
from atc.cli.sync import sync
from atc.errors import ConfigError
from atc.ui import Ui

PROGRAM = "atc"


_GROUP_OPTIONS = frozenset({"--help", "-h", "--version"})
"""The first arguments that are the group's own, not ``run``'s."""


class AtcGroup(rich_click.RichGroup):
    """
    The atc command group, where ``run`` is the command when none is named.

    No arguments, or a first argument that starts with ``-`` and is not one of
    the group's own options, means ``run``. That includes ``--``. Any other
    first word must be a command, so a typo never starts a session.
    """

    @override
    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if not args or (args[0].startswith("-") and args[0] not in _GROUP_OPTIONS):
            args = ["run", *args]
        return super().parse_args(ctx, args)


@click.group(cls=AtcGroup, name=PROGRAM, context_settings=HELP_OPTIONS)
@click.version_option(package_name=PROGRAM, prog_name=PROGRAM)
def app() -> None:
    """Start coding agents in the right account, with shared settings."""


app.add_command(check)
app.add_command(maint)
app.add_command(prj)
app.add_command(run)
app.add_command(show)
app.add_command(skills)
app.add_command(src)
app.add_command(sync)


def main(argv: Sequence[str] | None = None) -> int:
    """
    Run atc and return the process exit status.

    A configuration problem is reported on standard error and gives status 1,
    a usage problem gives 2, and an interrupt such as Ctrl-C gives 130.
    """
    # click types `main` as returning Any. Without standalone mode that is the
    # status of a click `Exit` (`--help`, `--version`) and otherwise what the
    # command function returned, which is None for every atc command.
    run = cast("Callable[..., int | None]", app.main)
    try:
        status = run(
            args=None if argv is None else list(argv),
            prog_name=PROGRAM,
            standalone_mode=False,
        )
    except ConfigError as exc:
        Ui.default().error(str(exc))
        return exitcodes.CONFIG
    except click.UsageError as exc:
        exc.show()
        return exitcodes.USAGE
    except (click.Abort, KeyboardInterrupt):
        Ui.default().info("aborted")
        return exitcodes.INTERRUPTED
    return exitcodes.OK if status is None else status
