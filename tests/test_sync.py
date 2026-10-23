"""What ``atc sync`` does with settings: the decision, the plan and the write."""

import os
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from atc.backends.claude import ClaudeBackend
from atc.context import BuildContext
from atc.errors import ConfigError, WriteError
from atc.jsonval import JsonObject, dumps
from atc.state import SyncState
from atc.sync import (
    AccountPlan,
    SettingsPlan,
    apply_account,
    decide_settings,
    plan_account,
)
from tests.conftest import ContextBuilder
from tests.helpers import unit

NEW: JsonObject = {"a": 1}
CLAUDE = ClaudeBackend(Path("/h"))


def parse_live(text: str) -> JsonObject:
    return CLAUDE.parse(text, "live")


def plan(
    live: str | None,
    last: JsonObject | None,
    *,
    take_over: bool = False,
    discard_live: bool = False,
) -> SettingsPlan:
    return decide_settings(
        NEW,
        live,
        last,
        parse=parse_live,
        take_over=take_over,
        discard_live=discard_live,
    )


def test_a_missing_file_is_written() -> None:
    p = plan(None, None)
    assert (p.situation, p.write, p.blocked) == ("missing", True, False)


def test_reformatting_alone_is_current() -> None:
    p = plan('{"a":1}', {"a": 2})
    assert (p.situation, p.write) == ("current", False)


def test_never_synced_and_different_needs_take_over() -> None:
    held = plan('{"a": 2}', None)
    taken = plan('{"a": 2}', None, take_over=True)
    assert (held.blocked, taken.write) == (True, True)


def test_unchanged_since_the_last_sync_is_rewritten() -> None:
    p = plan('{"a": 2}', {"a": 2})
    assert (p.situation, p.write) == ("unchanged", True)


def test_edited_outside_atc_stops_unless_discarded() -> None:
    assert plan('{"a": 3}', {"a": 2}).situation == "edited"
    assert plan('{"a": 3}', {"a": 2}).blocked
    assert plan('{"a": 3}', {"a": 2}, discard_live=True).write


def test_an_unparseable_live_file_counts_as_edited() -> None:
    p = plan("{nope", {"a": 2})
    assert (p.situation, p.blocked) == ("edited", True)


def test_an_unparseable_never_synced_file_needs_take_over() -> None:
    assert plan("{nope", None).situation == "never-synced"


def test_the_flags_change_nothing_where_they_do_not_apply() -> None:
    assert plan(None, None, take_over=True, discard_live=True).situation == "missing"
    assert not plan('{"a": 1}', None, take_over=True, discard_live=True).write
    both = plan('{"a": 2}', {"a": 2}, take_over=True, discard_live=True)
    assert (both.situation, both.write) == ("unchanged", True)


def test_texts_are_rendered_so_a_diff_shows_content_not_formatting() -> None:
    p = plan('{"b":2,"a":3}', {"a": 2})
    assert p.live_text == dumps({"a": 3, "b": 2})
    assert p.last_text == dumps({"a": 2})
    assert p.new_text == dumps(NEW)


def test_an_unparseable_live_file_is_kept_as_it_is() -> None:
    assert plan("{nope", None).live_text == "{nope"


NOW = datetime(2026, 10, 23, 12, 0, 0, tzinfo=UTC)
PERSONAL = "conf/accounts/personal.json"
SETTINGS: JsonObject = {"model": "opus"}


def context(build_context: ContextBuilder, home: Path) -> BuildContext:
    (home / ".claude").mkdir()
    return build_context({PERSONAL: unit(SETTINGS)})


def plan_for(
    ctx: BuildContext, *, take_over: bool = False, discard_live: bool = False
) -> AccountPlan:
    return plan_account(
        ctx,
        ctx.registry.account("personal"),
        take_over=take_over,
        discard_live=discard_live,
        lax=False,
    )


def test_a_missing_account_directory_is_a_config_error(
    build_context: ContextBuilder,
) -> None:
    ctx = build_context({PERSONAL: unit(SETTINGS)})

    with pytest.raises(ConfigError, match=r"account 'personal'.*\.claude"):
        _ = plan_for(ctx)


def test_the_plan_names_the_settings_file(
    build_context: ContextBuilder, home: Path
) -> None:
    ctx = context(build_context, home)

    account_plan = plan_for(ctx)

    assert account_plan.settings_path == home / ".claude" / "settings.json"
    assert account_plan.settings.new == SETTINGS
    assert account_plan.warnings == ()
    assert account_plan.rule_warnings == ()


@pytest.mark.parametrize(
    "record",
    [b"{nope", b'{"a": "\xe2\x82', b'{"model": "\xff"}', b"[1]", b""],
    ids=["invalid-json", "truncated-utf8", "invalid-utf8", "not-an-object", "empty"],
)
def test_a_last_that_cannot_be_read_is_never_trusted(
    build_context: ContextBuilder, home: Path, record: bytes
) -> None:
    ctx = context(build_context, home)
    _ = (home / ".claude" / "settings.json").write_text('{"model": "x"}')
    state = SyncState(ctx.dirs.state_dir, "personal")
    state.root.mkdir(parents=True)
    _ = (state.root / "settings.json").write_bytes(record)

    assert state.last_settings() is None
    assert plan_for(ctx).settings.situation == "never-synced"


def test_a_last_that_is_a_directory_is_never_trusted(
    build_context: ContextBuilder, home: Path
) -> None:
    ctx = context(build_context, home)
    state = SyncState(ctx.dirs.state_dir, "personal")
    (state.root / "settings.json").mkdir(parents=True)

    assert state.last_settings() is None


def test_state_directories_are_private_whatever_the_umask(
    build_context: ContextBuilder, home: Path
) -> None:
    ctx = context(build_context, home)
    _ = (home / ".claude" / "settings.json").write_text('{"model": "x"}')
    previous = os.umask(0o002)
    try:
        apply_account(plan_for(ctx, take_over=True), ctx, NOW)
    finally:
        _ = os.umask(previous)

    state = SyncState(ctx.dirs.state_dir, "personal")
    for directory in (
        ctx.dirs.state_dir,
        ctx.dirs.state_dir / "sync",
        state.root,
        state.backups_dir,
    ):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700, directory


def test_applying_backs_up_writes_and_records(
    build_context: ContextBuilder, home: Path
) -> None:
    ctx = context(build_context, home)
    live = home / ".claude" / "settings.json"
    _ = live.write_text('{"model": "x"}')
    state = SyncState(ctx.dirs.state_dir, "personal")

    apply_account(plan_for(ctx, take_over=True), ctx, NOW)

    backup = state.backups_dir / "20261023T120000Z-settings.json"
    assert live.read_text() == dumps(SETTINGS)
    assert state.last_settings() == SETTINGS
    assert list(state.backups_dir.iterdir()) == [backup]
    assert backup.read_text() == '{"model": "x"}'


def test_applying_a_current_file_only_records(
    build_context: ContextBuilder, home: Path
) -> None:
    ctx = context(build_context, home)
    live = home / ".claude" / "settings.json"
    _ = live.write_text('{"model":"opus"}')
    state = SyncState(ctx.dirs.state_dir, "personal")

    apply_account(plan_for(ctx), ctx, NOW)

    assert live.read_text() == '{"model":"opus"}'
    assert state.last_settings() == SETTINGS
    assert not state.backups_dir.exists()


def test_applying_a_blocked_plan_changes_nothing(
    build_context: ContextBuilder, home: Path
) -> None:
    ctx = context(build_context, home)
    live = home / ".claude" / "settings.json"
    _ = live.write_text('{"model": "x"}')

    apply_account(plan_for(ctx), ctx, NOW)

    assert live.read_text() == '{"model": "x"}'
    assert not ctx.dirs.state_dir.exists()


def test_a_write_that_fails_is_a_config_error_naming_the_account(
    build_context: ContextBuilder, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = context(build_context, home)

    def refuse(path: Path, text: str) -> None:
        raise PermissionError(13, "Permission denied", f"{path}: {len(text)}")

    monkeypatch.setattr("atc.sync.atomic_write", refuse)

    with pytest.raises(ConfigError, match=r"account 'personal'.*Permission denied"):
        apply_account(plan_for(ctx), ctx, NOW)


def test_a_backup_that_fails_leaves_the_live_file_and_the_record_alone(
    build_context: ContextBuilder, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = context(build_context, home)
    live = home / ".claude" / "settings.json"
    _ = live.write_text('{"model": "x"}')

    def refuse(path: Path, backups_dir: Path, now: datetime) -> Path:
        raise PermissionError(13, "Permission denied", f"{path} {backups_dir} {now}")

    monkeypatch.setattr("atc.sync.backup", refuse)

    with pytest.raises(WriteError, match=r"account 'personal'.*Permission denied"):
        apply_account(plan_for(ctx, take_over=True), ctx, NOW)

    assert live.read_text() == '{"model": "x"}'
    assert SyncState(ctx.dirs.state_dir, "personal").last_settings() is None
    assert [p.name for p in home.joinpath(".claude").iterdir()] == ["settings.json"]
