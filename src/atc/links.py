"""Symbolic links atc makes in an account: deciding, doing, and what is wanted."""

import os
import stat
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from secrets import token_hex
from typing import Literal

from atc.backends import backend_for
from atc.context import BuildContext
from atc.errors import ConfigError, PathError
from atc.fsutil import backup
from atc.refs import resolve_registry
from atc.registry import Account

type LinkAction = Literal[
    "none",
    "create",
    "repoint",
    "remove",
    "replace-link",
    "replace-file",
    "conflict",
    "leave",
]
"""
What happens to a link entry: ``none`` (nothing to do), ``create``, ``repoint``
or ``remove`` a link atc made, ``replace-link`` or ``replace-file`` an entry atc
did not make, ``conflict`` (it did not make it, and a link is wanted), or
``leave`` it be (it did not make it, and no link is wanted).
"""


@dataclass(frozen=True, slots=True)
class LinkPlan:
    """
    What to do with one link entry.

    ``entry`` is where the link is, ``wanted`` the absolute target it should
    have (``None`` when no link is wanted), and ``recorded`` the target of the
    link atc last made there, if any.
    """

    entry: Path
    wanted: Path | None
    recorded: Path | None
    action: LinkAction

    @property
    def pending(self) -> bool:
        """Whether applying the plan changes the entry."""
        return self.action not in {"none", "leave", "conflict"}

    @property
    def blocked(self) -> bool:
        """Whether a link is wanted but something atc did not make is in the way."""
        return self.action == "conflict"

    def describe(self) -> str:
        """Say what changes, as ``NAME: <what is there> → <what will be>``."""
        name = self.entry.name
        match self.action:
            case "create":
                before, after = "(none)", self.wanted
            case "repoint":
                before, after = self.recorded, self.wanted
            case "remove":
                before, after = self.recorded, None
            case "replace-link":
                before, after = "(another link)", self.wanted
            case "replace-file":
                before, after = "(a file, backed up first)", self.wanted
            case "conflict":
                before, after = "(not made by atc)", self.wanted
            case "none" | "leave":
                return f"{name}: unchanged"
        return f"{name}: {_shown(before)} → {_shown(after)}"


def decide_link(
    entry: Path, wanted: Path | None, recorded: Path | None, *, take_over: bool
) -> LinkPlan:
    """
    Decide what to do with ``entry``, changing nothing.

    ``wanted`` is the absolute target the entry should link to, or ``None`` for
    no link, and ``recorded`` the target of the link atc last made there. Only a
    link to ``recorded`` is atc's own; anything else was not made by it, and is
    left alone unless it is in the way of a link that is wanted and
    ``take_over`` allows replacing a link or a file. A directory, or anything
    else that is not a file or a link, is never replaced.

    Raises:
        OSError: the entry cannot be examined, as when a directory above it is a
            file.

    """

    def plan(action: LinkAction) -> LinkPlan:
        return LinkPlan(entry, wanted, recorded, action)

    try:
        mode = entry.lstat().st_mode
    except FileNotFoundError:
        return plan("create" if wanted is not None else "none")

    if stat.S_ISLNK(mode):
        target = entry.readlink()
        if target == wanted:
            return plan("none")
        if recorded is not None and target == recorded:
            return plan("repoint" if wanted is not None else "remove")
        if wanted is None:
            return plan("leave")
        return plan("replace-link" if take_over else "conflict")
    if stat.S_ISREG(mode):
        if wanted is None:
            return plan("leave")
        return plan("replace-file" if take_over else "conflict")
    return plan("conflict" if wanted is not None else "leave")


def apply_link(plan: LinkPlan, backups_dir: Path, now: datetime) -> Path | None:
    """
    Carry out ``plan``, and return the target of the link atc now owns at the entry.

    The result is ``None`` when atc owns none. For ``conflict`` and ``leave``
    nothing changes, so it is what was recorded. Missing parent directories are
    created. A link is made beside the entry under a temporary name and renamed
    over it, so the entry always holds the old link or the new one, never none
    and never one that dangles. A regular file is copied to ``backups_dir``
    first, named for ``now``. A directory is never touched.

    Raises:
        ValueError: a link is to be made, and the target is missing or not
            absolute.
        OSError: a file cannot be backed up, a link cannot be made, or the entry
            is no longer what the plan was made for, in which case it is left as
            it is.

    """
    if plan.pending and _decided_again(plan) != plan.action:
        raise OSError(f"{plan.entry}: changed since it was planned, so it was kept")
    match plan.action:
        case "none":
            return plan.wanted
        case "conflict" | "leave":
            return plan.recorded
        case "remove":
            plan.entry.unlink()
            return None
        case "create" | "repoint" | "replace-link" | "replace-file":
            target = plan.wanted
            if target is None or not target.is_absolute():
                message = f"{plan.entry}: a link needs an absolute target, not {target}"
                raise ValueError(message)
            if plan.action == "create":
                plan.entry.parent.mkdir(parents=True, exist_ok=True)
                plan.entry.symlink_to(target)
                return target
            if plan.action == "replace-file":
                _ = backup(plan.entry, backups_dir, now)
            _replace_with_link(plan.entry, target)
            return target


def wanted_instructions(ctx: BuildContext, account: Account) -> Path | None:
    """
    Find the file that ``account``'s instructions file should link to.

    It is the account's ``agents-md-file``, a path or an asset; else, when
    ``use-default-agents-md`` is set, the asset ``default-agents-md`` names;
    else ``None``, no link.

    Raises:
        ConfigError: the file does not exist, an asset is in several sources, or
            the file is the instructions file itself.

    """
    where = f"{ctx.registry.path}: account '{account.name}'"
    if account.agents_md_file is not None:
        key, wanted = "agents-md-file", account.agents_md_file
    elif account.use_default_agents_md:
        key, wanted = "default-agents-md", ctx.registry.default_agents_md
    else:
        return None
    if isinstance(wanted, Path):
        target = wanted
        if not target.is_file():
            raise ConfigError(f"{where}: {key}: {target} is not a file")
    else:
        try:
            found = resolve_registry(ctx.sources, wanted, "asset")
        except PathError as exc:
            raise PathError(f"{where}: {key}: {exc}") from exc
        if found is None:
            raise PathError(f"{where}: {key}: '{wanted}': no such asset")
        target = found.path
    entry = backend_for(account.agent, ctx.home).instructions_file(account)
    if target == entry:
        raise ConfigError(f"{where}: {key}: {target} is the instructions file itself")
    return target


def _decided_again(plan: LinkPlan) -> LinkAction:
    """Decide ``plan`` anew from what the entry is now."""
    take_over = plan.action in {"replace-link", "replace-file"}
    return decide_link(
        plan.entry, plan.wanted, plan.recorded, take_over=take_over
    ).action


def _replace_with_link(entry: Path, target: Path) -> None:
    """
    Make ``entry`` a link to ``target``, replacing what is there in one step.

    Raises:
        OSError: the link cannot be made, or ``entry`` is a directory.

    """
    temporary = entry.with_name(f".{entry.name}.{token_hex(8)}.tmp")
    temporary.symlink_to(target)
    try:
        os.replace(temporary, entry)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _shown(value: Path | str | None) -> str:
    """Spell a side of ``describe``: a path, a note, or ``(none)``."""
    return "(none)" if value is None else str(value)
