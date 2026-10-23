"""The unit envelope, and how malformed units are reported."""

from pathlib import Path

import pytest

from atc.errors import UnitError
from atc.unit import load_unit
from tests.conftest import UnitWriter


def test_a_minimal_unit_loads(write_unit: UnitWriter) -> None:
    path = write_unit("a.json", {"version": 1})

    unit = load_unit(path)

    assert unit.imports == ()
    assert unit.settings == {}


def test_description_is_optional_and_ignored(
    write_unit: UnitWriter,
) -> None:
    path = write_unit("a.json", {"version": 1, "description": "notes for humans"})

    assert load_unit(path).settings == {}


def test_a_missing_version_is_an_error(write_unit: UnitWriter) -> None:
    path = write_unit("a.json", {"settings": {}})

    with pytest.raises(UnitError, match="version"):
        _ = load_unit(path)


def test_an_unsupported_version_is_an_error(write_unit: UnitWriter) -> None:
    path = write_unit("a.json", {"version": 2})

    with pytest.raises(UnitError, match="version"):
        _ = load_unit(path)


def test_an_unknown_envelope_key_is_an_error(
    write_unit: UnitWriter,
) -> None:
    path = write_unit("a.json", {"version": 1, "import": ["a.json"]})

    with pytest.raises(UnitError, match="import"):
        _ = load_unit(path)


def test_imports_must_be_strings(write_unit: UnitWriter) -> None:
    path = write_unit("a.json", {"version": 1, "imports": "langs/rust.json"})

    with pytest.raises(UnitError, match="imports"):
        _ = load_unit(path)


def test_settings_must_be_an_object(write_unit: UnitWriter) -> None:
    path = write_unit("a.json", {"version": 1, "settings": []})

    with pytest.raises(UnitError, match="settings"):
        _ = load_unit(path)


def test_a_unit_must_be_an_object(root: Path) -> None:
    path = root / "a.json"
    _ = path.write_text("[]", encoding="utf-8")

    with pytest.raises(UnitError, match="must be a JSON object"):
        _ = load_unit(path)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_non_standard_json_constants_are_refused(root: Path, constant: str) -> None:
    path = root / "a.json"
    _ = path.write_text(
        f'{{"version": 1, "settings": {{"maxBackgroundAgents": {constant}}}}}',
        encoding="utf-8",
    )

    with pytest.raises(UnitError, match=constant.lstrip("-")):
        _ = load_unit(path)


def test_duplicate_keys_are_refused(root: Path) -> None:
    path = root / "a.json"
    _ = path.write_text('{"version": 1, "version": 1}', encoding="utf-8")

    with pytest.raises(UnitError, match="duplicate key"):
        _ = load_unit(path)


def test_invalid_json_names_the_file(root: Path) -> None:
    path = root / "a.json"
    _ = path.write_text("{", encoding="utf-8")

    with pytest.raises(UnitError, match="invalid JSON"):
        _ = load_unit(path)


def test_an_unreadable_unit_is_reported(root: Path) -> None:
    with pytest.raises(UnitError, match="cannot be read"):
        _ = load_unit(root / "absent.json")


def test_settings_are_taken_verbatim_from_the_file(
    write_unit: UnitWriter,
) -> None:
    # Validation must not invent defaults or reorder what reaches the output.
    path = write_unit(
        "a.json",
        {"version": 1, "settings": {"model": "opus", "cleanupPeriodDays": 7}},
    )

    assert load_unit(path).settings == {"model": "opus", "cleanupPeriodDays": 7}
