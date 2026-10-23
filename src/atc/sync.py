"""Bringing an account's settings and links in line with the configuration."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from atc.backends import Backend, backend_for
from atc.context import BuildContext
from atc.errors import ConfigError, UnitError, WriteError
from atc.fsutil import atomic_write, backup
from atc.jsonval import JsonObject, canonical, dumps
from atc.layers import build_account_layer
from atc.links import LinkPlan, apply_link, decide_link, wanted_instructions
from atc.registry import Account
from atc.rules import RuleWarning, dead_rules
from atc.state import SyncState

type Part = Literal["settings", "instructions"]
"""What ``sync`` brings in line: the ``settings``, or the ``instructions`` link."""

ALL_PARTS: frozenset[Part] = frozenset({"settings", "instructions"})
"""Every part, which is what ``sync`` handles unless told otherwise."""

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

    ``settings`` is ``None`` when the settings were not among the parts planned.
    ``links`` are the link entries planned. ``warnings`` are the layer's, and
    ``rule_warnings`` are the permission rules that never apply. Neither stops
    the sync.
    """

    account: Account
    settings_path: Path
    settings: SettingsPlan | None
    links: tuple[LinkPlan, ...]
    rule_warnings: tuple[RuleWarning, ...]
    warnings: tuple[str, ...]

    @property
    def pending(self) -> bool:
        """Whether applying the plan writes anything."""
        return (self.settings is not None and self.settings.write) or any(
            link.pending for link in self.links
        )

    @property
    def blocked(self) -> bool:
        """Whether something is left unwritten for want of a flag."""
        return (self.settings is not None and self.settings.blocked) or any(
            link.blocked for link in self.links
        )


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
    parts: frozenset[Part] = ALL_PARTS,
) -> AccountPlan:
    """
    Work out what ``sync`` does for ``account``, changing nothing.

    Only the ``parts`` are planned: nothing is read, built or checked for the
    others. ``take_over`` also allows replacing a link or a file atc did not
    make.

    Raises:
        ConfigError: the account's directory does not exist, a live file cannot
            be read, the account's settings cannot be built, as
            :func:`atc.layers.build_account_layer` raises, or a file to link to
            does not exist, as :func:`atc.links.wanted_instructions` raises.

    """
    if not account.dir.is_dir():
        raise ConfigError(
            f"account '{account.name}': {account.dir} is not a directory; "
            + "atc does not create accounts, which means logging in"
        )
    backend = backend_for(account.agent, ctx.home)
    state = SyncState(ctx.dirs.state_dir, account.name)
    path = backend.settings_file(account)
    settings: SettingsPlan | None = None
    rule_warnings: tuple[RuleWarning, ...] = ()
    warnings: tuple[str, ...] = ()
    if "settings" in parts:
        layer = build_account_layer(ctx, account, lax=lax)
        settings = _plan_settings(
            layer.settings,
            path,
            state,
            backend,
            take_over=take_over,
            discard_live=discard_live,
        )
        rule_warnings = dead_rules([layer])
        warnings = layer.warnings
    links: tuple[LinkPlan, ...] = ()
    if "instructions" in parts:
        entry = backend.instructions_file(account)
        recorded = state.links().get(str(entry))
        try:
            links = (
                decide_link(
                    entry,
                    wanted_instructions(ctx, account),
                    None if recorded is None else Path(recorded),
                    take_over=take_over,
                ),
            )
        except OSError as exc:
            raise ConfigError(f"{entry}: cannot be read: {exc}") from exc
    return AccountPlan(
        account=account,
        settings_path=path,
        settings=settings,
        links=links,
        rule_warnings=rule_warnings,
        warnings=warnings,
    )


def apply_account(plan: AccountPlan, ctx: BuildContext, now: datetime) -> None:
    """
    Carry out ``plan``.

    A settings file that is to be written is first backed up, if it exists, then
    replaced atomically, and what was written is recorded. A file that is
    already current is only recorded. Anything else is left as it is. Links are
    changed as :func:`atc.links.apply_link` says, and the record of the links
    atc made is updated for each entry changed or found current, and for no
    other, including the entries of parts that were not planned.

    Raises:
        WriteError: a file or link could not be written.

    """
    state = SyncState(ctx.dirs.state_dir, plan.account.name)
    try:
        if plan.settings is not None:
            _apply_settings(plan.settings, plan.settings_path, state, now)
        _apply_links(plan.links, state, now)
    except OSError as exc:
        raise WriteError(f"account '{plan.account.name}': {exc}") from exc


def _plan_settings(
    new: JsonObject,
    path: Path,
    state: SyncState,
    backend: Backend,
    *,
    take_over: bool,
    discard_live: bool,
) -> SettingsPlan:
    """Decide what happens to the settings file at ``path``."""
    try:
        # Bytes that are not UTF-8 become escape sequences, which JSON rejects,
        # so such a file counts as not valid JSON.
        live_text = path.read_bytes().decode("utf-8", errors="backslashreplace")
    except FileNotFoundError:
        live_text = None
    except OSError as exc:
        raise ConfigError(f"{path}: cannot be read: {exc}") from exc
    return decide_settings(
        new,
        live_text,
        state.last_settings(),
        parse=lambda text: backend.parse(text, str(path)),
        take_over=take_over,
        discard_live=discard_live,
        render=backend.render,
    )


def _apply_settings(
    settings: SettingsPlan, path: Path, state: SyncState, now: datetime
) -> None:
    """Write the settings file as ``settings`` says, and record what it holds."""
    if settings.write:
        if settings.live_text is not None:
            _ = backup(path, state.backups_dir, now)
        atomic_write(path, settings.new_text)
    elif settings.situation != "current":
        return
    state.record_settings(settings.new)


def _apply_links(links: tuple[LinkPlan, ...], state: SyncState, now: datetime) -> None:
    """Change the link entries, and keep the record of the links atc made."""
    recorded = state.links()
    updated = dict(recorded)
    try:
        for link in links:
            if link.action in {"conflict", "leave"}:
                continue
            owned = apply_link(link, state.backups_dir, now)
            if owned is None:
                _ = updated.pop(str(link.entry), None)
            else:
                updated[str(link.entry)] = str(owned)
    finally:
        # Also when a later entry failed, so that a link made already is known.
        if updated != recorded:
            state.record_links(updated)


def _parsed(text: str, parse: Callable[[str], JsonObject]) -> JsonObject | None:
    """Parse ``text``, or give ``None`` when ``parse`` refuses it."""
    try:
        return parse(text)
    except UnitError:
        return None
