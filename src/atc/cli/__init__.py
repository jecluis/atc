"""The atc command line."""

from collections.abc import Callable, Sequence
from typing import cast

import click
import rich_click

from atc import exitcodes
from atc.cli.build import build
from atc.errors import ConfigError
from atc.ui import Ui

PROGRAM = "atc"


@click.group(cls=rich_click.RichGroup, name=PROGRAM)
@click.version_option(package_name=PROGRAM, prog_name=PROGRAM)
def app() -> None:
    """Start coding agents in the right account, with shared settings."""


app.add_command(build)


def main(argv: Sequence[str] | None = None) -> int:
    """
    Run atc and return the process exit status.

    A configuration problem is reported on standard error and gives status 1,
    a usage problem gives 2.
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
    return exitcodes.OK if status is None else status
