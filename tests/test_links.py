"""Links: what to do with an entry, how it is changed, and what an account wants."""

import os
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from atc.errors import ConfigError
from atc.links import (
    LinkAction,
    LinkPlan,
    apply_link,
    decide_link,
    wanted_instructions,
)
from tests.conftest import ContextBuilder
from tests.helpers import MINIMAL_REGISTRY, registry

NOW = datetime(2026, 10, 23, 12, 0, 0, tzinfo=UTC)
WANTED = Path("/atc/assets/AGENTS.md")
RECORDED = Path("/atc/old/AGENTS.md")
STRANGER = Path("/elsewhere/CLAUDE.md")


@pytest.fixture
def entry(tmp_path: Path) -> Path:
    """Give an entry in a directory that exists."""
    directory = tmp_path / "account"
    directory.mkdir()
    return directory / "CLAUDE.md"


@pytest.fixture
def target(tmp_path: Path) -> Path:
    """Give a file that a link can point to."""
    path = tmp_path / "assets" / "AGENTS.md"
    path.parent.mkdir()
    _ = path.write_text("shared rules\n", encoding="utf-8")
    return path


@pytest.fixture
def backups(tmp_path: Path) -> Path:
    return tmp_path / "state" / "backups"


def decide(
    entry: Path,
    *,
    wanted: Path | None = WANTED,
    recorded: Path | None = None,
    take_over: bool = False,
) -> LinkAction:
    return decide_link(entry, wanted, recorded, take_over=take_over).action


def test_a_missing_entry_is_created_when_a_link_is_wanted(entry: Path) -> None:
    assert decide(entry) == "create"


def test_a_missing_entry_needs_nothing_when_no_link_is_wanted(entry: Path) -> None:
    assert decide(entry, wanted=None) == "none"
    assert decide(entry, wanted=None, recorded=RECORDED) == "none"


def test_a_link_to_the_wanted_target_is_current(entry: Path) -> None:
    entry.symlink_to(WANTED)

    assert decide(entry) == "none"
    assert decide(entry, recorded=WANTED) == "none"


def test_the_wanted_target_wins_over_the_recorded_one(entry: Path) -> None:
    entry.symlink_to(WANTED)

    assert decide(entry, recorded=RECORDED) == "none"


def test_a_link_to_the_recorded_target_is_repointed(entry: Path) -> None:
    entry.symlink_to(RECORDED)

    plan = decide_link(entry, WANTED, RECORDED, take_over=False)

    assert plan == LinkPlan(entry, WANTED, RECORDED, "repoint")


def test_a_link_to_the_recorded_target_is_removed_when_none_is_wanted(
    entry: Path,
) -> None:
    entry.symlink_to(RECORDED)

    assert decide(entry, wanted=None, recorded=RECORDED) == "remove"


def test_a_repoint_needs_no_take_over(entry: Path) -> None:
    entry.symlink_to(RECORDED)

    assert decide(entry, recorded=RECORDED, take_over=True) == "repoint"


def test_another_link_is_a_conflict(entry: Path, target: Path) -> None:
    entry.symlink_to(target)

    assert decide(entry, recorded=RECORDED) == "conflict"
    assert decide(entry) == "conflict"


def test_take_over_replaces_another_link(entry: Path, target: Path) -> None:
    entry.symlink_to(target)

    assert decide(entry, recorded=RECORDED, take_over=True) == "replace-link"


def test_a_dangling_link_counts_as_another_link(entry: Path) -> None:
    entry.symlink_to(STRANGER)

    assert decide(entry) == "conflict"
    assert decide(entry, take_over=True) == "replace-link"
    assert decide(entry, wanted=None) == "leave"


def test_another_link_is_left_when_none_is_wanted(entry: Path, target: Path) -> None:
    entry.symlink_to(target)

    assert decide(entry, wanted=None, take_over=True) == "leave"
    assert decide(entry, wanted=None, recorded=RECORDED) == "leave"


def test_a_regular_file_is_a_conflict(entry: Path) -> None:
    _ = entry.write_text("mine\n", encoding="utf-8")

    assert decide(entry) == "conflict"


def test_take_over_replaces_a_regular_file(entry: Path) -> None:
    _ = entry.write_text("mine\n", encoding="utf-8")

    assert decide(entry, take_over=True) == "replace-file"


def test_a_regular_file_is_left_when_none_is_wanted(entry: Path) -> None:
    _ = entry.write_text("mine\n", encoding="utf-8")

    assert decide(entry, wanted=None) == "leave"
    assert decide(entry, wanted=None, recorded=RECORDED, take_over=True) == "leave"


def test_a_directory_is_a_conflict_even_with_take_over(entry: Path) -> None:
    entry.mkdir()

    assert decide(entry) == "conflict"
    assert decide(entry, take_over=True) == "conflict"
    assert decide(entry, recorded=RECORDED, take_over=True) == "conflict"


def test_a_directory_is_left_when_none_is_wanted(entry: Path) -> None:
    entry.mkdir()

    assert decide(entry, wanted=None, take_over=True) == "leave"


def test_a_link_to_a_directory_is_a_link_not_a_directory(
    entry: Path, tmp_path: Path
) -> None:
    entry.symlink_to(tmp_path)

    assert decide(entry, take_over=True) == "replace-link"


def test_something_that_is_not_a_file_is_treated_like_a_directory(
    entry: Path,
) -> None:
    os.mkfifo(entry)

    assert decide(entry, take_over=True) == "conflict"
    assert decide(entry, wanted=None) == "leave"


def test_an_entry_under_a_file_cannot_be_read(tmp_path: Path) -> None:
    blocker = tmp_path / "skills"
    _ = blocker.write_text("not a directory", encoding="utf-8")

    with pytest.raises(NotADirectoryError):
        _ = decide(blocker / "x")


def test_deciding_writes_nothing(entry: Path, target: Path) -> None:
    entry.symlink_to(target)
    before = tuple(entry.parent.iterdir())

    _ = decide(entry, take_over=True)
    _ = decide(entry, wanted=None, recorded=target)

    assert tuple(entry.parent.iterdir()) == before
    assert entry.readlink() == target


@pytest.mark.parametrize(
    ("action", "pending", "blocked", "keeps_record", "owned"),
    [
        ("none", False, False, False, True),
        ("create", True, False, False, False),
        ("repoint", True, False, False, True),
        ("remove", True, False, False, True),
        ("replace-link", True, False, False, False),
        ("replace-file", True, False, False, False),
        ("conflict", False, True, True, False),
        ("leave", False, False, True, False),
    ],
)
def test_what_a_plan_says_about_its_entry(
    entry: Path,
    action: LinkAction,
    *,
    pending: bool,
    blocked: bool,
    keeps_record: bool,
    owned: bool,
) -> None:
    plan = LinkPlan(entry, WANTED, RECORDED, action)

    assert (plan.pending, plan.blocked, plan.keeps_record, plan.owned) == (
        pending,
        blocked,
        keeps_record,
        owned,
    )


@pytest.mark.parametrize(
    ("action", "text"),
    [
        ("create", "CLAUDE.md: (none) → /atc/assets/AGENTS.md"),
        ("repoint", "CLAUDE.md: /atc/old/AGENTS.md → /atc/assets/AGENTS.md"),
        ("remove", "CLAUDE.md: /atc/old/AGENTS.md → (none)"),
        ("replace-link", "CLAUDE.md: (another link) → /atc/assets/AGENTS.md"),
        (
            "replace-file",
            "CLAUDE.md: (a file, backed up first) → /atc/assets/AGENTS.md",
        ),
        ("conflict", "CLAUDE.md: (not made by atc) → /atc/assets/AGENTS.md"),
        ("none", "CLAUDE.md: unchanged"),
        ("leave", "CLAUDE.md: unchanged"),
    ],
)
def test_describe_says_what_changes(entry: Path, action: LinkAction, text: str) -> None:
    wanted = None if action == "remove" else WANTED

    assert LinkPlan(entry, wanted, RECORDED, action).describe() == text


def test_create_makes_a_link_and_returns_its_target(
    entry: Path, target: Path, backups: Path
) -> None:
    plan = decide_link(entry, target, None, take_over=False)

    owned = apply_link(plan, backups, NOW)

    assert owned == target
    assert entry.is_symlink()
    assert entry.readlink() == target
    assert entry.read_text(encoding="utf-8") == "shared rules\n"
    assert not backups.exists()


def test_links_are_absolute(entry: Path, target: Path, backups: Path) -> None:
    _ = apply_link(decide_link(entry, target, None, take_over=False), backups, NOW)

    assert Path(os.readlink(entry)).is_absolute()


def test_a_relative_target_is_refused_before_anything_changes(
    entry: Path, backups: Path
) -> None:
    plan = LinkPlan(entry, Path("assets/AGENTS.md"), None, "create")

    with pytest.raises(ValueError, match="absolute target"):
        _ = apply_link(plan, backups, NOW)

    assert not entry.is_symlink()


def test_a_missing_parent_directory_is_created(
    tmp_path: Path, target: Path, backups: Path
) -> None:
    nested = tmp_path / "account" / "skills" / "git-commits"

    owned = apply_link(decide_link(nested, target, None, take_over=False), backups, NOW)

    assert owned == target
    assert nested.readlink() == target


def test_a_current_link_is_left_as_it_is_and_still_owned(
    entry: Path, target: Path, backups: Path
) -> None:
    entry.symlink_to(target)
    before = entry.lstat().st_ino

    owned = apply_link(decide_link(entry, target, None, take_over=False), backups, NOW)

    assert owned == target
    assert entry.lstat().st_ino == before


def test_nothing_wanted_and_nothing_there_owns_nothing(
    entry: Path, backups: Path
) -> None:
    plan = decide_link(entry, None, RECORDED, take_over=False)

    assert apply_link(plan, backups, NOW) is None
    assert not entry.exists()
    assert not entry.is_symlink()


def test_repoint_replaces_the_link_and_leaves_the_old_target(
    entry: Path, target: Path, tmp_path: Path, backups: Path
) -> None:
    old = tmp_path / "old.md"
    _ = old.write_text("old rules\n", encoding="utf-8")
    entry.symlink_to(old)

    owned = apply_link(decide_link(entry, target, old, take_over=False), backups, NOW)

    assert owned == target
    assert entry.readlink() == target
    assert old.read_text(encoding="utf-8") == "old rules\n"
    assert [p.name for p in entry.parent.iterdir()] == ["CLAUDE.md"]


def test_remove_deletes_the_link_and_leaves_its_target(
    entry: Path, target: Path, backups: Path
) -> None:
    entry.symlink_to(target)

    owned = apply_link(decide_link(entry, None, target, take_over=False), backups, NOW)

    assert owned is None
    assert not entry.is_symlink()
    assert target.read_text(encoding="utf-8") == "shared rules\n"


def test_remove_deletes_a_dangling_link(entry: Path, backups: Path) -> None:
    entry.symlink_to(RECORDED)

    owned = apply_link(
        decide_link(entry, None, RECORDED, take_over=False), backups, NOW
    )

    assert owned is None
    assert not entry.is_symlink()


def test_take_over_replaces_another_link_and_leaves_its_target(
    entry: Path, target: Path, tmp_path: Path, backups: Path
) -> None:
    other = tmp_path / "other.md"
    _ = other.write_text("other rules\n", encoding="utf-8")
    entry.symlink_to(other)

    owned = apply_link(decide_link(entry, target, None, take_over=True), backups, NOW)

    assert owned == target
    assert entry.readlink() == target
    assert other.read_text(encoding="utf-8") == "other rules\n"
    assert not backups.exists()


def test_take_over_backs_up_a_regular_file_before_linking(
    entry: Path, target: Path, backups: Path
) -> None:
    _ = entry.write_text("hand-written rules\n", encoding="utf-8")

    owned = apply_link(decide_link(entry, target, None, take_over=True), backups, NOW)

    assert owned == target
    assert entry.is_symlink()
    assert entry.readlink() == target
    (saved,) = backups.iterdir()
    assert saved.name == "20261023T120000Z-CLAUDE.md"
    assert saved.read_text(encoding="utf-8") == "hand-written rules\n"
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600


def test_a_backup_that_fails_leaves_the_file_alone(
    entry: Path, target: Path, backups: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = entry.write_text("hand-written rules\n", encoding="utf-8")

    def refuse(path: Path, backups_dir: Path, now: datetime) -> Path:
        raise PermissionError(13, "Permission denied", f"{path} {backups_dir} {now}")

    monkeypatch.setattr("atc.links.backup", refuse)

    with pytest.raises(PermissionError):
        _ = apply_link(decide_link(entry, target, None, take_over=True), backups, NOW)

    assert not entry.is_symlink()
    assert entry.read_text(encoding="utf-8") == "hand-written rules\n"
    assert [p.name for p in entry.parent.iterdir()] == ["CLAUDE.md"]


def test_a_replacement_that_fails_keeps_the_old_link_and_leaves_no_litter(
    entry: Path,
    target: Path,
    tmp_path: Path,
    backups: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = tmp_path / "old.md"
    _ = old.write_text("old rules\n", encoding="utf-8")
    entry.symlink_to(old)
    plan = decide_link(entry, target, old, take_over=False)

    def refuse(source: Path, destination: Path) -> None:
        raise PermissionError(13, "Permission denied", f"{source} {destination}")

    monkeypatch.setattr("atc.links.os.replace", refuse)

    with pytest.raises(PermissionError):
        _ = apply_link(plan, backups, NOW)

    assert entry.readlink() == old
    assert [p.name for p in entry.parent.iterdir()] == ["CLAUDE.md"]


def test_a_conflict_changes_nothing_and_keeps_the_record(
    entry: Path, target: Path, backups: Path
) -> None:
    _ = entry.write_text("hand-written rules\n", encoding="utf-8")
    plan = decide_link(entry, target, RECORDED, take_over=False)

    owned = apply_link(plan, backups, NOW)

    assert owned == RECORDED
    assert not entry.is_symlink()
    assert entry.read_text(encoding="utf-8") == "hand-written rules\n"
    assert not backups.exists()


def test_leave_changes_nothing_and_keeps_the_record(
    entry: Path, target: Path, backups: Path
) -> None:
    entry.symlink_to(target)
    plan = decide_link(entry, None, RECORDED, take_over=True)

    owned = apply_link(plan, backups, NOW)

    assert plan.action == "leave"
    assert owned == RECORDED
    assert entry.readlink() == target


def test_a_directory_is_never_replaced_even_with_take_over(
    entry: Path, target: Path, backups: Path
) -> None:
    entry.mkdir()
    _ = (entry / "keep.txt").write_text("keep\n", encoding="utf-8")
    plan = decide_link(entry, target, None, take_over=True)

    owned = apply_link(plan, backups, NOW)

    assert plan.action == "conflict"
    assert owned is None
    assert entry.is_dir()
    assert not entry.is_symlink()
    assert (entry / "keep.txt").read_text(encoding="utf-8") == "keep\n"
    assert not backups.exists()


@pytest.mark.parametrize("action", ["replace-file", "replace-link", "repoint"])
def test_a_plan_that_went_stale_cannot_replace_a_directory(
    entry: Path, target: Path, backups: Path, action: LinkAction
) -> None:
    entry.mkdir()
    _ = (entry / "keep.txt").write_text("keep\n", encoding="utf-8")

    with pytest.raises(OSError, match="changed since it was planned"):
        _ = apply_link(LinkPlan(entry, target, RECORDED, action), backups, NOW)

    assert (entry / "keep.txt").read_text(encoding="utf-8") == "keep\n"
    assert [p.name for p in entry.parent.iterdir()] == ["CLAUDE.md"]
    assert not backups.exists()


@pytest.mark.parametrize("action", ["repoint", "remove", "create"])
def test_a_plan_that_went_stale_cannot_replace_a_file_without_a_backup(
    entry: Path, target: Path, backups: Path, action: LinkAction
) -> None:
    _ = entry.write_text("hand-written rules\n", encoding="utf-8")
    wanted = None if action == "remove" else target

    with pytest.raises(OSError, match="changed since it was planned"):
        _ = apply_link(LinkPlan(entry, wanted, RECORDED, action), backups, NOW)

    assert entry.read_text(encoding="utf-8") == "hand-written rules\n"
    assert not entry.is_symlink()
    assert [p.name for p in entry.parent.iterdir()] == ["CLAUDE.md"]
    assert not backups.exists()


def test_a_plan_that_went_stale_cannot_replace_a_link_that_changed(
    entry: Path, target: Path, tmp_path: Path, backups: Path
) -> None:
    plan = LinkPlan(entry, target, RECORDED, "repoint")
    other = tmp_path / "other.md"
    _ = other.write_text("other rules\n", encoding="utf-8")
    entry.symlink_to(other)

    with pytest.raises(OSError, match="changed since it was planned"):
        _ = apply_link(plan, backups, NOW)

    assert entry.readlink() == other


OTHER = {"assets/other.md": "x\n"}
"""A source that has no AGENTS.md."""


def test_agents_md_file_path_wins_over_the_flag(
    build_context: ContextBuilder, home: Path
) -> None:
    mine = home / "notes" / "AGENTS.md"
    mine.parent.mkdir()
    _ = mine.write_text("mine\n", encoding="utf-8")
    ctx = build_context(
        {"assets/AGENTS.md": "shared\n"},
        registry(f"agents-md-file: {mine}", "use-default-agents-md: true"),
    )

    assert wanted_instructions(ctx, ctx.registry.account("personal")) == mine


def test_agents_md_file_can_start_with_a_tilde(
    build_context: ContextBuilder, home: Path
) -> None:
    mine = home / "notes" / "AGENTS.md"
    mine.parent.mkdir()
    _ = mine.write_text("mine\n", encoding="utf-8")
    ctx = build_context(OTHER, registry("agents-md-file: ~/notes/AGENTS.md"))

    assert wanted_instructions(ctx, ctx.registry.account("personal")) == mine


def test_agents_md_file_can_be_an_asset_reference(
    build_context: ContextBuilder,
) -> None:
    ctx = build_context(
        {
            "bravo/assets/AGENTS.md": "bravo\n",
            "personal/assets/AGENTS.md": "personal\n",
        },
        registry('agents-md-file: "@bravo/AGENTS.md"'),
    )

    wanted = wanted_instructions(ctx, ctx.registry.account("personal"))

    assert wanted == ctx.dirs.conf_d / "bravo" / "assets" / "AGENTS.md"


def test_the_flag_links_the_copy_of_the_default_template(
    build_context: ContextBuilder,
) -> None:
    ctx = build_context(
        {"assets/AGENTS.in.md": "shared\n"}, registry("use-default-agents-md: true")
    )

    wanted = wanted_instructions(ctx, ctx.registry.account("personal"))

    assert wanted == ctx.dirs.state_dir / "sync" / "personal" / "assets" / "AGENTS.md"


def test_the_default_can_be_renamed(build_context: ContextBuilder) -> None:
    ctx = build_context(
        {"assets/docs/RULES.md": "rules\n"},
        registry(
            "use-default-agents-md: true", top="default-agents-md: docs/RULES.md\n"
        ),
    )

    wanted = wanted_instructions(ctx, ctx.registry.account("personal"))

    assert wanted == ctx.dirs.conf_d / "assets" / "docs" / "RULES.md"


def test_neither_set_wants_nothing(build_context: ContextBuilder) -> None:
    ctx = build_context({"assets/AGENTS.md": "shared\n"}, MINIMAL_REGISTRY)

    assert wanted_instructions(ctx, ctx.registry.account("personal")) is None


def test_the_flag_turned_off_wants_nothing(build_context: ContextBuilder) -> None:
    ctx = build_context(
        {"assets/AGENTS.md": "shared\n"}, registry("use-default-agents-md: false")
    )

    assert wanted_instructions(ctx, ctx.registry.account("personal")) is None


@pytest.mark.parametrize(
    ("lines", "message"),
    [
        (
            ["use-default-agents-md: true"],
            r"default-agents-md: 'AGENTS\.in\.md': no such",
        ),
        (
            ['agents-md-file: "@bravo/AGENTS.md"'],
            r"agents-md-file: .*'@bravo/AGENTS.md': no such",
        ),
        (['agents-md-file: "docs/AGENTS.md"'], r"agents-md-file: 'docs/AGENTS.md'"),
        (
            ["agents-md-file: /nowhere/AGENTS.md"],
            r"agents-md-file: /nowhere/AGENTS.md is not a file",
        ),
    ],
    ids=["default", "qualified-asset", "asset", "path"],
)
def test_a_missing_target_is_a_config_error(
    build_context: ContextBuilder, lines: list[str], message: str
) -> None:
    ctx = build_context({"bravo/assets/other.md": "x\n"}, registry(*lines))

    with pytest.raises(ConfigError, match=rf"account 'personal': {message}"):
        _ = wanted_instructions(ctx, ctx.registry.account("personal"))


def test_a_directory_is_not_a_target(build_context: ContextBuilder, home: Path) -> None:
    (home / "notes").mkdir()
    ctx = build_context(OTHER, registry("agents-md-file: ~/notes"))

    with pytest.raises(ConfigError, match="notes is not a file"):
        _ = wanted_instructions(ctx, ctx.registry.account("personal"))


def test_an_ambiguous_default_asks_for_a_source_prefix(
    build_context: ContextBuilder,
) -> None:
    ctx = build_context(
        {"bravo/assets/AGENTS.in.md": "a\n", "personal/assets/AGENTS.in.md": "b\n"},
        registry("use-default-agents-md: true"),
    )

    with pytest.raises(ConfigError, match=r"account 'personal'.*add an @name/ prefix"):
        _ = wanted_instructions(ctx, ctx.registry.account("personal"))


def test_the_instructions_file_cannot_be_its_own_target(
    build_context: ContextBuilder, home: Path
) -> None:
    (home / ".claude").mkdir()
    _ = (home / ".claude" / "CLAUDE.md").write_text("mine\n", encoding="utf-8")
    ctx = build_context(OTHER, registry("agents-md-file: ~/.claude/CLAUDE.md"))

    with pytest.raises(ConfigError, match="itself"):
        _ = wanted_instructions(ctx, ctx.registry.account("personal"))
