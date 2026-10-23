"""Where atc keeps its configuration."""

from pathlib import Path

from atc.paths import dirs_from_env
from tests.helpers import UID


def test_an_xdg_value_that_is_not_absolute_is_ignored(home: Path) -> None:
    dirs = dirs_from_env({"XDG_CONFIG_HOME": "relative/config"}, home, UID)
    assert dirs.config_dir == home / ".config" / "atc"


def test_an_absolute_xdg_value_is_used(home: Path, tmp_path: Path) -> None:
    dirs = dirs_from_env({"XDG_CONFIG_HOME": str(tmp_path / "cfg")}, home, UID)
    assert dirs.config_dir == tmp_path / "cfg" / "atc"


def test_an_empty_xdg_value_is_ignored(home: Path) -> None:
    dirs = dirs_from_env({"XDG_CONFIG_HOME": ""}, home, UID)
    assert dirs.config_dir == home / ".config" / "atc"


def test_conf_d_sits_under_the_config_dir(home: Path) -> None:
    dirs = dirs_from_env({}, home, UID)
    assert dirs.conf_d == dirs.config_dir / "conf.d"


def test_state_and_runtime_dirs_follow_their_xdg_values(
    home: Path, tmp_path: Path
) -> None:
    env = {
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_RUNTIME_DIR": str(tmp_path / "run"),
    }

    dirs = dirs_from_env(env, home, UID)

    assert dirs.state_dir == tmp_path / "state" / "atc"
    assert dirs.runtime_dir == tmp_path / "run" / "atc"


def test_state_and_runtime_dirs_have_defaults(home: Path) -> None:
    dirs = dirs_from_env({}, home, 1234)

    assert dirs.state_dir == home / ".local" / "state" / "atc"
    assert dirs.runtime_dir == Path("/run/user/1234/atc")


def test_state_and_runtime_values_that_are_not_absolute_are_ignored(
    home: Path,
) -> None:
    env = {"XDG_STATE_HOME": "state", "XDG_RUNTIME_DIR": ""}

    dirs = dirs_from_env(env, home, 7)

    assert dirs.state_dir == home / ".local" / "state" / "atc"
    assert dirs.runtime_dir == Path("/run/user/7/atc")
