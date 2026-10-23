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
from tests.conftest import ConfigWriter
from tests.helpers import MINIMAL_REGISTRY, make_source, unit

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
