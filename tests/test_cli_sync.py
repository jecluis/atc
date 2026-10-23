"""``atc sync``: what it writes, what it refuses, and what it exits with."""

import json
import stat
from pathlib import Path

import pytest

from atc.cli import main
from atc.exitcodes import CHECK, CONFIG, OK, USAGE
from atc.jsonval import JsonObject, dumps
from atc.paths import Dirs
from atc.state import SyncState
from tests.conftest import ConfigWriter, RegistryWriter
from tests.helpers import (
    LINKED_REGISTRY,
    MINIMAL_REGISTRY,
    make_source,
    registry,
    skill_files,
    skill_md,
    unit,
)

PERSONAL = "conf/accounts/personal.json"
WORK = "conf/accounts/work.json"
SETTINGS: JsonObject = {"model": "opus"}
TWO_ACCOUNTS = MINIMAL_REGISTRY + "      work: {agent: claude, dir: ~/.config/work}\n"


def settings_file(home: Path) -> Path:
    return home / ".claude" / "settings.json"


def set_up(
    configure: ConfigWriter, home: Path, settings: JsonObject = SETTINGS
) -> Path:
    """Make the account's directory and its unit, and return its settings file."""
    (home / ".claude").mkdir()
    configure({PERSONAL: unit(settings)})
    return settings_file(home)


def state(dirs: Dirs) -> SyncState:
    return SyncState(dirs.state_dir, "personal")


def test_first_sync_writes_settings_and_records_last(
    configure: ConfigWriter,
    home: Path,
    dirs: Dirs,
    capsys: pytest.CaptureFixture[str],
) -> None:
    live = set_up(configure, home)

    status = main(["sync"])

    captured = capsys.readouterr()
    assert status == OK
    assert captured.out == "personal: written\n"
    assert captured.err == ""
    assert live.read_text() == dumps(SETTINGS)
    assert stat.S_IMODE(live.stat().st_mode) == 0o600
    assert state(dirs).last_settings() == SETTINGS
    assert not state(dirs).backups_dir.exists()


def test_a_second_sync_finds_it_current(
    configure: ConfigWriter, home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = set_up(configure, home)
    _ = main(["sync"])
    _ = capsys.readouterr()

    assert main(["sync"]) == OK

    assert capsys.readouterr().out == "personal: current\n"


def test_check_exits_3_when_a_write_is_pending_and_writes_nothing(
    configure: ConfigWriter,
    home: Path,
    dirs: Dirs,
    capsys: pytest.CaptureFixture[str],
) -> None:
    live = set_up(configure, home)

    status = main(["sync", "--check"])

    assert status == CHECK
    assert capsys.readouterr().out == "personal: would write\n"
    assert not live.exists()
    assert not dirs.state_dir.exists()


def test_check_exits_0_when_current(
    configure: ConfigWriter,
    home: Path,
    dirs: Dirs,
    capsys: pytest.CaptureFixture[str],
) -> None:
    live = set_up(configure, home)
    _ = live.write_text('{"model":"opus"}')

    status = main(["sync", "--check"])

    assert status == OK
    assert capsys.readouterr().out == "personal: current\n"
    assert live.read_text() == '{"model":"opus"}'
    assert not dirs.state_dir.exists()


def test_diff_writes_nothing(
    configure: ConfigWriter,
    home: Path,
    dirs: Dirs,
    capsys: pytest.CaptureFixture[str],
) -> None:
    live = set_up(configure, home)
    _ = live.write_text('{"model": "x"}')
    state(dirs).record_settings({"model": "x"})

    status = main(["sync", "--diff"])

    out = capsys.readouterr().out
    assert status == OK
    assert out.startswith("personal: would write\n")
    assert f"--- {live} (live)\n+++ {live} (new)\n" in out
    assert '-  "model": "x"\n+  "model": "opus"\n' in out
    assert live.read_text() == '{"model": "x"}'
    assert state(dirs).last_settings() == {"model": "x"}
    assert not state(dirs).backups_dir.exists()


def test_a_diff_of_a_missing_file_adds_everything(
    configure: ConfigWriter, home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = set_up(configure, home)

    assert main(["sync", "--diff"]) == OK

    out = capsys.readouterr().out
    assert "--- /dev/null\n" in out
    assert '+  "model": "opus"\n' in out


def test_edited_outside_atc_shows_the_diff_and_exits_3(
    configure: ConfigWriter,
    home: Path,
    dirs: Dirs,
    capsys: pytest.CaptureFixture[str],
) -> None:
    live = set_up(configure, home)
    _ = live.write_text('{"model": "y"}')
    state(dirs).record_settings({"model": "x"})

    status = main(["sync"])

    out = capsys.readouterr().out
    assert status == CHECK
    assert out.startswith(
        "personal: edited outside atc (--discard-live overwrites it)\n"
    )
    assert f"--- {live} (last synced)\n+++ {live} (live)\n" in out
    assert '-  "model": "x"\n+  "model": "y"\n' in out
    assert live.read_text() == '{"model": "y"}'
    assert not state(dirs).backups_dir.exists()


def test_discard_live_backs_up_and_overwrites_an_edit(
    configure: ConfigWriter, home: Path, dirs: Dirs
) -> None:
    live = set_up(configure, home)
    _ = live.write_text('{"model": "y"}')
    state(dirs).record_settings({"model": "x"})

    assert main(["sync", "--discard-live"]) == OK

    assert live.read_text() == dumps(SETTINGS)
    (backup,) = state(dirs).backups_dir.iterdir()
    assert backup.read_text() == '{"model": "y"}'


def test_never_synced_and_different_stops_until_taken_over(
    configure: ConfigWriter,
    home: Path,
    dirs: Dirs,
    capsys: pytest.CaptureFixture[str],
) -> None:
    live = set_up(configure, home)
    _ = live.write_text('{"model": "x"}')

    assert main(["sync"]) == CHECK

    out = capsys.readouterr().out
    assert out.startswith(
        "personal: never synced and differs (--take-over writes it)\n"
    )
    assert '-  "model": "x"\n+  "model": "opus"\n' in out
    assert live.read_text() == '{"model": "x"}'
    assert not dirs.state_dir.exists()
    assert main(["sync", "--check"]) == CHECK


def test_an_invalid_live_file_is_shown_without_its_control_characters(
    configure: ConfigWriter, home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    live = set_up(configure, home)
    _ = live.write_text("{nope\x1b[2J")

    assert main(["sync"]) == CHECK

    out = capsys.readouterr().out
    assert "\x1b" not in out
    assert "-{nope\\x1b[2J\n" in out


def test_a_live_number_too_large_for_json_is_not_valid_json(
    configure: ConfigWriter, home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    live = set_up(configure, home)
    _ = live.write_text('{"cleanupPeriodDays": 1e400}')

    assert main(["sync"]) == CHECK

    out = capsys.readouterr().out
    assert out.startswith("personal: never synced and differs (--take-over writes it)")
    assert '-{"cleanupPeriodDays": 1e400}\n' in out
    assert live.read_text() == '{"cleanupPeriodDays": 1e400}'


def test_a_settings_file_that_is_a_symbolic_link_is_written_through_it(
    configure: ConfigWriter, home: Path, dirs: Dirs, tmp_path: Path
) -> None:
    live = set_up(configure, home)
    dotfiles = tmp_path / "dotfiles"
    dotfiles.mkdir()
    real = dotfiles / "claude-settings.json"
    _ = real.write_text(dumps({"model": "x"}))
    real.chmod(0o644)
    live.symlink_to(real)

    assert main(["sync", "--take-over"]) == OK

    assert live.readlink() == real
    assert json.loads(real.read_text()) == SETTINGS
    assert stat.S_IMODE(real.stat().st_mode) == 0o644
    (copy,) = state(dirs).backups_dir.iterdir()
    assert json.loads(copy.read_text()) == {"model": "x"}


def test_take_over_backs_up_and_writes(
    configure: ConfigWriter,
    home: Path,
    dirs: Dirs,
    capsys: pytest.CaptureFixture[str],
) -> None:
    live = set_up(configure, home)
    _ = live.write_text('{"model": "x"}')

    status = main(["sync", "--take-over"])

    assert status == OK
    assert capsys.readouterr().out == "personal: written\n"
    assert live.read_text() == dumps(SETTINGS)
    assert state(dirs).last_settings() == SETTINGS
    (backup,) = state(dirs).backups_dir.iterdir()
    assert backup.name.endswith("Z-settings.json")
    assert backup.read_text() == '{"model": "x"}'


def test_settings_unchanged_since_the_last_sync_follow_the_units(
    configure: ConfigWriter,
    home: Path,
    dirs: Dirs,
    capsys: pytest.CaptureFixture[str],
) -> None:
    live = set_up(configure, home)
    _ = main(["sync"])
    _ = make_source(dirs.conf_d, {PERSONAL: unit({"model": "sonnet"})})
    _ = capsys.readouterr()

    assert main(["sync"]) == OK

    assert capsys.readouterr().out == "personal: written\n"
    assert json.loads(live.read_text()) == {"model": "sonnet"}
    (backup,) = state(dirs).backups_dir.iterdir()
    assert json.loads(backup.read_text()) == SETTINGS


def test_an_error_in_one_account_writes_no_account(
    configure: ConfigWriter,
    home: Path,
    dirs: Dirs,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (home / ".claude").mkdir()
    (home / ".config" / "work").mkdir(parents=True)
    configure(
        {
            PERSONAL: unit(SETTINGS),
            WORK: unit({"autoMemoryDirectory": "/m"}),
        },
        TWO_ACCOUNTS,
    )

    status = main(["sync"])

    captured = capsys.readouterr()
    assert status == CONFIG
    assert captured.out == ""
    assert "error: accounts/work.json: /autoMemoryDirectory" in captured.err
    assert not settings_file(home).exists()
    assert not (home / ".config" / "work" / "settings.json").exists()
    assert not dirs.state_dir.exists()


def test_every_account_that_cannot_be_planned_is_reported(
    configure: ConfigWriter, home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (home / ".claude").mkdir()
    configure(
        {PERSONAL: unit({"autoMemoryDirectory": "/m"}), WORK: unit()}, TWO_ACCOUNTS
    )

    assert main(["sync"]) == CONFIG

    err = capsys.readouterr().err
    assert err.count("error:") == 2
    assert "accounts/personal.json: /autoMemoryDirectory" in err
    assert "account 'work'" in err


def test_named_accounts_are_the_only_ones_synced(
    configure: ConfigWriter, home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (home / ".claude").mkdir()
    (home / ".config" / "work").mkdir(parents=True)
    configure({PERSONAL: unit(SETTINGS), WORK: unit(SETTINGS)}, TWO_ACCOUNTS)

    assert main(["sync", "work", "work"]) == OK

    assert capsys.readouterr().out == "work: written\n"
    assert (home / ".config" / "work" / "settings.json").exists()
    assert not settings_file(home).exists()


def test_an_unknown_account_is_a_config_error(
    configure: ConfigWriter, home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = set_up(configure, home)

    assert main(["sync", "nope"]) == CONFIG

    assert "'nope'" in capsys.readouterr().err


def test_conf_d_requires_check_or_diff(
    tmp_path: Path, configure: ConfigWriter, home: Path
) -> None:
    live = set_up(configure, home)
    elsewhere = make_source(tmp_path / "elsewhere", {PERSONAL: unit(SETTINGS)})

    assert main(["sync", "--conf-d", str(elsewhere)]) == USAGE
    assert main(["sync", "--conf-d", str(elsewhere), "--check"]) == CHECK
    assert main(["sync", "--conf-d", str(elsewhere), "--diff"]) == OK

    assert not live.exists()


def test_conf_d_names_the_sources_to_build_from(
    tmp_path: Path,
    configure: ConfigWriter,
    home: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    live = set_up(configure, home)
    _ = live.write_text(dumps({"model": "other"}))
    elsewhere = make_source(
        tmp_path / "elsewhere", {PERSONAL: unit({"model": "other"})}
    )

    assert main(["sync", "--conf-d", str(elsewhere), "--check"]) == OK

    assert capsys.readouterr().out == "personal: current\n"


def test_a_missing_account_dir_is_a_config_error(
    configure: ConfigWriter, home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    configure({PERSONAL: unit(SETTINGS)})

    status = main(["sync"])

    captured = capsys.readouterr()
    assert status == CONFIG
    assert captured.out == ""
    assert captured.err.startswith("error: account 'personal': ")
    assert str(home / ".claude") in captured.err
    assert not settings_file(home).parent.exists()


def test_rule_warnings_are_printed(
    configure: ConfigWriter, home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    permissions: JsonObject = {"allow": ["Read(.env)"], "ask": ["Read"]}
    live = set_up(configure, home, {"permissions": permissions})

    status = main(["sync"])

    captured = capsys.readouterr()
    assert status == OK
    assert captured.out == "personal: written\n"
    assert captured.err == (
        "warning: allow Read(.env) (accounts/personal.json) never applies: "
        "ask Read (accounts/personal.json) takes precedence\n"
    )
    assert live.exists()


def test_lax_turns_unknown_keys_into_warnings(
    configure: ConfigWriter, home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    live = set_up(configure, home, {"permisions": {}})

    assert main(["sync"]) == CONFIG
    assert not live.exists()
    _ = capsys.readouterr()

    assert main(["sync", "--lax"]) == OK

    assert "warning:" in capsys.readouterr().err
    assert json.loads(live.read_text()) == {"permisions": {}}


def test_a_write_that_fails_exits_1_with_an_error_line(
    configure: ConfigWriter,
    home: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = set_up(configure, home)
    _ = live.write_text('{"model": "x"}')

    def refuse(path: Path, text: str) -> None:
        raise PermissionError(13, "Permission denied", f"{path}: {len(text)}")

    monkeypatch.setattr("atc.sync.atomic_write", refuse)

    status = main(["sync", "--take-over"])

    captured = capsys.readouterr()
    assert status == CONFIG
    assert captured.out == ""
    assert captured.err.startswith("error: account 'personal': ")
    assert "Permission denied" in captured.err
    assert "Traceback" not in captured.err
    assert live.read_text() == '{"model": "x"}'


MISSING_FILE_REGISTRY = LINKED_REGISTRY + (
    "  work: {agent: claude, dir: ~/.config/work, agents-md-file: /nowhere/AGENTS.md}\n"
)
AGENTS = "assets/AGENTS.md"
CONFLICT = "(conflict; --take-over replaces a file or link, never a directory)"


def set_up_linked(
    configure: ConfigWriter,
    home: Path,
    dirs: Dirs,
    registry_text: str = LINKED_REGISTRY,
) -> tuple[Path, Path]:
    """
    Make the account's directory, its unit and the default instructions file.

    Returns the instructions file's path in the account, and the asset it links to.
    """
    (home / ".claude").mkdir()
    configure({PERSONAL: unit(SETTINGS), AGENTS: "shared rules\n"}, registry_text)
    return home / ".claude" / "CLAUDE.md", dirs.conf_d / AGENTS


def test_an_account_without_claude_md_gets_the_link(
    configure: ConfigWriter,
    home: Path,
    dirs: Dirs,
    capsys: pytest.CaptureFixture[str],
) -> None:
    claude_md, asset = set_up_linked(configure, home, dirs)

    status = main(["sync"])

    captured = capsys.readouterr()
    assert status == OK
    assert captured.out == (
        f"personal: written\npersonal: CLAUDE.md: (none) → {asset} (changed)\n"
    )
    assert captured.err == ""
    assert claude_md.readlink() == asset
    assert claude_md.read_text() == "shared rules\n"
    assert state(dirs).links() == {str(claude_md): str(asset)}
    assert not state(dirs).backups_dir.exists()


def test_a_second_sync_finds_the_link_current(
    configure: ConfigWriter, home: Path, capsys: pytest.CaptureFixture[str], dirs: Dirs
) -> None:
    _ = set_up_linked(configure, home, dirs)
    _ = main(["sync"])
    _ = capsys.readouterr()

    assert main(["sync"]) == OK

    assert capsys.readouterr().out == "personal: current\n"


def test_a_hand_written_claude_md_is_a_conflict_and_left_alone(
    configure: ConfigWriter,
    home: Path,
    dirs: Dirs,
    capsys: pytest.CaptureFixture[str],
) -> None:
    claude_md, asset = set_up_linked(configure, home, dirs)
    _ = claude_md.write_text("my rules\n")

    status = main(["sync"])

    out = capsys.readouterr().out
    assert status == CHECK
    assert out == (
        f"personal: written\npersonal: CLAUDE.md: (not made by atc) → {asset} "
        f"{CONFLICT}\n"
    )
    assert not claude_md.is_symlink()
    assert claude_md.read_text() == "my rules\n"
    assert state(dirs).links() == {}
    assert not state(dirs).backups_dir.exists()
    assert main(["sync", "--check"]) == CHECK


def test_take_over_replaces_it_with_a_link_and_keeps_a_backup(
    configure: ConfigWriter,
    home: Path,
    dirs: Dirs,
    capsys: pytest.CaptureFixture[str],
) -> None:
    claude_md, asset = set_up_linked(configure, home, dirs)
    _ = claude_md.write_text("my rules\n")

    status = main(["sync", "--take-over"])

    out = capsys.readouterr().out
    assert status == OK
    assert out == (
        f"personal: written\n"
        f"personal: CLAUDE.md: (a file, backed up first) → {asset} (changed)\n"
    )
    assert claude_md.readlink() == asset
    (saved,) = state(dirs).backups_dir.iterdir()
    assert saved.name.endswith("Z-CLAUDE.md")
    assert saved.read_text() == "my rules\n"
    assert state(dirs).links() == {str(claude_md): str(asset)}


def test_take_over_replaces_a_link_atc_did_not_make(
    configure: ConfigWriter, home: Path, dirs: Dirs, tmp_path: Path
) -> None:
    claude_md, asset = set_up_linked(configure, home, dirs)
    mine = tmp_path / "mine.md"
    _ = mine.write_text("my rules\n")
    claude_md.symlink_to(mine)

    assert main(["sync"]) == CHECK
    assert claude_md.readlink() == mine

    assert main(["sync", "--take-over"]) == OK

    assert claude_md.readlink() == asset
    assert mine.read_text() == "my rules\n"
    assert not state(dirs).backups_dir.exists()


def test_take_over_never_replaces_a_directory(
    configure: ConfigWriter, home: Path, dirs: Dirs, capsys: pytest.CaptureFixture[str]
) -> None:
    claude_md, asset = set_up_linked(configure, home, dirs)
    claude_md.mkdir()
    _ = (claude_md / "keep.txt").write_text("keep\n")

    status = main(["sync", "--take-over"])

    assert status == CHECK
    assert (
        f"personal: CLAUDE.md: (not made by atc) → {asset} " in capsys.readouterr().out
    )
    assert (claude_md / "keep.txt").read_text() == "keep\n"
    assert state(dirs).links() == {}


def test_turning_the_flag_off_removes_atcs_link(
    configure: ConfigWriter,
    write_registry: RegistryWriter,
    home: Path,
    dirs: Dirs,
    capsys: pytest.CaptureFixture[str],
) -> None:
    claude_md, asset = set_up_linked(configure, home, dirs)
    _ = main(["sync"])
    _ = capsys.readouterr()
    _ = write_registry(MINIMAL_REGISTRY)

    status = main(["sync"])

    assert status == OK
    assert capsys.readouterr().out == (
        f"personal: current\npersonal: CLAUDE.md: {asset} → (none) (changed)\n"
    )
    assert not claude_md.is_symlink()
    assert not claude_md.exists()
    assert asset.read_text() == "shared rules\n"
    assert state(dirs).links() == {}


def test_turning_the_flag_off_leaves_a_hand_written_file(
    configure: ConfigWriter,
    write_registry: RegistryWriter,
    home: Path,
    dirs: Dirs,
    capsys: pytest.CaptureFixture[str],
) -> None:
    claude_md, _asset = set_up_linked(configure, home, dirs)
    _ = claude_md.write_text("my rules\n")
    _ = write_registry(MINIMAL_REGISTRY)

    status = main(["sync"])

    assert status == OK
    assert capsys.readouterr().out == "personal: written\n"
    assert claude_md.read_text() == "my rules\n"
    assert not state(dirs).backups_dir.exists()


def test_turning_the_flag_off_leaves_a_link_atc_did_not_make(
    configure: ConfigWriter,
    write_registry: RegistryWriter,
    home: Path,
    tmp_path: Path,
    dirs: Dirs,
) -> None:
    claude_md, _asset = set_up_linked(configure, home, dirs)
    mine = tmp_path / "mine.md"
    _ = mine.write_text("my rules\n")
    claude_md.symlink_to(mine)
    _ = write_registry(MINIMAL_REGISTRY)

    assert main(["sync"]) == OK

    assert claude_md.readlink() == mine


def test_a_link_that_was_made_by_atc_but_edited_since_is_left_alone(
    configure: ConfigWriter,
    write_registry: RegistryWriter,
    home: Path,
    dirs: Dirs,
    tmp_path: Path,
) -> None:
    claude_md, asset = set_up_linked(configure, home, dirs)
    _ = main(["sync"])
    mine = tmp_path / "mine.md"
    _ = mine.write_text("my rules\n")
    claude_md.unlink()
    claude_md.symlink_to(mine)
    _ = write_registry(MINIMAL_REGISTRY)

    assert main(["sync"]) == OK

    assert claude_md.readlink() == mine
    assert state(dirs).links() == {str(claude_md): str(asset)}


def test_a_moved_source_repoints_the_link(
    configure: ConfigWriter,
    write_registry: RegistryWriter,
    home: Path,
    dirs: Dirs,
    capsys: pytest.CaptureFixture[str],
) -> None:
    claude_md, asset = set_up_linked(configure, home, dirs)
    _ = main(["sync"])
    other = home / "elsewhere" / "AGENTS.md"
    other.parent.mkdir()
    _ = other.write_text("moved rules\n")
    _ = write_registry(registry(f"agents-md-file: {other}"))
    _ = capsys.readouterr()

    assert main(["sync"]) == OK

    assert capsys.readouterr().out == (
        f"personal: current\npersonal: CLAUDE.md: {asset} → {other} (changed)\n"
    )
    assert claude_md.readlink() == other
    assert state(dirs).links() == {str(claude_md): str(other)}


def test_diff_lists_the_link_change_and_writes_nothing(
    configure: ConfigWriter,
    home: Path,
    dirs: Dirs,
    capsys: pytest.CaptureFixture[str],
) -> None:
    claude_md, asset = set_up_linked(configure, home, dirs)

    status = main(["sync", "--diff"])

    out = capsys.readouterr().out
    assert status == OK
    assert f"personal: CLAUDE.md: (none) → {asset} (would change)\n" in out
    assert not claude_md.exists()
    assert not claude_md.is_symlink()
    assert not dirs.state_dir.exists()


def test_check_exits_3_when_only_a_link_is_pending(
    configure: ConfigWriter,
    home: Path,
    dirs: Dirs,
    capsys: pytest.CaptureFixture[str],
) -> None:
    claude_md, asset = set_up_linked(configure, home, dirs)
    _ = main(["sync"])
    claude_md.unlink()
    _ = capsys.readouterr()

    status = main(["sync", "--check"])

    assert status == CHECK
    assert capsys.readouterr().out == (
        f"personal: current\npersonal: CLAUDE.md: (none) → {asset} (would change)\n"
    )
    assert not claude_md.is_symlink()


def test_check_exits_3_for_a_conflict_with_nothing_else_to_do(
    configure: ConfigWriter, home: Path, dirs: Dirs
) -> None:
    claude_md, _asset = set_up_linked(configure, home, dirs)
    _ = main(["sync"])
    claude_md.unlink()
    _ = claude_md.write_text("my rules\n")

    assert main(["sync", "--check"]) == CHECK


def test_src_check_fails_when_the_default_instructions_file_is_missing(
    configure: ConfigWriter, home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (home / ".claude").mkdir()
    configure({PERSONAL: unit(SETTINGS)}, LINKED_REGISTRY)

    status = main(["src", "check"])

    captured = capsys.readouterr()
    assert status == CONFIG
    assert "error: " in captured.err
    assert "account 'personal': default-agents-md: 'AGENTS.md': no such asset" in (
        captured.err
    )


def test_src_check_passes_when_the_default_instructions_file_exists(
    configure: ConfigWriter, home: Path, dirs: Dirs
) -> None:
    _ = set_up_linked(configure, home, dirs)

    assert main(["src", "check"]) == OK


def test_an_account_whose_instructions_file_is_missing_stops_every_account(
    configure: ConfigWriter,
    home: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (home / ".claude").mkdir()
    (home / ".config" / "work").mkdir(parents=True)
    configure(
        {PERSONAL: unit(SETTINGS), WORK: unit(SETTINGS), AGENTS: "shared rules\n"},
        MISSING_FILE_REGISTRY,
    )

    status = main(["sync"])

    captured = capsys.readouterr()
    assert status == CONFIG
    assert captured.out == ""
    assert "account 'work': agents-md-file: /nowhere/AGENTS.md is not a file" in (
        captured.err
    )
    assert not (home / ".claude" / "CLAUDE.md").is_symlink()
    assert not settings_file(home).exists()


def test_both_keys_set_warns(
    configure: ConfigWriter, home: Path, capsys: pytest.CaptureFixture[str], dirs: Dirs
) -> None:
    text = registry("use-default-agents-md: true", "agents-md-file: AGENTS.md")
    claude_md, asset = set_up_linked(configure, home, dirs, text)

    status = main(["sync"])

    captured = capsys.readouterr()
    assert status == OK
    assert (
        "warning: "
        f"{dirs.config_dir / 'machine.yaml'}: /accounts/personal: "
        "agents-md-file is set, so use-default-agents-md has no effect\n"
    ) in captured.err
    assert claude_md.readlink() == asset


SKILL_FILES = skill_files("git-commits", "rust-2024", "phase-review")


def set_up_skills(
    configure: ConfigWriter, home: Path, dirs: Dirs, *selected: str
) -> tuple[Path, Path]:
    """
    Make the account's directory, its unit, and the skills, of which it selects some.

    Returns the account's skills directory, which does not exist yet, and the
    source's.
    """
    (home / ".claude").mkdir()
    configure(
        {PERSONAL: unit(SETTINGS), **SKILL_FILES},
        registry(f"skills: [{', '.join(selected)}]"),
    )
    return home / ".claude" / "skills", dirs.conf_d / "skills"


def test_selected_skills_are_linked_into_the_account(
    configure: ConfigWriter,
    home: Path,
    dirs: Dirs,
    capsys: pytest.CaptureFixture[str],
) -> None:
    skills, source = set_up_skills(configure, home, dirs, "git-commits", "rust-2024")

    status = main(["sync"])

    captured = capsys.readouterr()
    assert status == OK
    assert captured.out == (
        "personal: written\n"
        f"personal: git-commits: (none) → {source / 'git-commits'} (changed)\n"
        f"personal: rust-2024: (none) → {source / 'rust-2024'} (changed)\n"
    )
    assert captured.err == ""
    assert (skills / "git-commits").readlink() == source / "git-commits"
    assert (skills / "git-commits" / "SKILL.md").is_file()
    assert (skills / "rust-2024").readlink() == source / "rust-2024"
    assert not (skills / "phase-review").exists()
    assert state(dirs).links() == {
        str(skills / "git-commits"): str(source / "git-commits"),
        str(skills / "rust-2024"): str(source / "rust-2024"),
    }
    assert main(["sync"]) == OK
    assert capsys.readouterr().out == "personal: current\n"


def test_a_missing_skills_directory_is_created(
    configure: ConfigWriter, home: Path, dirs: Dirs
) -> None:
    skills, _source = set_up_skills(configure, home, dirs, "git-commits")
    assert not skills.exists()

    assert main(["sync"]) == OK

    assert skills.is_dir()
    assert not skills.is_symlink()
    assert [entry.name for entry in skills.iterdir()] == ["git-commits"]


def test_a_skills_directory_that_is_a_symbolic_link_is_refused(
    configure: ConfigWriter,
    home: Path,
    dirs: Dirs,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    skills, _source = set_up_skills(configure, home, dirs, "git-commits")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    skills.symlink_to(elsewhere)

    assert main(["sync"]) == CONFIG

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        f"error: account 'personal': {skills} is a symbolic link, and atc makes "
        + "skill links only in a directory; replace the link with a directory\n"
    )
    assert list(elsewhere.iterdir()) == []
    assert not settings_file(home).exists()


def test_sync_warns_once_about_skills_it_skipped_or_that_look_wrong(
    configure: ConfigWriter,
    home: Path,
    dirs: Dirs,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (home / ".claude").mkdir()
    (home / ".config" / "work").mkdir(parents=True)
    configure(
        {
            PERSONAL: unit(SETTINGS),
            WORK: unit(SETTINGS),
            **SKILL_FILES,
            "skills/Bad/SKILL.md": skill_md("Bad"),
            "skills/odd/SKILL.md": skill_md("other"),
        },
        TWO_ACCOUNTS,
    )
    skills = dirs.conf_d / "skills"

    assert main(["sync"]) == OK

    err = capsys.readouterr().err
    assert err.count(f"warning: {skills / 'Bad'}: 'Bad' is not a valid skill name") == 1
    assert (
        err.count(
            f"warning: {skills / 'odd' / 'SKILL.md'}: the front matter's name 'other' "
            + "differs from the directory name 'odd'"
        )
        == 1
    )


def test_a_deselected_skill_loses_atcs_link(
    configure: ConfigWriter,
    write_registry: RegistryWriter,
    home: Path,
    dirs: Dirs,
    capsys: pytest.CaptureFixture[str],
) -> None:
    skills, source = set_up_skills(configure, home, dirs, "git-commits", "rust-2024")
    _ = main(["sync"])
    _ = capsys.readouterr()
    _ = write_registry(registry("skills: [git-commits]"))

    status = main(["sync"])

    assert status == OK
    assert capsys.readouterr().out == (
        f"personal: current\npersonal: rust-2024: {source / 'rust-2024'} → (none) "
        "(changed)\n"
    )
    assert not (skills / "rust-2024").is_symlink()
    assert (skills / "git-commits").is_symlink()
    assert (source / "rust-2024" / "SKILL.md").is_file()
    assert state(dirs).links() == {
        str(skills / "git-commits"): str(source / "git-commits")
    }


def test_a_foreign_skill_link_is_a_conflict_until_take_over(
    configure: ConfigWriter,
    home: Path,
    dirs: Dirs,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    skills, source = set_up_skills(configure, home, dirs, "git-commits")
    checkout = tmp_path / "checkout" / "skills"
    (checkout / "git-commits").mkdir(parents=True)
    skills.mkdir()
    (skills / "git-commits").symlink_to(checkout / "git-commits")
    (skills / "rust-2024").symlink_to(checkout / "rust-2024")

    status = main(["sync"])

    assert status == CHECK
    assert capsys.readouterr().out == (
        "personal: written\n"
        f"personal: git-commits: (not made by atc) → {source / 'git-commits'} "
        f"{CONFLICT}\n"
    )
    assert (skills / "git-commits").readlink() == checkout / "git-commits"
    assert state(dirs).links() == {}

    assert main(["sync", "--take-over"]) == OK

    assert (skills / "git-commits").readlink() == source / "git-commits"
    assert (checkout / "git-commits").is_dir()
    assert (skills / "rust-2024").readlink() == checkout / "rust-2024"
    assert state(dirs).links() == {
        str(skills / "git-commits"): str(source / "git-commits")
    }
    assert not state(dirs).backups_dir.exists()


def test_directories_and_unrecorded_entries_are_left_alone(
    configure: ConfigWriter,
    write_registry: RegistryWriter,
    home: Path,
    dirs: Dirs,
    capsys: pytest.CaptureFixture[str],
) -> None:
    skills, source = set_up_skills(configure, home, dirs, "git-commits", "phase-review")
    for name in ("phase-review", "synced", ".trash"):
        (skills / name).mkdir(parents=True)
        _ = (skills / name / "keep.txt").write_text("keep\n")
    _ = (skills / "notes.md").write_text("mine\n")

    status = main(["sync", "--take-over"])

    assert status == CHECK
    assert capsys.readouterr().out == (
        "personal: written\n"
        f"personal: git-commits: (none) → {source / 'git-commits'} (changed)\n"
        f"personal: phase-review: (not made by atc) → {source / 'phase-review'} "
        f"{CONFLICT}\n"
    )
    for name in ("phase-review", "synced", ".trash"):
        assert (skills / name / "keep.txt").read_text() == "keep\n"
    assert not (skills / "phase-review").is_symlink()
    assert (skills / "notes.md").read_text() == "mine\n"
    assert state(dirs).links() == {
        str(skills / "git-commits"): str(source / "git-commits")
    }

    _ = write_registry(registry("skills: [git-commits]"))

    assert main(["sync", "--take-over"]) == OK
    assert capsys.readouterr().out == "personal: current\n"
    assert (skills / "phase-review" / "keep.txt").is_file()
    assert (skills / "synced" / "keep.txt").is_file()
