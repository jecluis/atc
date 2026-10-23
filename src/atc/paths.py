"""Where atc keeps its configuration, state and locks."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "atc"


@dataclass(frozen=True, slots=True)
class Dirs:
    """
    The directories atc uses.

    ``config_dir`` holds atc's configuration, and ``conf_d`` inside it holds
    the config sources. ``state_dir`` holds what atc remembers between runs,
    and ``runtime_dir`` what only matters while it is running.
    """

    config_dir: Path
    conf_d: Path
    state_dir: Path
    runtime_dir: Path


def dirs_from_env(env: Mapping[str, str], home: Path, uid: int) -> Dirs:
    """
    Work out atc's directories from the environment.

    Each directory is ``atc`` inside the XDG one. ``$XDG_CONFIG_HOME`` falls
    back to ``~/.config``, ``$XDG_STATE_HOME`` to ``~/.local/state`` and
    ``$XDG_RUNTIME_DIR`` to ``/run/user/<uid>``. A variable that is unset,
    empty or not an absolute path is ignored, as the XDG Base Directory
    Specification requires.
    """
    config_dir = _xdg(env, "XDG_CONFIG_HOME", home / ".config") / APP_NAME
    return Dirs(
        config_dir=config_dir,
        conf_d=config_dir / "conf.d",
        state_dir=_xdg(env, "XDG_STATE_HOME", home / ".local" / "state") / APP_NAME,
        runtime_dir=_xdg(env, "XDG_RUNTIME_DIR", Path("/run/user") / str(uid))
        / APP_NAME,
    )


def _xdg(env: Mapping[str, str], variable: str, fallback: Path) -> Path:
    """Return the absolute path in ``env[variable]``, or ``fallback``."""
    configured = Path(env.get(variable, ""))
    return configured if configured.is_absolute() else fallback
