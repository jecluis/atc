"""Reading YAML as JSON values, and refusing everything else."""

import time
from collections.abc import Sized
from pathlib import Path

import pytest

from atc import yamlval
from atc.errors import RegistryError
from atc.jsonval import JsonValue
from atc.yamlval import (
    delete_in_text,
    delete_item_in_text,
    load_yaml,
    parse_yaml,
    set_in_text,
)


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
    with pytest.raises(RegistryError, match="aliases are not supported"):
        _ = parse_yaml("a: &x [*x]\n", "t")


@pytest.mark.parametrize(
    "text", ["a: &x {b: 1}\nc: *x\n", "a: &x [1]\nc: [*x]\n"], ids=["map", "list"]
)
def test_an_alias_is_refused_where_it_is_used(text: str) -> None:
    with pytest.raises(RegistryError, match=r"^t: /c(/0)?: aliases are not supported"):
        _ = parse_yaml(text, "t")


@pytest.mark.parametrize(
    "text",
    [
        "base: &b {x: 1}\nother:\n  <<: *b\n  y: 2\n",
        "other:\n  <<: {x: 1}\n  y: 2\n",
        "base: &b {x: 1}\nother: {<<: [*b], y: 2}\n",
    ],
    ids=["alias", "inline", "list"],
)
def test_a_merge_key_is_refused(text: str) -> None:
    with pytest.raises(RegistryError, match=r"^t: line \d+: merge keys \(<<\) are not"):
        _ = parse_yaml(text, "t")


def test_a_quoted_merge_key_is_an_ordinary_key() -> None:
    assert parse_yaml("other:\n  '<<': 1\n", "t") == {"other": {"<<": 1}}


def test_an_alias_bomb_is_refused_at_once() -> None:
    lines = ["a: &a [x, x, x, x, x, x, x, x, x]"]
    for this, previous in zip("bcdefghij", "abcdefghi", strict=True):
        aliases = ", ".join([f"*{previous}"] * 9)
        lines.append(f"{this}: &{this} [{aliases}]")
    started = time.monotonic()

    with pytest.raises(RegistryError, match=r"^t: /b/0: aliases are not supported"):
        _ = parse_yaml("\n".join(lines) + "\n", "t")

    assert time.monotonic() - started < 5


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


PROJECTS = """\
# the registry
version: 1

projects:
  # the first
  a:  # about a
    repo: /a  # a's repo

    # a's account
    account: x

  # the second
  b: {repo: /b}  # b is flat

  # the third
  c:
    repo: /c

# the end
other: 1  # one
"""


def test_delete_in_text_drops_the_comment_block_directly_above_the_entry() -> None:
    changed = delete_in_text(PROJECTS, ["projects", "b"])

    assert changed == PROJECTS.replace(
        "  # the second\n  b: {repo: /b}  # b is flat\n\n", ""
    )


def test_delete_in_text_gives_the_last_entrys_following_lines_to_the_one_before() -> (
    None
):
    changed = delete_in_text(PROJECTS, ["projects", "c"])

    assert changed == PROJECTS.replace("  # the third\n  c:\n    repo: /c\n\n", "")


def test_delete_in_text_drops_the_comments_inside_the_entry_and_on_its_key() -> None:
    changed = delete_in_text(PROJECTS, ["projects", "a"])

    gone = (
        "  # the first\n  a:  # about a\n    repo: /a  # a's repo\n\n"
        "    # a's account\n    account: x\n\n"
    )
    assert changed == PROJECTS.replace(gone, "")
    assert parse_yaml(changed, "t") == {
        "version": 1,
        "projects": {"b": {"repo": "/b"}, "c": {"repo": "/c"}},
        "other": 1,
    }


@pytest.mark.parametrize(
    ("text", "keys", "expected"),
    [
        (  # the lines above the entry are the entry's, however many
            "p:\n  a: 1\n  # about b\n  # more\n  b: 2\n  c: 3\n",
            ["p", "b"],
            "p:\n  a: 1\n  c: 3\n",
        ),
        (  # what is above the next entry is not
            "p:\n  a: 1  # one\n  # about b\n  b: 2\n  # about c\n  c: 3\n",
            ["p", "b"],
            "p:\n  a: 1  # one\n  # about c\n  c: 3\n",
        ),
        (  # a blank line cuts the comment off from the entry
            "p:\n  a: 1\n\n  # about b\n\n  b: 2\n  c: 3\n",
            ["p", "b"],
            "p:\n  a: 1\n\n  # about b\n\n  c: 3\n",
        ),
        (  # the first entry has them in the mapping's own comments
            "p:\n  # about a\n  a: 1\n  # about b\n  b: 2\n",
            ["p", "a"],
            "p:\n  # about b\n  b: 2\n",
        ),
        (
            "p:\n  # first\n\n  # about a\n  a: 1\n  b: 2\n",
            ["p", "a"],
            "p:\n  # first\n\n  b: 2\n",
        ),
        (  # a deeper comment belongs to the entry before
            (
                "p:\n  a:\n    repo: /a\n    # about a's repo\n  b:\n    repo: /b\n"
                "  c: 1\n"
            ),
            ["p", "b"],
            "p:\n  a:\n    repo: /a\n    # about a's repo\n  c: 1\n",
        ),
        (  # so does one after a list item
            "p:\n  a:\n    xs:\n      - 1\n      # inner\n  b: 1\n",
            ["p", "b"],
            "p:\n  a:\n    xs:\n      - 1\n      # inner\n",
        ),
        (  # the block stops at the first line that is deeper
            "p:\n  a:\n    repo: /a\n    # deep\n  # about b\n  b: 2\n",
            ["p", "b"],
            "p:\n  a:\n    repo: /a\n    # deep\n",
        ),
        (  # and at one that is shallower, also for the first entry
            "p:\n# shallow\n  a: 1\n  b: 2\n",
            ["p", "a"],
            "p:\n# shallow\n  b: 2\n",
        ),
        (  # the first entry's lines are kept by ruamel one by one
            "p:\n  # one\n  # two\n  a: 1\n  b: 2\n",
            ["p", "a"],
            "p:\n  b: 2\n",
        ),
        (
            "p:\n  # one\n\n  # two\n  a: 1\n  b: 2\n",
            ["p", "a"],
            "p:\n  # one\n\n  b: 2\n",
        ),
        (  # the only entry
            "p:\n  # about a\n  a: 1\n\nq: 1\n",
            ["p", "a"],
            "p: {}\n\nq: 1\n",
        ),
        (  # a top-level entry
            "# top\n\n# about a\na: 1  # one\n# about b\nb: 2\n",
            ["a"],
            "# top\n\n# about b\nb: 2\n",
        ),
    ],
)
def test_delete_in_text_takes_only_the_comments_that_describe_the_entry(
    text: str, keys: list[str], expected: str
) -> None:
    assert delete_in_text(text, keys) == expected


def test_delete_in_text_keeps_the_lines_after_the_first_entry() -> None:
    text = "projects:\n  a:\n    repo: /a\n\n  # next\n  b: 1\n"

    assert delete_in_text(text, ["projects", "a"]) == "projects:\n  # next\n  b: 1\n"


def test_deleting_the_only_entry_leaves_an_empty_mapping_and_the_lines_after_it() -> (
    None
):
    text = "projects:  # mine\n  a:\n    repo: /a  # one\n\n# the end\nb: 1\n"

    changed = delete_in_text(text, ["projects", "a"])

    assert changed == "projects: {} # mine\n\n# the end\nb: 1\n"


def test_deleting_the_only_entry_keeps_a_comment_a_blank_line_cuts_off_from_it() -> (
    None
):
    text = "projects:\n  # none\n\n  a:\n    repo: /a\n\n# the end\nb: 1\n"

    changed = delete_in_text(text, ["projects", "a"])

    assert changed == "projects: {}\n# none\n\n# the end\nb: 1\n"


def test_delete_in_text_refuses_an_edit_that_would_lose_a_comment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_ending(_holder: Sized, _key: str) -> None:
        """Stand for a ruamel that no longer shows where an entry's comments are."""

    monkeypatch.setattr(yamlval, "_ending", no_ending)

    with pytest.raises(RegistryError, match=r"/projects/b: .*lose.* # the third"):
        _ = delete_in_text(PROJECTS, ["projects", "b"])


@pytest.mark.parametrize(
    ("keys", "message"),
    [
        (["projects", "ghost"], "/projects/ghost: no such entry"),
        (["ghost", "a"], "/ghost/a: no such entry"),
        (["version", "a"], "/version: is not a mapping"),
    ],
)
def test_delete_in_text_refuses_what_is_not_there(
    keys: list[str], message: str
) -> None:
    with pytest.raises(RegistryError, match=message):
        _ = delete_in_text(PROJECTS, keys)


def test_delete_in_text_needs_a_key() -> None:
    with pytest.raises(ValueError, match="at least one key"):
        _ = delete_in_text("a: 1\n", [])


def test_set_in_text_fills_an_empty_mapping_in_block_style() -> None:
    text = "projects: {}\n\n# next section\nmaint: 1\n"

    changed = set_in_text(text, ["projects", "a"], 1)

    assert changed == "projects:\n  a: 1\n\n# next section\nmaint: 1\n"


def test_the_lines_after_an_empty_mapping_stay_below_the_entry_it_gets() -> None:
    text = "x:\n  projects: {}  # none yet\n\n  # next\n  maint: 1\n"

    changed = set_in_text(text, ["x", "projects", "a"], 1)

    assert changed == (
        "x:\n  projects:     # none yet\n    a: 1\n\n  # next\n  maint: 1\n"
    )


def test_deleting_the_first_entry_keeps_the_lines_after_it_below_the_key_comment() -> (
    None
):
    text = "projects:  # mine\n  a:\n    repo: /a\n\n  # next\n  b: 1\n"

    changed = delete_in_text(text, ["projects", "a"])

    assert changed == "projects:  # mine\n  # next\n  b: 1\n"


SPACED = (
    "projects:\n  a:\n    repo: /a\n\n  b:\n    repo: /b\n\n  c:\n    repo: /c\n\n"
    "other: 1\n"
)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("a", "projects:\n  b:\n    repo: /b\n\n  c:\n    repo: /c\n\nother: 1\n"),
        ("b", "projects:\n  a:\n    repo: /a\n\n  c:\n    repo: /c\n\nother: 1\n"),
        ("c", "projects:\n  a:\n    repo: /a\n\n  b:\n    repo: /b\n\nother: 1\n"),
    ],
)
def test_deleting_an_entry_leaves_one_blank_line_where_there_were_two(
    name: str, expected: str
) -> None:
    assert delete_in_text(SPACED, ["projects", name]) == expected


def test_delete_in_text_puts_the_lines_after_a_block_list_below_it() -> None:
    text = "p:\n  a:\n    xs:\n      - 1  # one\n      - 2\n  b: 1\n\n# the end\nq: 1\n"

    changed = delete_in_text(text, ["p", "b"])

    assert (
        changed == "p:\n  a:\n    xs:\n      - 1  # one\n      - 2\n\n# the end\nq: 1\n"
    )


ALLOWED = """\
projects:
  bar:
    accounts:
      # who may work on it
      - personal  # mine
      # the one for work
      - work  # job
      - spare
    unit: x
# the end
"""
ACCOUNTS = ["projects", "bar", "accounts"]


def test_delete_item_in_text_keeps_the_other_items_comments() -> None:
    changed = delete_item_in_text(ALLOWED, ACCOUNTS, "work")

    assert changed == ALLOWED.replace(
        "      # the one for work\n      - work  # job\n", ""
    )


def test_delete_item_in_text_gives_a_last_items_following_lines_to_the_one_before() -> (
    None
):
    text = "accounts:\n  - personal  # mine\n  - work  # job\n\n# the end\n"

    changed = delete_item_in_text(text, ["accounts"], "work")

    assert changed == "accounts:\n  - personal  # mine\n\n# the end\n"


def test_delete_item_in_text_puts_the_first_items_following_lines_before_the_next() -> (
    None
):
    changed = delete_item_in_text(ALLOWED, ACCOUNTS, "personal")

    assert changed == ALLOWED.replace("      - personal  # mine\n", "")


def test_delete_item_in_text_keeps_a_flow_list_in_flow_style() -> None:
    text = "projects:\n  bar:\n    accounts: [personal, work]  # two\n    unit: x\n"

    changed = delete_item_in_text(text, ACCOUNTS, "work")

    assert parse_yaml(changed, "t") == {
        "projects": {"bar": {"accounts": ["personal"], "unit": "x"}}
    }
    assert "accounts: [personal]" in changed
    assert "# two" in changed


@pytest.mark.parametrize(
    ("text", "item", "message"),
    [
        ("accounts: [a, b]\n", "c", "/accounts: has no item 'c'"),
        ("accounts: [a, a]\n", "a", "/accounts: has no item but 'a'"),
        ("accounts: {a: 1}\n", "a", "/accounts: is not a list"),
        ("other: 1\n", "a", "/accounts: is not a list"),
    ],
)
def test_delete_item_in_text_refuses_what_it_cannot_remove(
    text: str, item: str, message: str
) -> None:
    with pytest.raises(RegistryError, match=f"^{message}$"):
        _ = delete_item_in_text(text, ["accounts"], item)
