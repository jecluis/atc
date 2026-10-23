"""Reading YAML as JSON values, and refusing everything else."""

from pathlib import Path

import pytest

from atc.errors import RegistryError
from atc.jsonval import JsonValue
from atc.yamlval import load_yaml, parse_yaml


def test_an_unquoted_timestamp_is_refused() -> None:
    with pytest.raises(RegistryError, match="quote"):
        _ = parse_yaml("when: 2026-10-23\n", "t")


def test_no_stays_a_string() -> None:
    assert parse_yaml("flag: no\n", "t") == {"flag": "no"}


def test_every_json_type_is_read() -> None:
    text = "a: [1, 2.5, text, true, null]\nb: {c: {d: ''}}\n"

    assert parse_yaml(text, "t") == {
        "a": [1, 2.5, "text", True, None],
        "b": {"c": {"d": ""}},
    }


def test_the_key_order_of_the_file_is_kept() -> None:
    parsed = parse_yaml("z: 1\na: 2\nm: 3\n", "t")

    assert isinstance(parsed, dict)
    assert list(parsed) == ["z", "a", "m"]


@pytest.mark.parametrize("text", ["a: 1\na: 2\n", "a: 1\nb: {c: 1, c: 2}\n"])
def test_duplicate_keys_are_refused(text: str) -> None:
    with pytest.raises(RegistryError, match="duplicate key"):
        _ = parse_yaml(text, "t")


def test_non_string_keys_are_refused() -> None:
    with pytest.raises(RegistryError, match="key 1 is not a string"):
        _ = parse_yaml("1: x\n", "t")


def test_a_non_string_key_names_where_it_is() -> None:
    with pytest.raises(RegistryError, match=r"t: /a/b: key True"):
        _ = parse_yaml("a:\n  b:\n    true: x\n", "t")


@pytest.mark.parametrize("value", [".nan", ".inf", "-.inf"])
def test_numbers_json_cannot_hold_are_refused(value: str) -> None:
    with pytest.raises(RegistryError, match="quote"):
        _ = parse_yaml(f"x: {value}\n", "t")


def test_a_refused_value_names_where_it_is() -> None:
    with pytest.raises(RegistryError, match=r"t: /a/1/when: .*quote it"):
        _ = parse_yaml("a:\n  - x\n  - when: 2026-10-23T10:00:00\n", "t")


@pytest.mark.parametrize("text", ["x: !!binary aGVsbG8=\n", "x: !!set {a}\n"])
def test_other_yaml_types_are_refused(text: str) -> None:
    with pytest.raises(RegistryError, match="quote"):
        _ = parse_yaml(text, "t")


def test_a_syntax_error_names_the_origin_and_the_line() -> None:
    with pytest.raises(RegistryError, match=r"^t: line 2: ") as caught:
        _ = parse_yaml("a: 1\n  b: 2\n", "t")

    assert "\n" not in str(caught.value)


def test_an_unclosed_bracket_names_where_it_opened() -> None:
    with pytest.raises(RegistryError, match=r"line 3: .*flow sequence, line 2"):
        _ = parse_yaml("a: 1\nb: [1, 2\n", "t")


def test_two_documents_are_refused() -> None:
    with pytest.raises(RegistryError, match=r"^t: "):
        _ = parse_yaml("a: 1\n---\nb: 2\n", "t")


def test_an_alias_to_itself_is_refused() -> None:
    with pytest.raises(RegistryError, match="refers to itself"):
        _ = parse_yaml("a: &x [*x]\n", "t")


def test_an_alias_used_twice_is_read_twice() -> None:
    parsed: JsonValue = parse_yaml("a: &x {b: 1}\nc: *x\n", "t")

    assert parsed == {"a": {"b": 1}, "c": {"b": 1}}


def test_an_empty_document_is_null() -> None:
    assert parse_yaml("", "t") is None


def test_load_yaml_reads_a_file(tmp_path: Path) -> None:
    path = tmp_path / "a.yaml"
    _ = path.write_text("a: [1]\n", encoding="utf-8")

    assert load_yaml(path) == {"a": [1]}


def test_load_yaml_names_the_file_in_an_error(tmp_path: Path) -> None:
    path = tmp_path / "a.yaml"
    _ = path.write_text("a: 1\na: 2\n", encoding="utf-8")

    with pytest.raises(RegistryError, match=f"^{path}: "):
        _ = load_yaml(path)


def test_load_yaml_reports_a_file_that_cannot_be_read(tmp_path: Path) -> None:
    with pytest.raises(RegistryError, match="cannot be read"):
        _ = load_yaml(tmp_path / "absent.yaml")


def test_load_yaml_reports_a_file_that_is_not_text(tmp_path: Path) -> None:
    path = tmp_path / "a.yaml"
    _ = path.write_bytes(b"a: \xff\xfe\n")

    with pytest.raises(RegistryError, match="cannot be read"):
        _ = load_yaml(path)
