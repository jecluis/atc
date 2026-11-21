"""The value merge rules."""

import pytest

from atc.errors import MergeError
from atc.jsonval import JsonObject
from atc.merge import keep_additions, merge_objects


def test_objects_merge_recursively() -> None:
    base: JsonObject = {"permissions": {"allow": ["a"], "deny": ["x"]}}
    overlay: JsonObject = {"permissions": {"ask": ["b"]}}

    assert merge_objects(base, overlay) == {
        "permissions": {"allow": ["a"], "deny": ["x"], "ask": ["b"]}
    }


def test_keys_present_on_one_side_are_carried_through() -> None:
    assert merge_objects({"model": "opus"}, {"theme": "dark"}) == {
        "model": "opus",
        "theme": "dark",
    }


def test_arrays_concatenate_and_drop_duplicates() -> None:
    base: JsonObject = {"allow": ["a", "b"]}
    overlay: JsonObject = {"allow": ["b", "c"]}

    assert merge_objects(base, overlay) == {"allow": ["a", "b", "c"]}


def test_array_order_is_first_seen() -> None:
    base: JsonObject = {"allow": ["z", "a"]}
    overlay: JsonObject = {"allow": ["m", "z"]}

    assert merge_objects(base, overlay) == {"allow": ["z", "a", "m"]}


def test_structurally_equal_objects_in_arrays_collapse() -> None:
    base: JsonObject = {"hooks": [{"matcher": "Bash", "command": "lint"}]}
    overlay: JsonObject = {"hooks": [{"command": "lint", "matcher": "Bash"}]}

    assert merge_objects(base, overlay) == {
        "hooks": [{"matcher": "Bash", "command": "lint"}]
    }


def test_scalars_of_the_same_type_take_the_overlay() -> None:
    assert merge_objects({"model": "sonnet"}, {"model": "opus"}) == {"model": "opus"}


def test_integers_and_floats_are_one_numeric_type() -> None:
    assert merge_objects({"cleanupPeriodDays": 30}, {"cleanupPeriodDays": 7.5}) == {
        "cleanupPeriodDays": 7.5
    }


def test_differing_types_are_an_error() -> None:
    with pytest.raises(MergeError, match="/permissions"):
        _ = merge_objects({"permissions": {}}, {"permissions": []})


def test_booleans_do_not_merge_with_numbers() -> None:
    with pytest.raises(MergeError, match="boolean in one unit, number in another"):
        _ = merge_objects({"tui": True}, {"tui": 1})


def test_null_does_not_delete() -> None:
    with pytest.raises(MergeError, match="string in one unit, null in another"):
        _ = merge_objects({"model": "opus"}, {"model": None})


def test_nested_conflicts_report_their_pointer() -> None:
    base: JsonObject = {"permissions": {"allow": ["a"]}}
    overlay: JsonObject = {"permissions": {"allow": "a"}}

    with pytest.raises(MergeError, match="/permissions/allow"):
        _ = merge_objects(base, overlay)


def test_inputs_are_not_mutated() -> None:
    base: JsonObject = {"permissions": {"allow": ["a"]}}
    overlay: JsonObject = {"permissions": {"allow": ["b"]}}

    _ = merge_objects(base, overlay)

    assert base == {"permissions": {"allow": ["a"]}}
    assert overlay == {"permissions": {"allow": ["b"]}}


def test_keep_additions_takes_what_the_units_set() -> None:
    base: JsonObject = {"model": "opus", "sandbox": {"enabled": True}}
    live: JsonObject = {"model": "haiku", "sandbox": {"enabled": False}}
    new: JsonObject = {"model": "opus", "sandbox": {"enabled": True}}

    kept = keep_additions(base, live, new)

    assert kept.settings == new
    assert kept.reset == ("/model", "/sandbox/enabled")
    assert kept.kept == ()


def test_keep_additions_keeps_keys_only_the_live_file_has() -> None:
    base: JsonObject = {"model": "opus"}
    live: JsonObject = {"model": "opus", "advisorModel": "x", "env": {"A": "1"}}
    new: JsonObject = {"model": "sonnet", "env": {"B": "2"}}

    kept = keep_additions(base, live, new)

    assert kept.settings == {
        "model": "sonnet",
        "advisorModel": "x",
        "env": {"B": "2", "A": "1"},
    }
    assert kept.kept == ("/advisorModel", "/env/A")
    assert kept.reset == ()


def test_keep_additions_drops_what_the_units_removed() -> None:
    base: JsonObject = {"model": "opus", "theme": "dark"}
    live: JsonObject = {"model": "opus", "theme": "light"}
    new: JsonObject = {"model": "opus"}

    kept = keep_additions(base, live, new)

    assert kept.settings == {"model": "opus"}
    assert kept.reset == ("/theme",)


def test_keep_additions_keeps_list_entries_the_live_file_added() -> None:
    base: JsonObject = {"permissions": {"allow": ["a", "old"], "deny": ["x", "y"]}}
    live: JsonObject = {
        "permissions": {"allow": ["a", "old", "WebFetch(domain:b.org)"], "deny": ["x"]}
    }
    new: JsonObject = {"permissions": {"allow": ["a", "c"], "deny": ["x", "y"]}}

    kept = keep_additions(base, live, new)

    assert kept.settings == {
        "permissions": {
            "allow": ["a", "c", "WebFetch(domain:b.org)"],
            "deny": ["x", "y"],
        }
    }
    assert kept.kept == ("/permissions/allow",)
    assert kept.reset == ("/permissions/deny",)


def test_keep_additions_gives_the_units_value_where_the_types_differ() -> None:
    base: JsonObject = {"env": {"A": "1"}, "allow": ["a"]}
    live: JsonObject = {"env": "x", "allow": {"b": 1}}
    new: JsonObject = {"env": {"A": "1"}, "allow": ["a"]}

    kept = keep_additions(base, live, new)

    assert kept.settings == new
    assert kept.reset == ("/allow", "/env")


def test_keep_additions_of_an_unchanged_file_is_the_new_settings() -> None:
    base: JsonObject = {"model": "opus", "allow": ["a"]}
    new: JsonObject = {"model": "sonnet", "allow": ["b"]}

    kept = keep_additions(base, base, new)

    assert kept.settings == new
    assert (kept.kept, kept.reset) == ((), ())
