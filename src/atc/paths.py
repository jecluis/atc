"""Where config units live, and which references are allowed to reach them."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from atc.errors import PathError

APP_NAME = "atc"

UNIT_SUFFIX = ".json"


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


def resolve_unit(root: Path, reference: str) -> Path:
    """
    Turn a unit reference into an absolute path beneath ``root``.

    ``reference`` is written relative to the root of the unit tree, with its
    ``.json`` extension spelled out — for example ``langs/rust.json``.

    Raises:
        PathError: the reference is empty or absolute, lacks the ``.json``
            extension, or resolves outside ``root``, whether through ``..``
            or through a symlink.

    """
    if not reference:
        raise PathError("empty unit reference")
    if not reference.endswith(UNIT_SUFFIX):
        raise PathError(f"'{reference}': unit references must end in {UNIT_SUFFIX}")
    candidate = Path(reference)
    if candidate.is_absolute():
        raise PathError(f"'{reference}': unit references must be relative")

    anchor = root.resolve()
    resolved = (anchor / candidate).resolve()
    if not resolved.is_relative_to(anchor):
        raise PathError(f"'{reference}': resolves outside {anchor}")
    return resolved


def display(root: Path, path: Path) -> str:
    """Render ``path`` relative to ``root`` when possible, for error messages."""
    anchor = root.resolve()
    if path.is_relative_to(anchor):
        return str(path.relative_to(anchor))
    return str(path)
