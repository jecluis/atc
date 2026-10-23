"""Where atc keeps its configuration."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "atc"


@dataclass(frozen=True, slots=True)
class Dirs:
    """
    The directories atc uses.

    ``config_dir`` holds atc's configuration, and ``conf_d`` inside it holds
    the config sources.
    """

    config_dir: Path
    conf_d: Path


def dirs_from_env(env: Mapping[str, str], home: Path) -> Dirs:
    """
    Work out atc's directories from the environment.

    ``config_dir`` is ``$XDG_CONFIG_HOME/atc``, or ``~/.config/atc`` when
    ``XDG_CONFIG_HOME`` is unset, empty or not an absolute path, as the XDG
    Base Directory Specification requires.
    """
    configured = Path(env.get("XDG_CONFIG_HOME", ""))
    base = configured if configured.is_absolute() else home / ".config"
    config_dir = base / APP_NAME
    return Dirs(config_dir=config_dir, conf_d=config_dir / "conf.d")
