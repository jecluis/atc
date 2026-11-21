"""What ``atc sync`` does with settings: the decision, the plan and the write."""

import json
import os
import shutil
import stat
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

from atc.backends.claude import ClaudeBackend
from atc.context import BuildContext, load_context
from atc.errors import ConfigError, WriteError
from atc.jsonval import JsonObject, dumps
from atc.links import LinkAction, LinkPlan, apply_link
from atc.skills import Catalog
from atc.state import SyncState
from atc.sync import (
    ALL_PARTS,
    AccountPlan,
    Part,
    SettingsPlan,
    apply_account,
    decide_settings,
    plan_account,
)
from tests.conftest import ConfigWriter, ContextBuilder
from tests.helpers import (
    LINKED_REGISTRY,
    MINIMAL_REGISTRY,
    UID,
    registry,
    skill_files,
    unit,
)

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


def merged(live: str | None, last: JsonObject | None) -> SettingsPlan:
    return decide_settings(
        {"model": "opus", "allow": ["a"]},
        live,
        last,
        parse=parse_live,
        take_over=False,
        discard_live=False,
        keep_additions=True,
    )


def test_keeping_additions_writes_the_merge_and_records_the_new_settings() -> None:
    p = merged('{"model": "haiku", "allow": ["a", "b"], "x": 1}', {"model": "opus"})

    assert (p.situation, p.write, p.blocked) == ("merged", True, False)
    assert p.new == {"model": "opus", "allow": ["a"]}
    assert p.new_text == dumps({"model": "opus", "allow": ["a", "b"], "x": 1})
    assert (p.kept, p.reset) == (("/allow", "/x"), ("/model",))


def test_keeping_additions_writes_nothing_when_the_merge_is_the_live_file() -> None:
    p = merged('{"model": "opus", "allow": ["a"], "x": 1}', {"model": "opus"})

    assert (p.situation, p.write) == ("current", False)
    assert p.kept == ("/x",)


def test_keeping_additions_still_stops_on_a_live_file_that_is_not_json() -> None:
    p = merged("{nope", {"model": "opus"})

    assert (p.situation, p.blocked) == ("edited", True)


def test_keeping_additions_still_needs_take_over_for_a_file_never_synced() -> None:
    assert merged('{"x": 1}', None).situation == "never-synced"


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


def settings_plan(account_plan: AccountPlan) -> SettingsPlan:
    """Give the settings plan, which a plan of every part has."""
    assert account_plan.settings is not None
    return account_plan.settings


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
    assert settings_plan(account_plan).new == SETTINGS
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
    assert settings_plan(plan_for(ctx)).situation == "never-synced"


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


def test_links_are_empty_until_recorded(tmp_path: Path) -> None:
    assert SyncState(tmp_path, "personal").links() == {}


def test_recorded_links_are_read_back(tmp_path: Path) -> None:
    state = SyncState(tmp_path, "personal")

    state.record_links({"/a/CLAUDE.md": "/x/AGENTS.md", "/a/skills/s": "/x/s"})

    assert state.links() == {"/a/CLAUDE.md": "/x/AGENTS.md", "/a/skills/s": "/x/s"}
    assert json.loads((state.root / "links.json").read_text()) == state.links()


def test_recording_links_replaces_the_record(tmp_path: Path) -> None:
    state = SyncState(tmp_path, "personal")
    state.record_links({"/a": "/x"})

    state.record_links({})

    assert state.links() == {}


def test_the_links_of_one_account_are_not_another_accounts(tmp_path: Path) -> None:
    SyncState(tmp_path, "personal").record_links({"/a": "/x"})

    assert SyncState(tmp_path, "work").links() == {}


@pytest.mark.parametrize(
    "record",
    [b"{nope", b'{"a": "\xe2\x82', b'{"/a": 1}', b'{"/a": null}', b'["/a"]', b""],
    ids=["invalid-json", "truncated-utf8", "number", "null", "array", "empty"],
)
def test_links_that_cannot_be_read_are_never_trusted(
    tmp_path: Path, record: bytes
) -> None:
    state = SyncState(tmp_path, "personal")
    state.root.mkdir(parents=True)
    _ = (state.root / "links.json").write_bytes(record)

    assert state.links() == {}


def test_links_that_are_a_directory_are_never_trusted(tmp_path: Path) -> None:
    state = SyncState(tmp_path, "personal")
    (state.root / "links.json").mkdir(parents=True)

    assert state.links() == {}


def test_the_link_record_is_private_whatever_the_umask(tmp_path: Path) -> None:
    state = SyncState(tmp_path, "personal")
    previous = os.umask(0o002)
    try:
        state.record_links({"/a": "/x"})
    finally:
        _ = os.umask(previous)

    assert stat.S_IMODE(state.root.stat().st_mode) == 0o700
    assert stat.S_IMODE((state.root / "links.json").stat().st_mode) == 0o600


AGENTS = "assets/AGENTS.md"


def linked_context(
    build_context: ContextBuilder, home: Path, *, with_unit: bool = True
) -> BuildContext:
    """Set up an account that links the default instructions file, which exists."""
    (home / ".claude").mkdir()
    files: dict[str, str | JsonObject] = {AGENTS: "shared rules\n"}
    if with_unit:
        files[PERSONAL] = unit(SETTINGS)
    return build_context(files, LINKED_REGISTRY)


def test_all_parts_are_the_settings_the_links_and_the_rendered_assets() -> None:
    assert {"settings", "instructions", "skills", "assets"} == ALL_PARTS


def test_the_plan_links_the_instructions_file(
    build_context: ContextBuilder, home: Path
) -> None:
    ctx = linked_context(build_context, home)

    account_plan = plan_for(ctx)

    entry = home / ".claude" / "CLAUDE.md"
    asset = ctx.dirs.conf_d / AGENTS
    assert account_plan.links == (LinkPlan(entry, asset, None, "create"),)
    assert account_plan.pending
    assert not account_plan.blocked


def test_an_account_that_wants_no_link_has_nothing_to_do_for_it(
    build_context: ContextBuilder, home: Path
) -> None:
    ctx = context(build_context, home)

    (link,) = plan_for(ctx).links

    assert (link.entry, link.wanted, link.action) == (
        home / ".claude" / "CLAUDE.md",
        None,
        "none",
    )


def test_the_plan_starts_from_the_recorded_link(
    build_context: ContextBuilder, home: Path, tmp_path: Path
) -> None:
    ctx = linked_context(build_context, home)
    old = tmp_path / "old.md"
    _ = old.write_text("old rules\n")
    entry = home / ".claude" / "CLAUDE.md"
    entry.symlink_to(old)
    SyncState(ctx.dirs.state_dir, "personal").record_links({str(entry): str(old)})

    (link,) = plan_for(ctx).links

    assert (link.action, link.recorded) == ("repoint", old)


def test_a_blocked_link_blocks_the_plan(
    build_context: ContextBuilder, home: Path
) -> None:
    ctx = linked_context(build_context, home)
    _ = (home / ".claude" / "CLAUDE.md").write_text("mine\n")

    account_plan = plan_for(ctx)

    assert account_plan.blocked
    assert plan_for(ctx, take_over=True).pending
    assert not plan_for(ctx, take_over=True).blocked


def test_planning_only_the_instructions_builds_no_settings(
    build_context: ContextBuilder, home: Path
) -> None:
    ctx = linked_context(build_context, home, with_unit=False)

    account_plan = plan_account(
        ctx,
        ctx.registry.account("personal"),
        take_over=False,
        discard_live=False,
        lax=False,
        parts=frozenset({"instructions"}),
    )

    assert account_plan.settings is None
    assert account_plan.warnings == ()
    assert [link.action for link in account_plan.links] == ["create"]


def test_planning_only_the_settings_plans_no_link(
    build_context: ContextBuilder, home: Path
) -> None:
    (home / ".claude").mkdir()
    ctx = build_context({PERSONAL: unit(SETTINGS)}, LINKED_REGISTRY)

    account_plan = plan_account(
        ctx,
        ctx.registry.account("personal"),
        take_over=False,
        discard_live=False,
        lax=False,
        parts=frozenset({"settings"}),
    )

    assert account_plan.links == ()
    assert account_plan.settings is not None
    assert account_plan.settings.new == SETTINGS


def test_an_instructions_file_that_is_missing_is_a_config_error(
    build_context: ContextBuilder, home: Path
) -> None:
    (home / ".claude").mkdir()
    ctx = build_context({PERSONAL: unit(SETTINGS)}, LINKED_REGISTRY)

    with pytest.raises(ConfigError, match=r"account 'personal': default-agents-md"):
        _ = plan_for(ctx)


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read any directory")
def test_a_link_entry_that_cannot_be_read_is_a_config_error(
    build_context: ContextBuilder, home: Path
) -> None:
    ctx = linked_context(build_context, home, with_unit=False)
    directory = home / ".claude"
    directory.chmod(0)
    try:
        with pytest.raises(ConfigError, match=r"CLAUDE\.md: cannot be read"):
            _ = plan_account(
                ctx,
                ctx.registry.account("personal"),
                take_over=False,
                discard_live=False,
                lax=False,
                parts=frozenset({"instructions"}),
            )
    finally:
        directory.chmod(0o700)


def test_applying_makes_the_link_and_records_it(
    build_context: ContextBuilder, home: Path
) -> None:
    ctx = linked_context(build_context, home)
    entry = home / ".claude" / "CLAUDE.md"
    asset = ctx.dirs.conf_d / AGENTS
    state = SyncState(ctx.dirs.state_dir, "personal")

    apply_account(plan_for(ctx), ctx, NOW)

    assert entry.readlink() == asset
    assert state.links() == {str(entry): str(asset)}
    assert state.last_settings() == SETTINGS


def test_applying_keeps_the_records_of_entries_it_did_not_plan(
    build_context: ContextBuilder, home: Path
) -> None:
    ctx = linked_context(build_context, home)
    state = SyncState(ctx.dirs.state_dir, "personal")
    state.record_links({"/a/skills/s": "/t/s"})

    apply_account(plan_for(ctx), ctx, NOW)

    entry = home / ".claude" / "CLAUDE.md"
    assert state.links() == {
        "/a/skills/s": "/t/s",
        str(entry): str(ctx.dirs.conf_d / AGENTS),
    }


def test_applying_only_the_instructions_leaves_the_settings_alone(
    build_context: ContextBuilder, home: Path
) -> None:
    ctx = linked_context(build_context, home)
    plan = plan_account(
        ctx,
        ctx.registry.account("personal"),
        take_over=False,
        discard_live=False,
        lax=False,
        parts=frozenset({"instructions"}),
    )

    apply_account(plan, ctx, NOW)

    assert (home / ".claude" / "CLAUDE.md").is_symlink()
    assert not (home / ".claude" / "settings.json").exists()
    assert SyncState(ctx.dirs.state_dir, "personal").last_settings() is None


def test_applying_a_removal_drops_the_record(
    build_context: ContextBuilder, home: Path, tmp_path: Path
) -> None:
    ctx = context(build_context, home)
    old = tmp_path / "old.md"
    _ = old.write_text("old rules\n")
    entry = home / ".claude" / "CLAUDE.md"
    entry.symlink_to(old)
    state = SyncState(ctx.dirs.state_dir, "personal")
    state.record_links({str(entry): str(old), "/a/skills/s": "/t/s"})

    apply_account(plan_for(ctx), ctx, NOW)

    assert not entry.is_symlink()
    assert state.links() == {"/a/skills/s": "/t/s"}


@pytest.mark.parametrize(
    ("registry_text", "action"),
    [(LINKED_REGISTRY, "conflict"), (MINIMAL_REGISTRY, "leave")],
)
def test_a_conflict_and_a_leave_keep_their_records(
    build_context: ContextBuilder, home: Path, registry_text: str, action: LinkAction
) -> None:
    (home / ".claude").mkdir()
    entry = home / ".claude" / "CLAUDE.md"
    _ = entry.write_text("mine\n")
    ctx = build_context({AGENTS: "x\n", PERSONAL: unit(SETTINGS)}, registry_text)
    state = SyncState(ctx.dirs.state_dir, "personal")
    state.record_links({str(entry): "/gone/AGENTS.md"})

    plan = plan_for(ctx)
    apply_account(plan, ctx, NOW)

    assert [link.action for link in plan.links] == [action]
    assert entry.read_text() == "mine\n"
    assert state.links() == {str(entry): "/gone/AGENTS.md"}


def test_nothing_changed_writes_no_link_record(
    build_context: ContextBuilder, home: Path
) -> None:
    ctx = context(build_context, home)

    apply_account(plan_for(ctx), ctx, NOW)

    assert not (SyncState(ctx.dirs.state_dir, "personal").root / "links.json").exists()


def test_a_link_that_fails_is_a_write_error_naming_the_account(
    build_context: ContextBuilder, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = linked_context(build_context, home)

    def refuse(plan: LinkPlan, backups_dir: Path, now: datetime) -> Path | None:
        raise PermissionError(
            13, "Permission denied", f"{plan.entry} {backups_dir} {now}"
        )

    monkeypatch.setattr("atc.sync.apply_link", refuse)

    with pytest.raises(WriteError, match=r"account 'personal'.*Permission denied"):
        apply_account(plan_for(ctx), ctx, NOW)

    assert not (home / ".claude" / "CLAUDE.md").is_symlink()
    assert SyncState(ctx.dirs.state_dir, "personal").links() == {}


SKILLS = skill_files("a", "b", "c")


def skills_context(
    build_context: ContextBuilder, home: Path, *selected: str
) -> BuildContext:
    """Set up an account that selects ``selected`` of the skills a, b and c."""
    (home / ".claude").mkdir()
    return build_context(
        {PERSONAL: unit(SETTINGS), **SKILLS},
        registry(f"skills: [{', '.join(selected)}]"),
    )


def skill_links(account_plan: AccountPlan, home: Path) -> list[LinkPlan]:
    """Give the link plans for entries in the account's skills directory."""
    skills = home / ".claude" / "skills"
    return [link for link in account_plan.links if link.entry.parent == skills]


def test_the_plan_links_each_selected_skill(
    build_context: ContextBuilder, home: Path
) -> None:
    ctx = skills_context(build_context, home, "b", "a")

    account_plan = plan_for(ctx)

    skills, source = home / ".claude" / "skills", ctx.dirs.conf_d / "skills"
    assert skill_links(account_plan, home) == [
        LinkPlan(skills / "b", source / "b", None, "create"),
        LinkPlan(skills / "a", source / "a", None, "create"),
    ]
    assert account_plan.pending
    assert not account_plan.blocked


def test_a_recorded_skill_that_is_no_longer_selected_is_removed(
    build_context: ContextBuilder, home: Path
) -> None:
    ctx = skills_context(build_context, home, "a")
    skills = home / ".claude" / "skills"
    skills.mkdir()
    made = ctx.dirs.conf_d / "skills" / "b"
    (skills / "b").symlink_to(made)
    SyncState(ctx.dirs.state_dir, "personal").record_links(
        {str(skills / "b"): str(made)}
    )

    links = {link.entry.name: link for link in skill_links(plan_for(ctx), home)}

    assert links["a"].action == "create"
    assert links["b"] == LinkPlan(skills / "b", None, made, "remove")


def test_nothing_else_in_the_skills_directory_is_looked_at(
    build_context: ContextBuilder, home: Path, tmp_path: Path
) -> None:
    ctx = skills_context(build_context, home, "a")
    skills = home / ".claude" / "skills"
    for name in ("synced", ".trash", "phase-review"):
        (skills / name).mkdir(parents=True)
    (skills / "c").symlink_to(tmp_path)
    _ = (skills / "notes.md").write_text("mine\n")
    outside, deeper = home / ".claude" / "other", skills / "synced" / "x"
    SyncState(ctx.dirs.state_dir, "personal").record_links(
        {str(outside): "/t/o", str(deeper): "/t/x"}
    )

    account_plan = plan_for(ctx)

    assert [link.entry.name for link in skill_links(account_plan, home)] == ["a"]
    assert {link.entry for link in account_plan.links} == {
        home / ".claude" / "CLAUDE.md",
        skills / "a",
    }


@pytest.mark.parametrize(
    ("parts", "names"),
    [
        (frozenset({"skills"}), ["a"]),
        (frozenset({"settings", "instructions"}), ["CLAUDE.md"]),
        (frozenset({"instructions", "skills"}), ["CLAUDE.md", "a"]),
    ],
)
def test_skill_links_are_planned_only_with_the_skills_part(
    build_context: ContextBuilder, home: Path, parts: frozenset[Part], names: list[str]
) -> None:
    ctx = skills_context(build_context, home, "a")

    account_plan = plan_account(
        ctx,
        ctx.registry.account("personal"),
        take_over=False,
        discard_live=False,
        lax=False,
        parts=parts,
    )

    assert [link.entry.name for link in account_plan.links] == names


def test_a_catalog_that_is_given_is_the_one_used(
    build_context: ContextBuilder, home: Path
) -> None:
    ctx = skills_context(build_context, home, "a")

    with pytest.raises(ConfigError, match=r"skills: 'a': no such skill"):
        _ = plan_account(
            ctx,
            ctx.registry.account("personal"),
            take_over=False,
            discard_live=False,
            lax=False,
            parts=frozenset({"skills"}),
            catalog=Catalog((), ()),
        )


def test_a_skill_entry_that_cannot_be_read_is_a_config_error(
    build_context: ContextBuilder, home: Path
) -> None:
    ctx = skills_context(build_context, home, "a")
    _ = (home / ".claude" / "skills").write_text("not a directory\n")

    with pytest.raises(ConfigError, match=r"skills/a: cannot be read"):
        _ = plan_for(ctx)


def link_b_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``apply_link`` work for every link but the one named ``b``."""

    def refuse_b(plan: LinkPlan, backups_dir: Path, now: datetime) -> Path | None:
        if plan.entry.name == "b":
            raise PermissionError(13, "Permission denied", str(plan.entry))
        return apply_link(plan, backups_dir, now)

    monkeypatch.setattr("atc.sync.apply_link", refuse_b)


def test_links_made_before_a_failure_are_still_recorded(
    build_context: ContextBuilder, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = skills_context(build_context, home, "a", "b")
    skills, source = home / ".claude" / "skills", ctx.dirs.conf_d / "skills"
    link_b_fails(monkeypatch)

    with pytest.raises(WriteError, match=r"account 'personal'.*Permission denied"):
        apply_account(plan_for(ctx), ctx, NOW)

    assert (skills / "a").is_symlink()
    assert not (skills / "b").is_symlink()
    state = SyncState(ctx.dirs.state_dir, "personal")
    assert state.links() == {str(skills / "a"): str(source / "a")}


def test_a_record_that_cannot_be_written_does_not_hide_the_failure_before_it(
    build_context: ContextBuilder, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = skills_context(build_context, home, "a", "b")
    link_b_fails(monkeypatch)

    def disk_full(_state: SyncState, _links: Mapping[str, str]) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(SyncState, "record_links", disk_full)

    with pytest.raises(WriteError, match=r"Permission denied") as raised:
        apply_account(plan_for(ctx), ctx, NOW)

    cause = raised.value.__cause__
    assert cause is not None
    assert any("No space left on device" in note for note in cause.__notes__)


TEMPLATE = "assets/bin/clean.in.sh"


def templated_context(
    build_context: ContextBuilder, home: Path, text: str = "ROOT={{account.name}}\n"
) -> BuildContext:
    """Set up an account, and a source with one template."""
    (home / ".claude").mkdir()
    return build_context({PERSONAL: unit(SETTINGS), TEMPLATE: text})


def copy_of(ctx: BuildContext) -> Path:
    """Where the template of :func:`templated_context` is rendered."""
    return SyncState(ctx.dirs.state_dir, "personal").rendered_dir / "bin/clean.sh"


def test_the_plan_renders_each_template(
    build_context: ContextBuilder, home: Path
) -> None:
    ctx = templated_context(build_context, home)

    account_plan = plan_for(ctx)

    (asset,) = account_plan.assets
    assert (asset.path, asset.action) == (copy_of(ctx), "write")
    assert (asset.live_text, asset.live_mode) == (None, None)
    assert asset.rendering is not None
    assert asset.rendering.text == "ROOT=personal\n"
    assert account_plan.pending


def test_applying_writes_a_private_copy_that_is_then_current(
    build_context: ContextBuilder, home: Path
) -> None:
    ctx = templated_context(build_context, home)

    apply_account(plan_for(ctx), ctx, NOW)

    copy = copy_of(ctx)
    assert copy.read_text() == "ROOT=personal\n"
    assert stat.S_IMODE(copy.stat().st_mode) == 0o600
    assert stat.S_IMODE(copy.parent.stat().st_mode) == 0o700
    (asset,) = plan_for(ctx).assets
    assert asset.action == "none"
    assert not plan_for(ctx).pending


def test_an_executable_template_gives_a_copy_only_its_owner_can_run(
    build_context: ContextBuilder, home: Path
) -> None:
    ctx = templated_context(build_context, home)
    (ctx.dirs.conf_d / TEMPLATE).chmod(0o755)

    apply_account(plan_for(ctx), ctx, NOW)

    assert stat.S_IMODE(copy_of(ctx).stat().st_mode) == 0o700


def test_a_copy_that_differs_in_text_or_in_mode_is_written_again(
    build_context: ContextBuilder, home: Path
) -> None:
    ctx = templated_context(build_context, home)
    apply_account(plan_for(ctx), ctx, NOW)
    copy = copy_of(ctx)
    _ = copy.write_text("edited\n")

    (edited,) = plan_for(ctx).assets
    apply_account(plan_for(ctx), ctx, NOW)
    copy.chmod(0o644)
    (opened,) = plan_for(ctx).assets

    assert (edited.action, edited.live_text) == ("write", "edited\n")
    assert copy.read_text() == "ROOT=personal\n"
    assert (opened.action, opened.live_mode) == ("write", 0o644)


def test_a_copy_whose_template_is_gone_is_removed(
    build_context: ContextBuilder, home: Path
) -> None:
    ctx = templated_context(build_context, home)
    apply_account(plan_for(ctx), ctx, NOW)
    (ctx.dirs.conf_d / TEMPLATE).unlink()

    (asset,) = plan_for(ctx).assets
    apply_account(plan_for(ctx), ctx, NOW)

    assert (asset.path, asset.rendering, asset.action) == (copy_of(ctx), None, "remove")
    assert asset.live_text == "ROOT=personal\n"
    assert not copy_of(ctx).parent.exists()
    assert SyncState(ctx.dirs.state_dir, "personal").rendered_dir.is_dir()


@pytest.mark.parametrize(
    ("parts", "planned"),
    [({"skills"}, False), ({"assets"}, True), ({"instructions"}, True)],
)
def test_templates_are_rendered_with_the_assets_or_the_instructions(
    build_context: ContextBuilder, home: Path, parts: set[Part], planned: bool
) -> None:
    ctx = templated_context(build_context, home)

    account_plan = plan_account(
        ctx,
        ctx.registry.account("personal"),
        take_over=False,
        discard_live=False,
        lax=False,
        parts=frozenset(parts),
    )

    assert bool(account_plan.assets) == planned


def test_a_template_that_cannot_be_rendered_is_a_config_error(
    build_context: ContextBuilder, home: Path
) -> None:
    ctx = templated_context(build_context, home, "{{nope}}\n")

    with pytest.raises(ConfigError, match=r"unknown reference \{\{nope\}\}"):
        _ = plan_for(ctx)


def test_the_default_instructions_template_is_linked_as_its_copy(
    build_context: ContextBuilder, home: Path
) -> None:
    (home / ".claude").mkdir()
    ctx = build_context(
        {PERSONAL: unit(SETTINGS), "assets/AGENTS.in.md": "for {{account.name}}\n"},
        registry("use-default-agents-md: true"),
    )
    entry = home / ".claude" / "CLAUDE.md"

    apply_account(plan_for(ctx), ctx, NOW)

    rendered = SyncState(ctx.dirs.state_dir, "personal").rendered_dir
    assert entry.readlink() == rendered / "AGENTS.md"
    assert entry.read_text() == "for personal\n"


def test_a_link_where_a_copy_goes_is_replaced_not_written_through(
    build_context: ContextBuilder, home: Path, tmp_path: Path
) -> None:
    ctx = templated_context(build_context, home)
    elsewhere = tmp_path / "elsewhere.sh"
    _ = elsewhere.write_text("untouched\n")
    copy = copy_of(ctx)
    copy.parent.mkdir(parents=True)
    copy.symlink_to(elsewhere)

    apply_account(plan_for(ctx), ctx, NOW)

    assert not copy.is_symlink()
    assert copy.read_text() == "ROOT=personal\n"
    assert elsewhere.read_text() == "untouched\n"


@pytest.mark.parametrize("linked", ["", "bin"])
def test_a_directory_of_copies_that_is_a_link_is_a_config_error(
    build_context: ContextBuilder, home: Path, tmp_path: Path, linked: str
) -> None:
    ctx = templated_context(build_context, home)
    directory = SyncState(ctx.dirs.state_dir, "personal").rendered_dir / linked
    directory.parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "elsewhere").mkdir()
    directory.symlink_to(tmp_path / "elsewhere")

    with pytest.raises(ConfigError, match="is a symbolic link, and atc writes"):
        _ = plan_for(ctx)


def test_the_old_copy_of_a_renamed_instructions_template_outlives_a_failure(
    build_context: ContextBuilder, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (home / ".claude").mkdir()
    files: dict[str, str | JsonObject] = {
        PERSONAL: unit(SETTINGS),
        "assets/AGENTS.in.md": "old\n",
    }
    ctx = build_context(
        files,
        registry(
            "use-default-agents-md: true", top="default-agents-md: AGENTS.in.md\n"
        ),
    )
    apply_account(plan_for(ctx), ctx, NOW)
    (ctx.dirs.conf_d / "assets" / "AGENTS.in.md").unlink()
    ctx = build_context(
        {"assets/RULES.in.md": "new\n"},
        registry("use-default-agents-md: true", top="default-agents-md: RULES.in.md\n"),
    )
    entry = home / ".claude" / "CLAUDE.md"
    renamed = plan_for(ctx)

    def refuse(*_: object) -> None:
        raise PermissionError(13, "Permission denied")

    with monkeypatch.context() as patched:
        patched.setattr("atc.sync._apply_links", refuse)
        with pytest.raises(ConfigError, match="Permission denied"):
            apply_account(renamed, ctx, NOW)
    kept = entry.read_text()
    apply_account(renamed, ctx, NOW)

    rendered = SyncState(ctx.dirs.state_dir, "personal").rendered_dir
    assert kept == "old\n"
    assert entry.readlink() == rendered / "RULES.md"
    assert entry.read_text() == "new\n"
    assert not (rendered / "AGENTS.md").exists()


def test_the_copies_of_a_source_that_is_gone_are_removed(
    build_context: ContextBuilder, home: Path
) -> None:
    (home / ".claude").mkdir()
    two = (
        "version: 1\ndefault-account: a\n"
        "accounts:\n  a: {agent: claude, dir: ~/.claude}\n"
    )
    ctx = build_context(
        {
            "main/conf/accounts/a.json": unit(SETTINGS),
            "main/assets/x.in": "x\n",
            "other/assets/y.in": "y\n",
        },
        two,
    )
    account = ctx.registry.account("a")
    rendered = SyncState(ctx.dirs.state_dir, "a").rendered_dir
    apply_account(
        plan_account(ctx, account, take_over=False, discard_live=False, lax=False),
        ctx,
        NOW,
    )
    shutil.rmtree(ctx.dirs.conf_d / "other")
    ctx = load_context(env=os.environ, home=home, uid=UID, conf_d=None)

    gone = plan_account(ctx, account, take_over=False, discard_live=False, lax=False)
    apply_account(gone, ctx, NOW)

    assert [(a.path, a.action) for a in gone.assets] == [
        (rendered / "main" / "x", "none"),
        (rendered / "other" / "y", "remove"),
    ]
    assert not (rendered / "other").exists()
    assert (rendered / "main" / "x").read_text() == "x\n"


OWN_DIR_REGISTRY = """\
    version: 1
    default-account: personal
    accounts:
      personal: {agent: claude, dir: ~/.claude, use-default-agents-md: true}
    projects:
      bar: {repo: /r/bar, own-config-dir: true}
"""
OWN_DIR_FILES = {
    PERSONAL: unit({"model": "opus", "permissions": {"deny": ["Bash(rm *)"]}}),
    "conf/projects/bar.json": unit(
        {"sandbox": {"network": {"allowedDomains": ["{{project.name}}.org"]}}}
    ),
    "assets/AGENTS.in.md": "hi {{account.name}}\n",
}


def project_context(build_context: ContextBuilder, home: Path) -> BuildContext:
    (home / ".claude").mkdir()
    ctx = build_context(OWN_DIR_FILES, OWN_DIR_REGISTRY)
    own_dir(ctx).mkdir(parents=True)
    return ctx


def own_dir(ctx: BuildContext) -> Path:
    return ctx.dirs.state_dir / "projects" / "bar" / "personal"


def memory(ctx: BuildContext) -> str:
    return str(ctx.dirs.data_dir / "projects" / "bar" / "memory")


def sync_project(ctx: BuildContext, *, discard_live: bool = False) -> AccountPlan:
    account_plan = plan_account(
        ctx,
        ctx.registry.account("personal"),
        project=ctx.registry.project("bar"),
        take_over=False,
        discard_live=discard_live,
        lax=False,
    )
    apply_account(account_plan, ctx, NOW)
    return account_plan


def test_a_project_dir_gets_the_account_settings_with_the_project_ones_over_them(
    build_context: ContextBuilder, home: Path
) -> None:
    ctx = project_context(build_context, home)
    directory = own_dir(ctx)

    account_plan = sync_project(ctx)

    assert account_plan.label == "personal/bar"
    assert account_plan.settings_path == directory / "settings.json"
    assert json.loads((directory / "settings.json").read_text()) == {
        "autoMemoryDirectory": memory(ctx),
        "model": "opus",
        "permissions": {"deny": ["Bash(rm *)"]},
        "sandbox": {"network": {"allowedDomains": ["bar.org"]}},
    }
    rendered = SyncState(ctx.dirs.state_dir, "personal").rendered_dir / "AGENTS.md"
    assert (directory / "CLAUDE.md").readlink() == rendered
    assert not (home / ".claude" / "settings.json").exists()
    record = ctx.dirs.state_dir / "sync" / "personal" / "projects" / "bar"
    assert json.loads((record / "settings.json").read_text())["model"] == "opus"


def test_a_project_dir_shares_its_memory_and_plugins_with_the_projects_accounts(
    build_context: ContextBuilder, home: Path
) -> None:
    ctx = project_context(build_context, home)

    _ = sync_project(ctx)

    common = ctx.dirs.state_dir / "projects" / "bar" / "_common"
    assert (own_dir(ctx) / "plugins").readlink() == common / "plugins"
    assert (common / "plugins").is_dir()
    assert Path(memory(ctx)).is_dir()
    assert (Path(memory(ctx)).stat().st_mode & 0o777) == 0o700


def test_a_project_unit_can_point_auto_memory_elsewhere(
    build_context: ContextBuilder, home: Path, configure: ConfigWriter
) -> None:
    ctx = project_context(build_context, home)
    files = {
        **OWN_DIR_FILES,
        "conf/projects/bar.json": unit({"autoMemoryDirectory": "/m"}),
    }
    configure(files, OWN_DIR_REGISTRY)
    ctx = load_context(env=os.environ, home=home, uid=UID, conf_d=None)

    _ = sync_project(ctx)

    assert '"autoMemoryDirectory": "/m"' in (own_dir(ctx) / "settings.json").read_text()


def test_a_project_dir_keeps_what_claude_added_and_takes_the_units_changes(
    build_context: ContextBuilder, home: Path, configure: ConfigWriter
) -> None:
    ctx = project_context(build_context, home)
    path = own_dir(ctx) / "settings.json"
    _ = sync_project(ctx)
    edited: JsonObject = {
        "advisorModel": "fable",
        "autoMemoryDirectory": memory(ctx),
        "model": "haiku",
        "permissions": {"deny": []},
        "sandbox": {"network": {"allowedDomains": ["bar.org"]}},
    }
    _ = path.write_text(dumps(edited))
    files = {**OWN_DIR_FILES, PERSONAL: unit({"model": "sonnet"})}
    configure(files, OWN_DIR_REGISTRY)
    ctx = load_context(env=os.environ, home=home, uid=UID, conf_d=None)

    account_plan = sync_project(ctx)

    assert json.loads(path.read_text()) == {
        "autoMemoryDirectory": memory(ctx),
        "model": "sonnet",
        "advisorModel": "fable",
        "sandbox": {"network": {"allowedDomains": ["bar.org"]}},
    }
    plan_of = settings_plan(account_plan)
    assert (plan_of.situation, plan_of.kept, plan_of.reset) == (
        "merged",
        ("/advisorModel",),
        ("/model", "/permissions"),
    )


def test_a_project_dir_restores_a_deny_rule_a_session_removed(
    build_context: ContextBuilder, home: Path
) -> None:
    ctx = project_context(build_context, home)
    path = own_dir(ctx) / "settings.json"
    _ = sync_project(ctx)
    edited: JsonObject = {
        "autoMemoryDirectory": memory(ctx),
        "model": "opus",
        "permissions": {"deny": ["Bash(git *)"]},
        "sandbox": {"network": {"allowedDomains": ["bar.org"]}},
    }
    _ = path.write_text(dumps(edited))

    _ = sync_project(ctx)

    assert json.loads(path.read_text())["permissions"]["deny"] == [
        "Bash(rm *)",
        "Bash(git *)",
    ]


def test_a_missing_project_dir_is_a_config_error(
    build_context: ContextBuilder, home: Path
) -> None:
    (home / ".claude").mkdir()
    ctx = build_context(OWN_DIR_FILES, OWN_DIR_REGISTRY)

    with pytest.raises(ConfigError, match=r"personal/bar.*projects/bar/personal"):
        _ = sync_project(ctx)


@pytest.mark.parametrize(
    "linked", ["projects", "projects/bar", "projects/bar/personal"]
)
def test_a_project_dir_reached_through_a_symbolic_link_is_refused(
    build_context: ContextBuilder, home: Path, tmp_path: Path, linked: str
) -> None:
    (home / ".claude").mkdir()
    ctx = build_context(OWN_DIR_FILES, OWN_DIR_REGISTRY)
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "bar" / "personal").mkdir(parents=True)
    link = ctx.dirs.state_dir / linked
    link.parent.mkdir(parents=True, exist_ok=True)
    depth = linked.count("/")
    link.symlink_to(
        [elsewhere, elsewhere / "bar", elsewhere / "bar" / "personal"][depth]
    )

    with pytest.raises(ConfigError, match=r"personal/bar: .* is a symbolic link"):
        _ = sync_project(ctx)

    assert not (elsewhere / "bar" / "personal" / "settings.json").exists()
