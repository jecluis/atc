"""Shared fixtures for the atc test suite."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from atc.jsonval import JsonObject

type UnitWriter = Callable[[str, JsonObject], Path]


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
