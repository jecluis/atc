"""Import resolution: precedence, de-duplication, cycles, and bad references."""

import re
from pathlib import Path

import pytest

from atc.errors import CycleError, MergeError, PathError, UnitError
from atc.resolve import resolve
from tests.conftest import UnitWriter


def test_an_importing_unit_overrides_what_it_imports(
    root: Path, write_unit: UnitWriter
) -> None:
    _ = write_unit("base.json", {"version": 1, "settings": {"model": "sonnet"}})
    _ = write_unit(
        "top.json",
        {"version": 1, "imports": ["base.json"], "settings": {"model": "opus"}},
    )

    assert resolve(root, ["top.json"]).settings == {"model": "opus"}


def test_later_imports_override_earlier_ones(
    root: Path, write_unit: UnitWriter
) -> None:
    _ = write_unit("first.json", {"version": 1, "settings": {"model": "sonnet"}})
    _ = write_unit("second.json", {"version": 1, "settings": {"model": "opus"}})
    _ = write_unit("top.json", {"version": 1, "imports": ["first.json", "second.json"]})

    assert resolve(root, ["top.json"]).settings == {"model": "opus"}


def test_units_on_the_command_line_merge_left_to_right(
    root: Path, write_unit: UnitWriter
) -> None:
    _ = write_unit("first.json", {"version": 1, "settings": {"model": "sonnet"}})
    _ = write_unit("second.json", {"version": 1, "settings": {"model": "opus"}})

    assert resolve(root, ["first.json", "second.json"]).settings == {"model": "opus"}


def test_a_shared_unit_is_merged_exactly_once(
    root: Path, write_unit: UnitWriter
) -> None:
    # If `shared` were merged again while walking `right`, it would overwrite
    # the value `left` set, and the result would be "shared" instead of "left".
    _ = write_unit("shared.json", {"version": 1, "settings": {"model": "shared"}})
    _ = write_unit(
        "left.json",
        {"version": 1, "imports": ["shared.json"], "settings": {"model": "left"}},
    )
    _ = write_unit("right.json", {"version": 1, "imports": ["shared.json"]})
    _ = write_unit("top.json", {"version": 1, "imports": ["left.json", "right.json"]})

    assert resolve(root, ["top.json"]).settings == {"model": "left"}


def test_naming_an_already_imported_unit_does_not_move_it(
    root: Path, write_unit: UnitWriter
) -> None:
    # `top` imports `base`, so `base` has already contributed by the time the
    # command line reaches it. Contributing once wins over rightmost-wins.
    _ = write_unit("base.json", {"version": 1, "settings": {"model": "sonnet"}})
    _ = write_unit(
        "top.json",
        {"version": 1, "imports": ["base.json"], "settings": {"model": "opus"}},
    )

    resolution = resolve(root, ["top.json", "base.json"])

    assert resolution.settings == {"model": "opus"}
    assert len(resolution.warnings) == 1
    assert "already merged" in resolution.warnings[0]


def test_naming_a_unit_twice_warns_once(root: Path, write_unit: UnitWriter) -> None:
    _ = write_unit("a.json", {"version": 1, "settings": {"model": "opus"}})

    resolution = resolve(root, ["a.json", "a.json"])

    assert resolution.settings == {"model": "opus"}
    assert len(resolution.warnings) == 1


def test_a_profile_carries_no_settings_of_its_own(
    root: Path, write_unit: UnitWriter
) -> None:
    _ = write_unit(
        "rust.json", {"version": 1, "settings": {"permissions": {"allow": ["cargo"]}}}
    )
    _ = write_unit(
        "profile.json",
        {"version": 1, "description": "a profile", "imports": ["rust.json"]},
    )

    assert resolve(root, ["profile.json"]).settings == {
        "permissions": {"allow": ["cargo"]}
    }


def test_an_import_cycle_is_reported(root: Path, write_unit: UnitWriter) -> None:
    _ = write_unit("a.json", {"version": 1, "imports": ["b.json"]})
    _ = write_unit("b.json", {"version": 1, "imports": ["a.json"]})

    with pytest.raises(CycleError, match=re.escape("a.json -> b.json -> a.json")):
        _ = resolve(root, ["a.json"])


def test_a_unit_importing_itself_is_a_cycle(root: Path, write_unit: UnitWriter) -> None:
    _ = write_unit("a.json", {"version": 1, "imports": ["a.json"]})

    with pytest.raises(CycleError):
        _ = resolve(root, ["a.json"])


def test_a_missing_import_names_the_importer(
    root: Path, write_unit: UnitWriter
) -> None:
    _ = write_unit("a.json", {"version": 1, "imports": ["nowhere.json"]})

    with pytest.raises(PathError, match=re.escape("a.json: imports 'nowhere.json'")):
        _ = resolve(root, ["a.json"])


def test_a_missing_top_level_unit_is_reported(root: Path) -> None:
    with pytest.raises(PathError, match="no such unit"):
        _ = resolve(root, ["nowhere.json"])


def test_an_import_escaping_the_root_is_refused(
    root: Path, write_unit: UnitWriter
) -> None:
    _ = write_unit("a.json", {"version": 1, "imports": ["../outside.json"]})

    with pytest.raises(PathError, match="resolves outside"):
        _ = resolve(root, ["a.json"])


def test_a_symlink_escaping_the_root_is_refused(
    root: Path, write_unit: UnitWriter
) -> None:
    outside = root.parent / "outside.json"
    _ = outside.write_text('{"version": 1}', encoding="utf-8")
    (root / "link.json").symlink_to(outside)
    _ = write_unit("a.json", {"version": 1, "imports": ["link.json"]})

    with pytest.raises(PathError, match="resolves outside"):
        _ = resolve(root, ["a.json"])


def test_a_reference_without_the_json_extension_is_refused(root: Path) -> None:
    with pytest.raises(PathError, match=re.escape("must end in .json")):
        _ = resolve(root, ["langs/rust"])


def test_conflicting_types_across_units_name_the_unit(
    root: Path, write_unit: UnitWriter
) -> None:
    _ = write_unit("base.json", {"version": 1, "settings": {"permissions": {}}})
    _ = write_unit(
        "top.json",
        {"version": 1, "imports": ["base.json"], "settings": {"permissions": []}},
    )

    with pytest.raises(MergeError, match=re.escape("top.json: conflicting types")):
        _ = resolve(root, ["top.json"])


def test_unknown_settings_keys_are_rejected(root: Path, write_unit: UnitWriter) -> None:
    _ = write_unit("a.json", {"version": 1, "settings": {"permisions": {}}})

    with pytest.raises(UnitError, match="unknown settings key"):
        _ = resolve(root, ["a.json"])


def test_lax_downgrades_unknown_settings_keys_to_warnings(
    root: Path, write_unit: UnitWriter
) -> None:
    _ = write_unit("a.json", {"version": 1, "settings": {"permisions": {}}})

    resolution = resolve(root, ["a.json"], lax=True)

    assert resolution.settings == {"permisions": {}}
    assert len(resolution.warnings) == 1
    assert "permisions" in resolution.warnings[0]


def test_known_but_unmodelled_keys_pass_through(
    root: Path, write_unit: UnitWriter
) -> None:
    _ = write_unit("a.json", {"version": 1, "settings": {"cleanupPeriodDays": 7}})

    assert resolve(root, ["a.json"]).settings == {"cleanupPeriodDays": 7}


def test_units_nested_in_subdirectories_resolve(
    root: Path, write_unit: UnitWriter
) -> None:
    _ = write_unit("langs/rust.json", {"version": 1, "settings": {"model": "opus"}})
    _ = write_unit("bravo/bar.json", {"version": 1, "imports": ["langs/rust.json"]})

    assert resolve(root, ["bravo/bar.json"]).settings == {"model": "opus"}
