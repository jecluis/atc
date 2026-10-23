"""Everything a layer build needs, loaded once."""

import sys
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from atc.paths import Dirs, dirs_from_env
from atc.registry import REGISTRY_FILE, Registry, load_registry
from atc.sources import Sources, discover


@dataclass(frozen=True, slots=True)
class BuildContext:
    """
    The registry and the config sources, with the directories they came from.

    ``install_dir`` is the Python environment atc runs from, and ``python_dir``
    the Python installation that environment is built on.
    """

    registry: Registry
    sources: Sources
    dirs: Dirs
    home: Path
    install_dir: Path
    python_dir: Path


def current_install_dir() -> Path:
    """Return the Python environment this process runs from."""
    return Path(sys.prefix)


def current_python_dir() -> Path:
    """
    Return the Python installation this process's environment is built on.

    Symbolic links are resolved. Outside a virtual environment it is the
    environment itself.
    """
    return Path(sys.base_prefix).resolve()


def load_context(
    *, env: Mapping[str, str], home: Path, uid: int, conf_d: Path | None
) -> BuildContext:
    """
    Load the registry and discover the config sources.

    Directories come from ``env`` and ``home``, except that ``conf_d``, when
    given, replaces the directory the sources are read from. ``uid`` is the
    user the sources must belong to.

    Raises:
        RegistryError: the registry is missing or invalid.
        SourceError: the sources are missing, ambiguous, or not trusted.

    """
    dirs = dirs_from_env(env, home, uid)
    if conf_d is not None:
        dirs = replace(dirs, conf_d=conf_d)
    sources = discover(dirs.conf_d, uid=uid)
    registry = load_registry(dirs.config_dir / REGISTRY_FILE, home)
    return BuildContext(
        registry=registry,
        sources=sources,
        dirs=dirs,
        home=home,
        install_dir=current_install_dir(),
        python_dir=current_python_dir(),
    )
