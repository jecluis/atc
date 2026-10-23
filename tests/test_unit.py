"""The unit envelope, and how malformed units are reported."""

from pathlib import Path

import pytest

from atc.errors import UnitError
from atc.unit import load_unit
from tests.conftest import UnitWriter


def test_a_minimal_unit_loads(write_unit: UnitWriter) -> None:
    unit = load_unit(write_unit("a.json", {"version": 2}))

    assert unit.settings == {}
    assert unit.imports == ()


def test_description_is_optional_and_ignored(
    write_unit: UnitWriter,
) -> None:
    path = write_unit("a.json", {"version": 2, "description": "notes for humans"})

    assert load_unit(path).settings == {}


def test_a_missing_version_is_an_error(write_unit: UnitWriter) -> None:
    path = write_unit("a.json", {"settings": {}})

    with pytest.raises(UnitError, match="version"):
        _ = load_unit(path)


def test_version_1_is_refused(write_unit: UnitWriter) -> None:
    with pytest.raises(UnitError, match="version"):
        _ = load_unit(write_unit("a.json", {"version": 1}))


def test_an_unknown_envelope_key_is_an_error(
    write_unit: UnitWriter,
) -> None:
    path = write_unit("a.json", {"version": 2, "import": ["a.json"]})

    with pytest.raises(UnitError, match="import"):
        _ = load_unit(path)


def test_imports_must_be_strings(write_unit: UnitWriter) -> None:
    path = write_unit("a.json", {"version": 2, "imports": "langs/rust.json"})

    with pytest.raises(UnitError, match="imports"):
        _ = load_unit(path)


def test_settings_must_be_an_object(write_unit: UnitWriter) -> None:
    path = write_unit("a.json", {"version": 2, "settings": []})

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
        f'{{"version": 2, "settings": {{"maxBackgroundAgents": {constant}}}}}',
        encoding="utf-8",
    )

    with pytest.raises(UnitError, match=constant.lstrip("-")):
        _ = load_unit(path)


def test_duplicate_keys_are_refused(root: Path) -> None:
    path = root / "a.json"
    _ = path.write_text('{"version": 2, "version": 2}', encoding="utf-8")

    with pytest.raises(UnitError, match="duplicate key"):
        _ = load_unit(path)


def test_invalid_json_names_the_file(root: Path) -> None:
    path = root / "a.json"
    _ = path.write_text("{", encoding="utf-8")

    with pytest.raises(UnitError, match="invalid JSON"):
        _ = load_unit(path)


def test_a_unit_that_is_not_utf8_is_refused(root: Path) -> None:
    path = root / "a.json"
    _ = path.write_bytes(b'{"version": 2, "description": "\xff"}')

    with pytest.raises(UnitError, match=f"^{path}: not UTF-8: "):
        _ = load_unit(path)


@pytest.mark.parametrize("number", ["1e400", "-1e400"])
def test_a_number_too_large_for_json_is_refused(root: Path, number: str) -> None:
    path = root / "a.json"
    _ = path.write_text(
        f'{{"version": 2, "settings": {{"cleanupPeriodDays": {number}}}}}',
        encoding="utf-8",
    )

    with pytest.raises(UnitError, match=f"invalid JSON: {number} is too large"):
        _ = load_unit(path)


@pytest.mark.parametrize(
    "document",
    [
        '{"version": 2, "description": "a\\ud800"}',
        '{"version": 2, "settings": {"env": {"\\udfff": "x"}}}',
        '{"version": 2, "imports": ["\\ud800.json"]}',
    ],
)
def test_a_lone_surrogate_is_refused(root: Path, document: str) -> None:
    path = root / "a.json"
    _ = path.write_text(document, encoding="utf-8")

    with pytest.raises(UnitError, match="unpaired surrogate"):
        _ = load_unit(path)


def test_a_unit_nested_too_deeply_is_refused(root: Path) -> None:
    path = root / "a.json"
    _ = path.write_text("[" * 1_000_000, encoding="utf-8")

    with pytest.raises(UnitError, match=f"^{path}: invalid JSON: nested too deeply$"):
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
        {"version": 2, "settings": {"model": "opus", "cleanupPeriodDays": 7}},
    )

    assert load_unit(path).settings == {"model": "opus", "cleanupPeriodDays": 7}
