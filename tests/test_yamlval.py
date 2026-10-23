"""Reading YAML as JSON values, and refusing everything else."""

from collections.abc import Sized
from pathlib import Path

import pytest

from atc import yamlval
from atc.errors import RegistryError
from atc.jsonval import JsonValue
from atc.yamlval import load_yaml, parse_yaml, set_in_text


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


REGISTRY = """\
# This machine's registry
version: 1   # bump with care

default-account: personal

accounts:
  personal:
    agent: claude
    dir: ~/.claude
    skills: ["a", b]  # chosen by hand
    vars:
      scratch: /x

  # the work account
  work:
    agent: claude
    dir: ~/.config/work  # not under ~/.claude

  # kept for later
  other:
    agent: claude
    dir: ~/.config/other

projects: {}
"""


def test_set_in_text_keeps_comments_and_other_keys() -> None:
    changed = set_in_text(REGISTRY, ["accounts", "personal", "skills"], ["b", "@x/c"])

    assert changed == REGISTRY.replace(
        'skills: ["a", b]  # chosen by hand',
        "skills: [b, '@x/c'] # chosen by hand",
    )
    assert parse_yaml(changed, "t") == parse_yaml(
        REGISTRY.replace('["a", b]', "[b, '@x/c']"), "t"
    )


def test_set_in_text_adds_a_key_after_the_last_key_of_a_mapping() -> None:
    changed = set_in_text(REGISTRY, ["accounts", "work", "skills"], ["a"])

    assert changed == REGISTRY.replace(
        "dir: ~/.config/work  # not under ~/.claude\n",
        "dir: ~/.config/work  # not under ~/.claude\n    skills: [a]\n",
    )


def test_a_key_added_to_the_last_mapping_keeps_the_comment_lines_below_it() -> None:
    text = "a:\n  b:\n    c: 1\n\n    # about d\nd: 2\n"

    changed = set_in_text(text, ["a", "b", "e"], [])

    assert changed == "a:\n  b:\n    c: 1\n    e: []\n\n    # about d\nd: 2\n"


def test_a_key_added_below_a_mapping_goes_after_its_last_entry() -> None:
    text = "a:\n  b:\n    c: 1  # one\n\n# about d\nd: 2\n"

    changed = set_in_text(text, ["a", "e"], True)

    assert changed == "a:\n  b:\n    c: 1  # one\n  e: true\n\n# about d\nd: 2\n"


def test_replacing_a_block_list_keeps_the_comment_lines_after_it() -> None:
    text = "a:\n  skills:\n    - x\n    - y\n\n  # about b\n  b: 1\n"

    changed = set_in_text(text, ["a", "skills"], ["z"])

    assert changed == "a:\n  skills: [z]\n\n  # about b\n  b: 1\n"


def test_set_in_text_creates_the_mappings_on_the_way() -> None:
    changed = set_in_text("version: 1\n", ["accounts", "work", "skills"], ["a"])

    assert changed == "version: 1\naccounts:\n  work:\n    skills: [a]\n"


def test_set_in_text_writes_every_json_type() -> None:
    value: JsonValue = {"n": None, "t": True, "i": 3, "f": 2.5, "s": "no", "l": [1]}

    changed = set_in_text("a: 1\n", ["b"], value)

    assert parse_yaml(changed, "t") == {"a": 1, "b": value}


def test_set_in_text_leaves_the_formatting_of_other_entries_alone() -> None:
    text = "a:\n  - 'x'\n  - \"y\"\nb: [1, 2]\nc: {d: 1}\n"

    assert set_in_text(text, ["e"], 1) == text + "e: 1\n"


def test_set_in_text_starts_a_document_from_nothing() -> None:
    assert set_in_text("", ["a", "b"], [1]) == "a:\n  b: [1]\n"


def test_set_in_text_does_not_wrap_a_long_list() -> None:
    names = [f"skill-number-{n}" for n in range(12)]

    changed = set_in_text("a: 1\n", ["skills"], [*names])

    assert changed.count("\n") == 2
    assert parse_yaml(changed, "t") == {"a": 1, "skills": names}


@pytest.mark.parametrize(
    ("text", "keys", "message"),
    [
        ("a: 1\n", ["a", "b"], "/a: is not a mapping"),
        ("a: [1]\n", ["a", "b", "c"], "/a: is not a mapping"),
        ("- 1\n", ["a"], "must be a mapping"),
        ("a: 1\na: 2\n", ["b"], "duplicate key"),
        ("a: [1\n", ["b"], "line"),
    ],
)
def test_set_in_text_refuses_what_it_cannot_edit(
    text: str, keys: list[str], message: str
) -> None:
    with pytest.raises(RegistryError, match=message):
        _ = set_in_text(text, keys, 1)


def test_set_in_text_needs_a_key() -> None:
    with pytest.raises(ValueError, match="at least one key"):
        _ = set_in_text("a: 1\n", [], 1)


def test_set_in_text_adds_to_a_mapping_written_in_flow_style() -> None:
    text = "accounts:\n  work: {agent: claude, dir: ~/w}  # mine\n"

    changed = set_in_text(text, ["accounts", "work", "skills"], ["a"])

    assert changed == (
        "accounts:\n  work: {agent: claude, dir: ~/w, skills: [a]} # mine\n"
    )


def test_set_in_text_fills_a_key_that_has_no_value() -> None:
    changed = set_in_text("a:\nb: 1\n", ["a", "c"], 2)

    assert changed == "a:\n  c: 2\nb: 1\n"


def test_replacing_a_block_list_that_has_a_key_comment_keeps_the_lines_after_it() -> (
    None
):
    text = (
        "accounts:\n  a:\n    agent: claude\n    skills:  # chosen\n"
        "      - x  # first\n      - y  # last\n\n  # next\n  b:\n    agent: claude\n"
    )

    changed = set_in_text(text, ["accounts", "a", "skills"], ["z"])

    assert changed == (
        "accounts:\n  a:\n    agent: claude\n    skills: [z] # chosen\n"
        "\n  # next\n  b:\n    agent: claude\n"
    )


def test_a_commented_block_list_that_is_the_last_key_keeps_the_lines_after_it() -> None:
    text = (
        "accounts:\n  a:\n    agent: claude\n    skills:  # chosen\n"
        "      - x  # first\n      - y  # last\n\n# next\nb: 1\n"
    )

    changed = set_in_text(text, ["accounts", "a", "skills"], ["z"])

    assert changed == (
        "accounts:\n  a:\n    agent: claude\n    skills: [z] # chosen\n\n# next\nb: 1\n"
    )


def test_the_comments_of_a_replaced_list_items_are_the_only_ones_dropped() -> None:
    text = (
        "# top\na:\n  - x  # first\n  # between\n  - y  # last\n\n# next\nb: 1  # one\n"
    )

    changed = set_in_text(text, ["a"], ["z"])

    assert changed == "# top\na: [z]\n\n# next\nb: 1  # one\n"


def test_set_in_text_refuses_an_edit_that_would_lose_a_comment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = "a:\n  skills:\n    - x\n    - y\n\n  # about b\n  b: 1\n"

    def no_ending(_holder: Sized, _key: str) -> None:
        """Stand for a ruamel that no longer shows where an entry's comments are."""

    monkeypatch.setattr(yamlval, "_ending", no_ending)

    with pytest.raises(RegistryError, match=r"/a/skills: .*lose.* # about b"):
        _ = set_in_text(text, ["a", "skills"], ["z"])


def test_a_comment_that_looks_like_one_inside_quotes_is_not_counted() -> None:
    text = "a:\n  - 'x #not'\n  - \"y #nor\"  # real\nb: 1\n"

    assert set_in_text(text, ["a"], ["z"]) == "a: [z]\nb: 1\n"


def test_mappings_created_on_the_way_come_before_the_lines_that_followed() -> None:
    text = "accounts:\n  work:\n    agent: claude\n\n# the end\n"

    changed = set_in_text(text, ["accounts", "new", "skills"], ["q"])

    assert changed == (
        "accounts:\n  work:\n    agent: claude\n  new:\n    skills: [q]\n\n# the end\n"
    )


def test_a_mapping_created_at_the_end_of_the_document_keeps_the_last_comment_last() -> (
    None
):
    changed = set_in_text("a: 1  # one\n\n# fin\n", ["b", "c"], 2)

    assert changed == "a: 1  # one\nb:\n  c: 2\n\n# fin\n"
