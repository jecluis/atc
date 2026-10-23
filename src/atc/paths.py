"""Where config units live, and which references are allowed to reach them."""

import os
from pathlib import Path

from atc.errors import PathError

APP_NAME = "atc"

UNIT_SUFFIX = ".json"


def user_config_root() -> Path:
    """
    Return the directory holding installed config units.

    That is ``$XDG_CONFIG_HOME/atc/conf.d``, falling back to
    ``~/.config/atc/conf.d`` when ``XDG_CONFIG_HOME`` is unset or is
    not an absolute path, as the XDG Base Directory Specification requires.
    """
    configured = os.environ.get("XDG_CONFIG_HOME", "")
    base = (
        Path(configured)
        if configured and Path(configured).is_absolute()
        else Path.home() / ".config"
    )
    return base / APP_NAME / "conf.d"


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
