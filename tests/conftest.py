"""Shared fixtures for the atc test suite."""

import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from atc.jsonval import JsonObject
from atc.paths import Dirs, dirs_from_env

type UnitWriter = Callable[[str, JsonObject], Path]

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
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point every directory atc and git look at into ``tmp_path``."""
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
    return home


@pytest.fixture
def home(isolated_env: Path) -> Path:
    return isolated_env


@pytest.fixture
def dirs(home: Path) -> Dirs:
    return dirs_from_env(os.environ, home)


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
