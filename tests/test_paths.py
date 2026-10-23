"""Where atc keeps its configuration."""

from pathlib import Path

from atc.paths import dirs_from_env


def test_an_xdg_value_that_is_not_absolute_is_ignored(home: Path) -> None:
    dirs = dirs_from_env({"XDG_CONFIG_HOME": "relative/config"}, home)
    assert dirs.config_dir == home / ".config" / "atc"


def test_an_absolute_xdg_value_is_used(home: Path, tmp_path: Path) -> None:
    dirs = dirs_from_env({"XDG_CONFIG_HOME": str(tmp_path / "cfg")}, home)
    assert dirs.config_dir == tmp_path / "cfg" / "atc"


def test_an_empty_xdg_value_is_ignored(home: Path) -> None:
    dirs = dirs_from_env({"XDG_CONFIG_HOME": ""}, home)
    assert dirs.config_dir == home / ".config" / "atc"


def test_conf_d_sits_under_the_config_dir(home: Path) -> None:
    dirs = dirs_from_env({}, home)
    assert dirs.conf_d == dirs.config_dir / "conf.d"
