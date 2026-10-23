"""What ``atc sync`` does with settings: the decision, the plan and the write."""

import json
import os
import stat
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

from atc.backends.claude import ClaudeBackend
from atc.context import BuildContext
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
from tests.conftest import ContextBuilder
from tests.helpers import LINKED_REGISTRY, MINIMAL_REGISTRY, registry, skill_files, unit

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


def test_all_parts_are_the_settings_the_instructions_and_the_skills() -> None:
    assert {"settings", "instructions", "skills"} == ALL_PARTS


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
