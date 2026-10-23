"""Bringing an account's settings in line with the configuration."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from atc.backends import backend_for
from atc.context import BuildContext
from atc.errors import ConfigError, UnitError, WriteError
from atc.fsutil import atomic_write, backup
from atc.jsonval import JsonObject, canonical, dumps
from atc.layers import build_account_layer
from atc.registry import Account
from atc.rules import RuleWarning, dead_rules
from atc.state import SyncState

type Situation = Literal["missing", "current", "never-synced", "unchanged", "edited"]
"""
How the live settings file stands against what atc would write and last wrote:
``missing``, already ``current``, ``never-synced`` and different, ``unchanged``
since the last sync, or ``edited`` outside atc.
"""


@dataclass(frozen=True, slots=True)
class SettingsPlan:
    """
    What to do with one settings file.

    ``new`` is what the units produce, and the three texts are the versions to
    compare: ``new_text`` is how it would be written, ``live_text`` the live file
    and ``last_text`` what was last written, each ``None`` when there is none.
    Live text that is valid is rendered the way atc renders, so a comparison
    shows changes in content and not in formatting. ``write`` says the file
    will be replaced, and ``blocked`` that it would be, but a flag the user did
    not give is needed first.
    """

    situation: Situation
    new: JsonObject
    new_text: str
    live_text: str | None
    last_text: str | None
    write: bool
    blocked: bool


@dataclass(frozen=True, slots=True)
class AccountPlan:
    """
    What ``sync`` does for one account.

    ``warnings`` are the layer's, and ``rule_warnings`` are the permission rules
    that never apply. Neither stops the sync.
    """

    account: Account
    settings_path: Path
    settings: SettingsPlan
    rule_warnings: tuple[RuleWarning, ...]
    warnings: tuple[str, ...]


def decide_settings(
    new: JsonObject,
    live_text: str | None,
    last: JsonObject | None,
    *,
    parse: Callable[[str], JsonObject],
    take_over: bool,
    discard_live: bool,
    render: Callable[[JsonObject], str] = dumps,
) -> SettingsPlan:
    """
    Decide what happens to a settings file.

    ``live_text`` is the file, or ``None`` when there is none, and ``last`` what
    atc last wrote, or ``None`` when that is unknown. Versions are compared as
    canonical JSON, so only a change of content counts. ``parse`` reads the
    live file, and a file it refuses equals nothing: it is never synced when
    ``last`` is unknown and edited outside atc otherwise. ``take_over`` allows
    writing over a file that was never synced, and ``discard_live`` over one
    that was edited. ``render`` makes the text of a version.
    """
    live = None if live_text is None else _parsed(live_text, parse)
    shown = live_text if live is None else render(live)
    last_text = None if last is None else render(last)

    def plan(
        situation: Situation, *, write: bool, blocked: bool = False
    ) -> SettingsPlan:
        return SettingsPlan(
            situation, new, render(new), shown, last_text, write=write, blocked=blocked
        )

    if live_text is None:
        return plan("missing", write=True)
    if live is not None and canonical(live) == canonical(new):
        return plan("current", write=False)
    if last is None:
        return plan("never-synced", write=take_over, blocked=not take_over)
    if live is not None and canonical(live) == canonical(last):
        return plan("unchanged", write=True)
    return plan("edited", write=discard_live, blocked=not discard_live)


def plan_account(
    ctx: BuildContext,
    account: Account,
    *,
    take_over: bool,
    discard_live: bool,
    lax: bool,
) -> AccountPlan:
    """
    Work out what ``sync`` does for ``account``, changing nothing.

    Raises:
        ConfigError: the account's directory does not exist, the live file
            cannot be read, or the account's settings cannot be built, as
            :func:`atc.layers.build_account_layer` raises.

    """
    if not account.dir.is_dir():
        raise ConfigError(
            f"account '{account.name}': {account.dir} is not a directory; "
            + "atc does not create accounts, which means logging in"
        )
    layer = build_account_layer(ctx, account, lax=lax)
    backend = backend_for(account.agent, ctx.home)
    path = backend.settings_file(account)
    try:
        # Bytes that are not UTF-8 become escape sequences, which JSON rejects,
        # so such a file counts as not valid JSON.
        live_text = path.read_bytes().decode("utf-8", errors="backslashreplace")
    except FileNotFoundError:
        live_text = None
    except OSError as exc:
        raise ConfigError(f"{path}: cannot be read: {exc}") from exc
    settings = decide_settings(
        layer.settings,
        live_text,
        SyncState(ctx.dirs.state_dir, account.name).last_settings(),
        parse=lambda text: backend.parse(text, str(path)),
        take_over=take_over,
        discard_live=discard_live,
        render=backend.render,
    )
    return AccountPlan(
        account=account,
        settings_path=path,
        settings=settings,
        rule_warnings=dead_rules([layer]),
        warnings=layer.warnings,
    )


def apply_account(plan: AccountPlan, ctx: BuildContext, now: datetime) -> None:
    """
    Carry out ``plan``.

    A file that is to be written is first backed up, if it exists, then replaced
    atomically, and what was written is recorded. A file that is already current
    is only recorded. Anything else is left as it is.

    Raises:
        WriteError: a file could not be written.

    """
    settings = plan.settings
    state = SyncState(ctx.dirs.state_dir, plan.account.name)
    try:
        if settings.write:
            if settings.live_text is not None:
                _ = backup(plan.settings_path, state.backups_dir, now)
            atomic_write(plan.settings_path, settings.new_text)
        elif settings.situation != "current":
            return
        state.record_settings(settings.new)
    except OSError as exc:
        raise WriteError(f"account '{plan.account.name}': {exc}") from exc


def _parsed(text: str, parse: Callable[[str], JsonObject]) -> JsonObject | None:
    """Parse ``text``, or give ``None`` when ``parse`` refuses it."""
    try:
        return parse(text)
    except UnitError:
        return None
