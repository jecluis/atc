"""``atc check``: verify a session's protections from inside it."""

import os
from pathlib import Path

import click
import rich_click

from atc import exitcodes
from atc.check import RealProbe, Report, in_sandbox, run_checks
from atc.cli.options import HELP_OPTIONS, current_dirs
from atc.context import current_install_dir, current_python_dir
from atc.registry import REGISTRY_FILE, load_registry
from atc.ui import Ui


@click.command(cls=rich_click.RichCommand, name="check", context_settings=HELP_OPTIONS)
def check() -> None:
    """
    Check that this session's protections hold.

    Run it through the agent's shell tool, so it runs inside the sandbox. It
    only inspects, never writes, and reads the machine registry but none of the
    config sources. Prints one PASS or FAIL line per check:

    \b
    - the repository's objects/pack and packed-refs, and hooks and config when
      present, cannot be written, new objects can be, git's automatic gc is
      off, and the directory above the worktree cannot be written
    - the session's own account's credentials, and every other account's
      directory and files, cannot be read
    - atc's configuration, config sources, state and runtime directories, its
      installation and the Python installation it runs on cannot be written
    - the files that the account's instructions file and every entry of its
      skills directory lead to cannot be written

    A check that does not apply, such as the repository checks outside a
    repository, gives a plain line saying so instead. If git cannot inspect
    the repository, that is a FAIL. The exit status is 0 when everything passes
    and 3 otherwise. If the home directory can be written this is not a
    sandbox: it says so and exits with status 2.
    """  # noqa: D301  (\b keeps the list above from being rewrapped)
    home = Path.home()
    probe = RealProbe()
    ui = Ui.default()
    if not in_sandbox(probe, home):
        message = "the home directory is writable, so this is not a sandbox; "
        ui.error(message + "run atc check through the agent's shell tool")
        raise click.exceptions.Exit(exitcodes.USAGE)
    dirs = current_dirs()
    registry = load_registry(dirs.config_dir / REGISTRY_FILE, home)
    report = run_checks(
        probe,
        registry=registry,
        dirs=dirs,
        install_dir=current_install_dir(),
        python_dir=current_python_dir(),
        home=home,
        env=os.environ,
    )
    show_report(ui, report)
    if not report.ok:
        raise click.exceptions.Exit(exitcodes.CHECK)


def show_report(ui: Ui, report: Report) -> None:
    """Write the notes as plain lines, then one ``PASS`` or ``FAIL`` line per result."""
    for note in report.notes:
        ui.dim(note)
    for result in report.results:
        ui.verdict(result.ok, result.what)
