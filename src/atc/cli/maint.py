"""``atc maint``: keep the repositories of guarded sessions packed."""

from datetime import UTC, datetime
from pathlib import Path

import click
import rich_click

from atc import exitcodes, guard
from atc.cli.options import HELP_OPTIONS, current_dirs
from atc.registry import REGISTRY_FILE, load_registry
from atc.ui import Ui


@click.command(cls=rich_click.RichCommand, name="maint", context_settings=HELP_OPTIONS)
def maint() -> None:
    """
    Keep the repositories of guarded sessions packed.

    Sessions cannot write a repository's packed history, and git's own automatic
    gc is off inside them, so this does the packing from outside. It goes
    through the repositories that "atc run" has guarded, which are listed in
    $XDG_STATE_HOME/atc/repos (~/.local/state/atc/repos when unset), and skips
    any that no longer exist, with a warning.

    For each repository it packs loose objects with a geometric repack, which is
    safe while sessions run. Only when no session is using the repository does
    it also pack the references and, if the last full "git gc" is older than
    maint.gc-hours in machine.yaml (24 by default), run "git gc". A repository
    that fails is reported on standard error and the others are still
    maintained. The status is 1 when any failed, and 0 otherwise.

    It reads machine.yaml but none of the config sources, and prints nothing
    when all is well. It is meant to run from a timer, outside the sandbox.
    """
    dirs = current_dirs()
    registry = load_registry(dirs.config_dir / REGISTRY_FILE, Path.home())
    failures = guard.maint(dirs, gc_hours=registry.gc_hours, now=datetime.now(UTC))
    ui = Ui.default()
    for failure in failures:
        ui.error(failure)
    if failures:
        raise click.exceptions.Exit(exitcodes.CONFIG)
