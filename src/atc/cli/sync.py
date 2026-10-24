"""``atc sync``: bring each account's files in line with the configuration."""

import difflib
from datetime import UTC, datetime
from pathlib import Path

import click
import rich_click

from atc import exitcodes
from atc.cli.options import (
    chosen_accounts,
    conf_d_option,
    lax_option,
    load_build_context,
)
from atc.errors import ConfigError
from atc.links import LinkPlan
from atc.skills import discover_skills
from atc.sync import AccountPlan, AssetPlan, SettingsPlan, apply_account, plan_account
from atc.ui import Ui


@click.command(cls=rich_click.RichCommand, name="sync")
@click.argument("names", nargs=-1, metavar="[ACCOUNT]...")
@click.option(
    "--check",
    is_flag=True,
    help="Write nothing, and exit with status 3 if anything would be written.",
)
@click.option(
    "--diff",
    "show_diff",
    is_flag=True,
    help="Write nothing, and show what would change in each account.",
)
@click.option(
    "--take-over",
    is_flag=True,
    help=(
        "Write settings that atc never wrote but that differ from what it builds, "
        "and replace a link or file in the way of the instructions file or of a "
        "skill link."
    ),
)
@click.option(
    "--discard-live",
    is_flag=True,
    help="Write settings that were edited outside atc, discarding the edit.",
)
@lax_option
@conf_d_option
def sync(
    names: tuple[str, ...],
    check: bool,
    show_diff: bool,
    take_over: bool,
    discard_live: bool,
    lax: bool,
    conf_d: Path | None,
) -> None:
    """
    Write each account's settings, link its instructions and skills, render templates.

    Builds the settings of each ACCOUNT, or of every account, and writes them to
    settings.json in the account's directory. The directory must exist already:
    atc does not create accounts. Nothing is written until every account has been
    built, so an error in building one leaves all of them as they were. A write
    that fails stops the sync there, and the accounts before it stay written.

    The settings are compared with what atc wrote last, which it remembers under
    $XDG_STATE_HOME/atc/sync (~/.local/state/atc/sync when unset), and compared
    only by content, so a file that was just reformatted is not an edit. Each
    account is in one of these situations:

    \b
    - settings.json does not exist: it is written.
    - It already has the settings atc builds: nothing is written.
    - atc never wrote it and it differs: it is shown, and left alone unless
      --take-over is given.
    - It is what atc last wrote: it is written.
    - It differs from what atc last wrote, or is not valid JSON: the edit is
      shown, and left alone unless --discard-live is given.

    Before it replaces a settings.json, atc copies the file to
    sync/ACCOUNT/backups in the same state directory, keeping the last ten. The
    file is then replaced in one step and keeps its permissions. To keep an edit
    made outside atc, move it into a unit, pull the unit's source, and run sync
    again.

    An account whose registry entry sets agents-md-file, or use-default-agents-md,
    also gets its instructions file (CLAUDE.md for Claude) as a symbolic link to
    that file. atc re-points or removes a link it made when the configuration
    changes. It never touches anything else: a link or file it did not make is a
    conflict, which --take-over resolves by replacing a link, or by backing a file
    up and replacing it. A directory is never replaced.

    A file under a source's assets named NAME.in.EXT, or NAME.in, is a template.
    sync renders it for each account, as NAME.EXT or NAME, into
    sync/ACCOUNT/assets in the same state directory, which sessions must not be
    able to write to. Its {{...}} references are replaced as in a unit's
    settings, with the account's variables and those of the template's source,
    and the copy is executable when the template is. Units name the copies with
    {{source.rendered}}, and an instructions file that is a template is linked
    as its copy. A copy whose template is gone is removed.

    An account's registry entry may also select skills. Each one becomes a link
    named for the skill in the skills directory of the account's directory,
    which is created when it is missing. A skill that is deselected loses its
    link in the same way. Entries in that directory that atc did not make, such as
    Claude Code's own, are never touched, except that a link or file where a
    selected skill is to go is a conflict, as above.

    Prints one line per account, one more for each link that changes or is in
    conflict and for each rendered copy that changes, and on standard error the
    warnings of the account's settings and of the skills that were skipped or
    whose front matter looks wrong. Exits
    with status 3 when something was left alone, or with --check when anything
    would be written. Run it from your own terminal: Claude Code does not let its
    sandboxed commands write settings.json.
    """  # noqa: D301  (\b keeps the list below from being rewrapped)
    if conf_d is not None and not (check or show_diff):
        raise click.UsageError(
            "--conf-d only works with --check or --diff, because sync writes "
            + "only what the default sources build"
        )
    ctx = load_build_context(conf_d)
    ui = Ui.default()
    catalog = discover_skills(ctx.sources)
    for warning in (*ctx.sources.warnings, *ctx.registry.warnings, *catalog.warnings):
        ui.warning(warning)
    accounts = chosen_accounts(ctx, names)
    plans: list[AccountPlan] = []
    for account in accounts:
        try:
            plans.append(
                plan_account(
                    ctx,
                    account,
                    take_over=take_over,
                    discard_live=discard_live,
                    lax=lax,
                    catalog=catalog,
                )
            )
        except ConfigError as exc:
            ui.error(str(exc))
    if len(plans) != len(accounts):
        raise click.exceptions.Exit(exitcodes.CONFIG)

    dry = check or show_diff
    now = datetime.now(UTC)
    shown: set[str] = set()
    for plan in plans:
        if not dry:
            apply_account(plan, ctx, now)
        name = plan.account.name
        if plan.settings is not None:
            ui.line(f"{name}: {_status(plan.settings, dry=dry)}")
            if show_diff or plan.settings.blocked:
                ui.diff(_diff(plan.settings_path, plan.settings))
        for asset in plan.assets:
            if asset.pending:
                change = "(would change)" if dry else "(changed)"
                ui.line(f"{name}: {asset.describe()} {change}")
                if show_diff:
                    ui.diff(_asset_diff(asset))
        for link in plan.links:
            if link.pending or link.blocked:
                ui.line(f"{name}: {link.describe()} {link_status(link, dry=dry)}")
        for message in (
            *plan.warnings,
            *(warning.message() for warning in plan.rule_warnings),
        ):
            if message not in shown:
                shown.add(message)
                ui.warning(message)
    pending = check and any(plan.pending for plan in plans)
    if pending or any(plan.blocked for plan in plans):
        raise click.exceptions.Exit(exitcodes.CHECK)


def _status(settings: SettingsPlan, *, dry: bool) -> str:
    """Say what happens to the account's settings, or happened to them."""
    match settings.situation:
        case "never-synced" if settings.blocked:
            return "never synced and differs (--take-over writes it)"
        case "edited" if settings.blocked:
            return "edited outside atc (--discard-live overwrites it)"
        case _ if settings.write:
            return "would write" if dry else "written"
        case _:
            return "current"


def link_status(link: LinkPlan, *, dry: bool) -> str:
    """
    Say what happens to a link entry that changes or is in conflict.

    ``dry`` says nothing is being written, so the change is only planned.
    """
    if link.blocked:
        return "(conflict; --take-over replaces a file or link, never a directory)"
    return "(would change)" if dry else "(changed)"


def _diff(path: Path, settings: SettingsPlan) -> list[str]:
    """
    Show the change that matters for a settings file, as a unified diff.

    That is the edit, from what atc last wrote to the live file, when the live
    file was edited and atc stopped. It is from the live file to what atc builds
    otherwise.
    """
    name = str(path)
    if settings.situation == "edited" and settings.blocked:
        old, new = settings.last_text or "", settings.live_text or ""
        names = (f"{name} (last synced)", f"{name} (live)")
    else:
        old, new = settings.live_text or "", settings.new_text
        missing = settings.live_text is None
        names = ("/dev/null" if missing else f"{name} (live)", f"{name} (new)")
    return list(
        difflib.unified_diff(old.splitlines(), new.splitlines(), *names, lineterm="")
    )


def _asset_diff(asset: AssetPlan) -> list[str]:
    """Show the change to a rendered copy, as a unified diff, or of its mode alone."""
    name = str(asset.path)
    old = asset.live_text or ""
    new = "" if asset.rendering is None else asset.rendering.text
    names = (
        "/dev/null" if asset.live_text is None else f"{name} (live)",
        "/dev/null" if asset.rendering is None else f"{name} (new)",
    )
    lines = list(
        difflib.unified_diff(old.splitlines(), new.splitlines(), *names, lineterm="")
    )
    if not lines and asset.rendering is not None and asset.live_mode is not None:
        return [f"{name}: mode {asset.live_mode:o} → {asset.rendering.mode:o}"]
    return lines
