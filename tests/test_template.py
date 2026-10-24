"""Expanding references in a unit's settings, and in templated assets."""

import pytest

from atc.errors import TemplateError
from atc.jsonval import JsonObject
from atc.template import VarValue, check_syntax, expand, expand_text

V: dict[str, VarValue] = {
    "home": "/h",
    "account.dir": "/h/.claude",
    "peers.dir": ("/p1", "/p2"),
    "peers.files": ("/f",),
    "none": (),
}


def test_scalar_references_are_replaced_anywhere() -> None:
    assert expand({"a": "x{{home}}y"}, V, origin="u") == {"a": "x/hy"}


def test_a_list_reference_expands_a_string_element() -> None:
    got = expand({"a": ["Read({{peers.dir}}/**)", "k"]}, V, origin="u")
    assert got == {"a": ["Read(/p1/**)", "Read(/p2/**)", "k"]}


def test_a_list_reference_expands_an_object_element() -> None:
    got = expand({"f": [{"path": "{{peers.dir}}", "mode": "deny"}]}, V, origin="u")
    assert got == {
        "f": [{"path": "/p1", "mode": "deny"}, {"path": "/p2", "mode": "deny"}]
    }


def test_an_empty_list_removes_the_element() -> None:
    assert expand({"a": ["{{none}}", "k"]}, V, origin="u") == {"a": ["k"]}


def test_nested_arrays_bind_to_the_nearest_element() -> None:
    got = expand({"a": [{"b": ["{{peers.dir}}"], "c": "{{home}}"}]}, V, origin="u")
    assert got == {"a": [{"b": ["/p1", "/p2"], "c": "/h"}]}


def test_two_list_references_in_one_element_are_an_error() -> None:
    with pytest.raises(TemplateError, match="one list reference"):
        _ = expand(
            {"a": [{"x": "{{peers.dir}}", "y": "{{peers.files}}"}]}, V, origin="u"
        )


def test_a_list_reference_outside_an_array_is_an_error() -> None:
    with pytest.raises(TemplateError, match="outside an array"):
        _ = expand({"a": "{{peers.dir}}"}, V, origin="u")


def test_an_unknown_reference_names_origin_and_pointer() -> None:
    with pytest.raises(
        TemplateError, match=r"^u: /a/b/0: unknown reference \{\{nope\}\}"
    ):
        _ = expand({"a": {"b": ["{{nope}}"]}}, V, origin="u")


@pytest.mark.parametrize("text", ["{{ home }}", "{{Home}}", "{{home", "a{{b"])
def test_malformed_references_are_errors(text: str) -> None:
    with pytest.raises(TemplateError, match="malformed"):
        _ = expand({"a": text}, V, origin="u")


def test_keys_are_never_substituted() -> None:
    with pytest.raises(TemplateError, match="key"):
        _ = expand({"{{home}}": 1}, V, origin="u")


def test_substituted_text_is_not_scanned_again() -> None:
    assert expand({"k": "{{a}}"}, {"a": "{{b}}", "b": "z"}, origin="u") == {
        "k": "{{b}}"
    }


def test_non_strings_pass_through() -> None:
    assert expand({"a": [1, True, None, 2.5]}, V, origin="u") == {
        "a": [1, True, None, 2.5]
    }


def test_an_element_removed_by_an_empty_list_is_still_checked() -> None:
    settings: JsonObject = {"a": [{"path": "{{none}}", "mode": "{{typo}}"}]}

    with pytest.raises(
        TemplateError, match=r"^u: /a/0/mode: unknown reference \{\{typo"
    ):
        _ = expand(settings, V, origin="u")


def test_malformed_text_in_a_removed_element_is_an_error() -> None:
    settings: JsonObject = {"a": ["{{none}} {{ home }}"]}

    with pytest.raises(TemplateError, match=r"^u: /a/0: malformed"):
        _ = expand(settings, V, origin="u")


def test_a_key_with_braces_in_a_removed_element_is_an_error() -> None:
    settings: JsonObject = {"a": [{"{{home}}": "{{none}}"}]}

    with pytest.raises(TemplateError, match=r"^u: /a/0/\{\{home\}\}: .*key"):
        _ = expand(settings, V, origin="u")


@pytest.mark.parametrize("text", ["{{ home }}", "{{Home}}", "{{home", "a{{b"])
def test_check_syntax_finds_malformed_references_without_variables(text: str) -> None:
    with pytest.raises(TemplateError, match=r"^u: /a/0/b: malformed reference"):
        check_syntax({"a": [{"b": text}]}, origin="u")


def test_check_syntax_refuses_a_key_with_braces() -> None:
    with pytest.raises(TemplateError, match=r"^u: /a/\{\{x\}\}: object keys"):
        check_syntax({"a": {"{{x}}": 1}}, origin="u")


def test_check_syntax_takes_any_well_formed_reference() -> None:
    check_syntax({"a": ["{{nope}}", {"b": "x{{list.ref}}y"}], "c": 1}, origin="u")


def test_a_file_has_its_references_replaced_on_every_line() -> None:
    text = 'ROOT="{{home}}"\nDIR={{account.dir}}\nno newline at the end'

    got = expand_text(text, V, origin="bin/x.in.sh")

    assert got == 'ROOT="/h"\nDIR=/h/.claude\nno newline at the end'


def test_an_unknown_reference_in_a_file_names_the_file_and_the_line() -> None:
    expected = r"^bin/x\.in\.sh: line 2: unknown reference \{\{nope\}\}"
    with pytest.raises(TemplateError, match=expected):
        _ = expand_text("ok\n{{nope}}\n", V, origin="bin/x.in.sh")


def test_a_list_reference_cannot_be_used_in_a_file() -> None:
    with pytest.raises(TemplateError, match=r"line 1: list reference .* in a file"):
        _ = expand_text("{{peers.dir}}\n", V, origin="f")


@pytest.mark.parametrize("text", ["{{ home }}", "a {{ b", "{{Home}}"])
def test_malformed_references_in_a_file_are_errors(text: str) -> None:
    with pytest.raises(TemplateError, match="malformed reference"):
        _ = expand_text(text, V, origin="f")
