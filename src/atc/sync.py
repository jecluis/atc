"""Bringing an account's files in line with the configuration."""

import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from atc.backends import Backend, backend_for
from atc.context import BuildContext
from atc.errors import ConfigError, UnitError, WriteError
from atc.fsutil import atomic_write, backup, make_private_dir
from atc.jsonval import JsonObject, canonical, dumps
from atc.layers import build_account_layer
from atc.links import LinkPlan, apply_link, decide_link, wanted_instructions
from atc.registry import Account
from atc.rendered import Rendering, render_templates
from atc.rules import RuleWarning, dead_rules
from atc.skills import Catalog, discover_skills, wanted_skill_links
from atc.state import SyncState

type Part = Literal["settings", "instructions", "skills", "assets"]
"""
What ``sync`` brings in line: the ``settings``, the ``instructions`` link, the
links of the selected ``skills``, or the rendered copies of templated
``assets``.
"""

ALL_PARTS: frozenset[Part] = frozenset({"settings", "instructions", "skills", "assets"})
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


type AssetAction = Literal["none", "write", "remove"]
"""
What happens to a rendered copy: ``none`` (it is current), ``write`` it, or
``remove`` it, because its template is gone.
"""


@dataclass(frozen=True, slots=True)
class AssetPlan:
    """
    What to do with one rendered copy of a templated asset, at ``path``.

    ``rendering`` is what it should be, or ``None`` when its template is gone.
    ``live_text`` and ``live_mode`` are what it holds and its permissions now,
    each ``None`` when it is missing.
    """

    path: Path
    rendering: Rendering | None
    live_text: str | None
    live_mode: int | None
    action: AssetAction

    @property
    def pending(self) -> bool:
        """Whether applying the plan changes the copy."""
        return self.action != "none"

    def describe(self) -> str:
        """Say what changes, naming the template and its copy."""
        if self.rendering is None:
            return f"{self.path}: removed, as its template is gone"
        return f"{self.rendering.template.display} rendered as {self.path}"


@dataclass(frozen=True, slots=True)
class AccountPlan:
    """
    What ``sync`` does for one account.

    ``settings`` is ``None`` when the settings were not among the parts planned.
    ``links`` are the link entries planned, and ``assets`` the rendered copies.
    ``warnings`` are the layer's, and ``rule_warnings`` are the permission rules
    that never apply. Neither stops the sync.
    """

    account: Account
    settings_path: Path
    settings: SettingsPlan | None
    links: tuple[LinkPlan, ...]
    rule_warnings: tuple[RuleWarning, ...]
    warnings: tuple[str, ...]
    assets: tuple[AssetPlan, ...] = ()

    @property
    def pending(self) -> bool:
        """Whether applying the plan writes anything."""
        return (
            (self.settings is not None and self.settings.write)
            or any(link.pending for link in self.links)
            or any(asset.pending for asset in self.assets)
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
    catalog: Catalog | None = None,
) -> AccountPlan:
    """
    Work out what ``sync`` does for ``account``, changing nothing.

    Only the ``parts`` are planned: nothing is read, built or checked for the
    others, except that planning the instructions also plans the rendered
    copies, one of which a templated instructions file links to. ``take_over``
    also allows replacing a link or a file atc did not make. The skills are
    looked for in ``catalog``, or in the sources when it is ``None``.

    Raises:
        ConfigError: the account's directory does not exist, a live file cannot
            be read, the account's settings cannot be built, as
            :func:`atc.layers.build_account_layer` raises, a file to link to
            does not exist, as :func:`atc.links.wanted_instructions` raises, a
            selected skill does not, as :func:`atc.skills.wanted_skill_links`
            raises, or a template cannot be rendered, as
            :func:`atc.rendered.render_templates` raises.

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
    assets: list[AssetPlan] = []
    if not parts.isdisjoint({"assets", "instructions"}):
        assets = _plan_assets(render_templates(ctx, account), state.rendered_dir)
    links: list[LinkPlan] = []
    recorded = state.links()
    if "instructions" in parts:
        entry = backend.instructions_file(account)
        links.append(
            _plan_link(
                entry, wanted_instructions(ctx, account), recorded, take_over=take_over
            )
        )
    if "skills" in parts:
        links.extend(
            _plan_skills(
                ctx,
                account,
                backend.skills_dir(account),
                catalog if catalog is not None else discover_skills(ctx.sources),
                recorded,
                take_over=take_over,
            )
        )
    return AccountPlan(
        account=account,
        settings_path=path,
        settings=settings,
        links=tuple(links),
        rule_warnings=rule_warnings,
        warnings=warnings,
        assets=tuple(assets),
    )


def apply_account(plan: AccountPlan, ctx: BuildContext, now: datetime) -> None:
    """
    Carry out ``plan``.

    The rendered copies are written first, each in one step, so that an
    instructions file can link to its copy, and those whose template is gone
    are removed last, once no link atc makes leads to them. A settings file
    that is to be written is first backed up, if it exists, then replaced
    atomically, and what was written is recorded. A file that is already
    current is only recorded. Anything else is left as it is. Links are changed
    as :func:`atc.links.apply_link` says, and the record of the links atc made
    is updated for each entry changed or found current, and for no other,
    including the entries of parts that were not planned.

    Raises:
        WriteError: a file or link could not be written.

    """
    state = SyncState(ctx.dirs.state_dir, plan.account.name)
    try:
        _write_copies(plan.assets)
        if plan.settings is not None:
            _apply_settings(plan.settings, plan.settings_path, state, now)
        _apply_links(plan.links, state, now)
        _remove_copies(plan.assets, state.rendered_dir)
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


def _plan_assets(
    renderings: tuple[Rendering, ...], rendered_dir: Path
) -> list[AssetPlan]:
    """
    Plan a write of each copy that differs from its rendering, in text or mode.

    Any other file under ``rendered_dir`` is a copy whose template is gone, and
    is planned for removal: atc alone writes there. A link where a copy goes is
    replaced, never written through.

    Raises:
        ConfigError: a copy cannot be read, or ``rendered_dir`` or a directory
            under it that a copy goes in is a symbolic link, which atc does not
            follow.

    """
    for directory in {rendered_dir}.union(
        parent
        for rendering in renderings
        for parent in rendering.path.parents
        if parent.is_relative_to(rendered_dir)
    ):
        if directory.is_symlink():
            message = (
                f"{directory} is a symbolic link, and atc writes rendered copies "
                "only in directories; remove the link"
            )
            raise ConfigError(message)
    plans: list[AssetPlan] = []
    for rendering in renderings:
        live_text, mode = _read_copy(rendering.path)
        current = live_text == rendering.text and mode == rendering.mode
        action: AssetAction = "none" if current else "write"
        plans.append(AssetPlan(rendering.path, rendering, live_text, mode, action))
    wanted = {rendering.path for rendering in renderings}
    for path in sorted(rendered_dir.rglob("*")):
        if path not in wanted and (path.is_symlink() or not path.is_dir()):
            plans.append(AssetPlan(path, None, *_read_copy(path), "remove"))
    return plans


def _read_copy(path: Path) -> tuple[str | None, int | None]:
    """Read a rendered copy's text and mode, or ``None`` for each that it lacks."""
    try:
        if path.is_symlink() or not path.is_file():
            return None, None
        mode = stat.S_IMODE(path.stat().st_mode)
        return path.read_bytes().decode("utf-8", errors="backslashreplace"), mode
    except OSError as exc:
        raise ConfigError(f"{path}: cannot be read: {exc}") from exc


def _write_copies(assets: tuple[AssetPlan, ...]) -> None:
    """Write the rendered copies that ``assets`` say to write."""
    for asset in assets:
        if asset.action == "write" and asset.rendering is not None:
            make_private_dir(asset.path.parent)
            if asset.path.is_symlink():
                asset.path.unlink()
            atomic_write(asset.path, asset.rendering.text, mode=asset.rendering.mode)


def _remove_copies(assets: tuple[AssetPlan, ...], rendered_dir: Path) -> None:
    """Remove the copies whose template is gone, and directories left empty."""
    for asset in assets:
        if asset.action == "remove":
            asset.path.unlink(missing_ok=True)
            _prune(asset.path.parent, rendered_dir)


def _prune(directory: Path, top: Path) -> None:
    """Remove ``directory`` and each parent below ``top`` while it is empty."""
    while directory != top and directory.is_relative_to(top):
        try:
            directory.rmdir()
        except OSError:
            return
        directory = directory.parent


def _plan_link(
    entry: Path, wanted: Path | None, recorded: Mapping[str, str], *, take_over: bool
) -> LinkPlan:
    """Decide what happens to ``entry``, given the links atc has recorded."""
    made = recorded.get(str(entry))
    try:
        return decide_link(
            entry, wanted, None if made is None else Path(made), take_over=take_over
        )
    except OSError as exc:
        raise ConfigError(f"{entry}: cannot be read: {exc}") from exc


def _plan_skills(
    ctx: BuildContext,
    account: Account,
    directory: Path,
    catalog: Catalog,
    recorded: Mapping[str, str],
    *,
    take_over: bool,
) -> list[LinkPlan]:
    """
    Plan a link for each selected skill, and the removal of those no longer wanted.

    Of the other entries in ``directory``, only the links atc recorded are looked
    at. The rest is not atc's.

    Raises:
        ConfigError: ``directory`` is a symbolic link, which atc does not follow,
            or a selected skill is missing or ambiguous.

    """
    if directory.is_symlink():
        message = (
            f"account '{account.name}': {directory} is a symbolic link, and atc "
            "makes skill links only in a directory; replace the link with a "
            "directory"
        )
        raise ConfigError(message)
    wanted = wanted_skill_links(ctx, account, catalog)
    plans = [
        _plan_link(entry, target, recorded, take_over=take_over)
        for entry, target in wanted.items()
    ]
    plans.extend(
        _plan_link(entry, None, recorded, take_over=take_over)
        for entry in map(Path, recorded)
        if entry.parent == directory and entry not in wanted
    )
    return plans


def _apply_links(links: tuple[LinkPlan, ...], state: SyncState, now: datetime) -> None:
    """Change the link entries, and keep the record of the links atc made."""
    recorded = state.links()
    updated = dict(recorded)
    try:
        for link in links:
            if link.keeps_record:
                continue
            owned = apply_link(link, state.backups_dir, now)
            if owned is None:
                _ = updated.pop(str(link.entry), None)
            else:
                updated[str(link.entry)] = str(owned)
    except BaseException as failure:
        # The links made before the failure are recorded all the same, but not
        # being able to record them must not hide what stopped the rest.
        try:
            _record(state, recorded, updated)
        except OSError as unrecorded:
            failure.add_note(
                f"the links made so far could not be recorded: {unrecorded}"
            )
        raise
    _record(state, recorded, updated)


def _record(
    state: SyncState, recorded: Mapping[str, str], updated: Mapping[str, str]
) -> None:
    """Write the record of links when it changed."""
    if updated != recorded:
        state.record_links(updated)


def _parsed(text: str, parse: Callable[[str], JsonObject]) -> JsonObject | None:
    """Parse ``text``, or give ``None`` when ``parse`` refuses it."""
    try:
        return parse(text)
    except UnitError:
        return None
