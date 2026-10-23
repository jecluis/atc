"""Shared fixtures for the atc test suite."""

import json
import os
import textwrap
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Protocol

import pytest

from atc.context import BuildContext, load_context
from atc.jsonval import JsonObject
from atc.paths import Dirs, dirs_from_env
from atc.registry import REGISTRY_FILE
from tests.helpers import MINIMAL_REGISTRY, UID, make_source

type UnitWriter = Callable[[str, JsonObject], Path]
type RegistryWriter = Callable[[str], Path]
type Files = Mapping[str, str | JsonObject]


class ConfigWriter(Protocol):
    def __call__(self, files: Files, registry: str = MINIMAL_REGISTRY) -> None: ...


class ContextBuilder(Protocol):
    def __call__(
        self, files: Files, registry: str = MINIMAL_REGISTRY
    ) -> BuildContext: ...


# Variables that change what git or rich do, whatever the machine running
# the tests has set.
_SCRUBBED = frozenset(
    {
        "CLAUDE_CONFIG_DIR",
        "GIT_CONFIG_COUNT",
        "COLORTERM",
        "COLUMNS",
        "FORCE_COLOR",
        "LINES",
        "NO_COLOR",
        "TTY_COMPATIBLE",
        "TTY_INTERACTIVE",
    }
)
_SCRUBBED_PREFIXES = ("ATC_", "GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """
    Point every directory atc and git look at into ``tmp_path``.

    The umask is fixed at 022: with 002, as on Fedora, directories a test makes
    would be group-writable, which the trust rules refuse.
    """
    for name in list(os.environ):
        if name in _SCRUBBED or name.startswith(_SCRUBBED_PREFIXES):
            monkeypatch.delenv(name)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg" / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg" / "state"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "xdg" / "runtime"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@example.com")
    previous = os.umask(0o022)
    yield home
    _ = os.umask(previous)


@pytest.fixture
def home(isolated_env: Path) -> Path:
    return isolated_env


@pytest.fixture
def dirs(home: Path) -> Dirs:
    return dirs_from_env(os.environ, home, UID)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    conf = tmp_path / "conf.d"
    conf.mkdir()
    return conf


@pytest.fixture
def write_unit(root: Path) -> UnitWriter:
    def write(reference: str, document: JsonObject) -> Path:
        path = root / reference
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(json.dumps(document), encoding="utf-8")
        return path

    return write


@pytest.fixture
def write_registry(dirs: Dirs) -> RegistryWriter:
    """Write dedented text as the registry, and return the file's path."""

    def write(text: str) -> Path:
        path = dirs.config_dir / REGISTRY_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(textwrap.dedent(text), encoding="utf-8")
        return path

    return write


@pytest.fixture
def configure(dirs: Dirs, write_registry: RegistryWriter) -> ConfigWriter:
    """
    Set up what atc reads by default: sources in ``conf.d`` and the registry.

    ``files`` is written under ``conf.d`` as :func:`tests.helpers.make_source`
    does, so a path starts with ``conf/`` in the single layout and with the
    source's name in the multi layout.
    """

    def write(files: Files, registry: str = MINIMAL_REGISTRY) -> None:
        _ = make_source(dirs.conf_d, files)
        _ = write_registry(registry)

    return write


@pytest.fixture
def build_context(configure: ConfigWriter, home: Path) -> ContextBuilder:
    """Set up as :func:`configure` does, then load it."""

    def build(files: Files, registry: str = MINIMAL_REGISTRY) -> BuildContext:
        configure(files, registry)
        return load_context(env=os.environ, home=home, uid=UID, conf_d=None)

    return build
