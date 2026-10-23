"""Import resolution: precedence, de-duplication, cycles, and bad references."""

import re
from collections.abc import Mapping
from pathlib import Path

import pytest

from atc.errors import CycleError, MergeError, PathError, UnitError
from atc.jsonval import JsonObject
from atc.refs import Located, resolve_registry
from atc.resolve import Resolution, resolve
from atc.sources import Sources, discover
from tests.helpers import UID, make_source, unit


def single(conf_d: Path, units: Mapping[str, JsonObject]) -> Sources:
    """Make a source in the single layout holding ``units``, and discover it."""
    _ = make_source(conf_d, {f"conf/{name}": doc for name, doc in units.items()})
    return discover(conf_d, uid=UID)


def located(sources: Sources, *references: str) -> list[Located]:
    found: list[Located] = []
    for reference in references:
        unit_at = resolve_registry(sources, reference)
        assert unit_at is not None, reference
        found.append(unit_at)
    return found


def run(sources: Sources, *references: str, lax: bool = False) -> Resolution:
    return resolve(sources, located(sources, *references), lax=lax)


def test_an_importing_unit_overrides_what_it_imports(tmp_path: Path) -> None:
    sources = single(
        tmp_path / "conf.d",
        {
            "base.json": unit({"model": "sonnet"}),
            "top.json": unit({"model": "opus"}, imports=["base.json"]),
        },
    )

    assert run(sources, "top.json").settings == {"model": "opus"}


def test_later_imports_override_earlier_ones(tmp_path: Path) -> None:
    sources = single(
        tmp_path / "conf.d",
        {
            "first.json": unit({"model": "sonnet"}),
            "second.json": unit({"model": "opus"}),
            "top.json": unit(imports=["first.json", "second.json"]),
        },
    )

    assert run(sources, "top.json").settings == {"model": "opus"}


def test_units_on_the_command_line_merge_left_to_right(tmp_path: Path) -> None:
    sources = single(
        tmp_path / "conf.d",
        {
            "first.json": unit({"model": "sonnet"}),
            "second.json": unit({"model": "opus"}),
        },
    )

    assert run(sources, "first.json", "second.json").settings == {"model": "opus"}


def test_a_shared_unit_is_merged_exactly_once(tmp_path: Path) -> None:
    # If `shared` were merged again while walking `right`, it would overwrite
    # the value `left` set, and the result would be "shared" instead of "left".
    sources = single(
        tmp_path / "conf.d",
        {
            "shared.json": unit({"model": "shared"}),
            "left.json": unit({"model": "left"}, imports=["shared.json"]),
            "right.json": unit(imports=["shared.json"]),
            "top.json": unit(imports=["left.json", "right.json"]),
        },
    )

    assert run(sources, "top.json").settings == {"model": "left"}


def test_naming_an_already_imported_unit_does_not_move_it(tmp_path: Path) -> None:
    # `top` imports `base`, so `base` has already contributed by the time the
    # command line reaches it. Contributing once wins over rightmost-wins.
    sources = single(
        tmp_path / "conf.d",
        {
            "base.json": unit({"model": "sonnet"}),
            "top.json": unit({"model": "opus"}, imports=["base.json"]),
        },
    )

    resolution = run(sources, "top.json", "base.json")

    assert resolution.settings == {"model": "opus"}
    assert len(resolution.warnings) == 1
    assert "already merged" in resolution.warnings[0]


def test_naming_a_unit_twice_warns_once(tmp_path: Path) -> None:
    sources = single(tmp_path / "conf.d", {"a.json": unit({"model": "opus"})})

    resolution = run(sources, "a.json", "a.json")

    assert resolution.settings == {"model": "opus"}
    assert len(resolution.warnings) == 1


def test_a_profile_carries_no_settings_of_its_own(tmp_path: Path) -> None:
    sources = single(
        tmp_path / "conf.d",
        {
            "rust.json": unit({"permissions": {"allow": ["cargo"]}}),
            "profile.json": {
                **unit(imports=["rust.json"]),
                "description": "a profile",
            },
        },
    )

    assert run(sources, "profile.json").settings == {
        "permissions": {"allow": ["cargo"]}
    }


def test_an_import_cycle_is_reported(tmp_path: Path) -> None:
    sources = single(
        tmp_path / "conf.d",
        {"a.json": unit(imports=["b.json"]), "b.json": unit(imports=["a.json"])},
    )

    with pytest.raises(CycleError, match=re.escape("a.json -> b.json -> a.json")):
        _ = run(sources, "a.json")


def test_a_unit_importing_itself_is_a_cycle(tmp_path: Path) -> None:
    sources = single(tmp_path / "conf.d", {"a.json": unit(imports=["a.json"])})

    with pytest.raises(CycleError):
        _ = run(sources, "a.json")


def test_a_missing_import_names_the_importer(tmp_path: Path) -> None:
    sources = single(tmp_path / "conf.d", {"a.json": unit(imports=["nowhere.json"])})

    with pytest.raises(PathError, match=re.escape("a.json: imports 'nowhere.json'")):
        _ = run(sources, "a.json")


def test_a_root_that_is_not_a_file_is_reported(tmp_path: Path) -> None:
    sources = single(tmp_path / "conf.d", {"a.json": unit()})
    source = sources.items[0]
    nowhere = Located(source, source.conf / "nowhere.json", "nowhere.json")

    with pytest.raises(PathError, match=r"'nowhere\.json': no such unit"):
        _ = resolve(sources, [nowhere], lax=False)


def test_an_import_escaping_conf_is_refused(tmp_path: Path) -> None:
    sources = single(tmp_path / "conf.d", {"a.json": unit(imports=["../outside.json"])})

    with pytest.raises(PathError, match=r"a\.json: .*resolves outside"):
        _ = run(sources, "a.json")


def test_a_symlink_escaping_conf_is_refused(tmp_path: Path) -> None:
    sources = single(tmp_path / "conf.d", {"a.json": unit(imports=["link.json"])})
    outside = tmp_path / "outside.json"
    _ = outside.write_text('{"version": 2}', encoding="utf-8")
    (sources.items[0].conf / "link.json").symlink_to(outside)

    with pytest.raises(PathError, match="resolves outside"):
        _ = run(sources, "a.json")


def test_conflicting_types_across_units_name_the_unit(tmp_path: Path) -> None:
    sources = single(
        tmp_path / "conf.d",
        {
            "base.json": unit({"permissions": {}}),
            "top.json": unit({"permissions": []}, imports=["base.json"]),
        },
    )

    with pytest.raises(MergeError, match=re.escape("top.json: conflicting types")):
        _ = run(sources, "top.json")


def test_unknown_settings_keys_are_rejected(tmp_path: Path) -> None:
    sources = single(tmp_path / "conf.d", {"a.json": unit({"permisions": {}})})

    with pytest.raises(UnitError, match="unknown settings key"):
        _ = run(sources, "a.json")


def test_lax_downgrades_unknown_settings_keys_to_warnings(tmp_path: Path) -> None:
    sources = single(tmp_path / "conf.d", {"a.json": unit({"permisions": {}})})

    resolution = run(sources, "a.json", lax=True)

    assert resolution.settings == {"permisions": {}}
    assert len(resolution.warnings) == 1
    assert "permisions" in resolution.warnings[0]


def test_known_but_unmodelled_keys_pass_through(tmp_path: Path) -> None:
    sources = single(tmp_path / "conf.d", {"a.json": unit({"cleanupPeriodDays": 7})})

    assert run(sources, "a.json").settings == {"cleanupPeriodDays": 7}


def test_units_nested_in_subdirectories_resolve(tmp_path: Path) -> None:
    sources = single(
        tmp_path / "conf.d",
        {
            "langs/rust.json": unit({"model": "opus"}),
            "bravo/bar.json": unit(imports=["langs/rust.json"]),
        },
    )

    assert run(sources, "bravo/bar.json").settings == {"model": "opus"}


def test_imports_follow_into_a_named_source(tmp_path: Path) -> None:
    conf_d = tmp_path / "conf.d"
    _ = make_source(
        conf_d / "personal", {"conf/common/base.json": unit({"model": "sonnet"})}
    )
    _ = make_source(
        conf_d / "bravo",
        {
            "conf/projects/x.json": unit(
                {"theme": "dark"}, imports=["@personal/common/base.json"]
            )
        },
    )
    sources = discover(conf_d, uid=UID)

    resolution = run(sources, "projects/x.json")

    assert resolution.settings == {"model": "sonnet", "theme": "dark"}


def test_a_unit_reached_from_two_sources_contributes_once(tmp_path: Path) -> None:
    conf_d = tmp_path / "conf.d"
    _ = make_source(
        conf_d / "personal",
        {
            "conf/base.json": unit({"model": "base"}),
            "conf/left.json": unit({"model": "left"}, imports=["base.json"]),
        },
    )
    _ = make_source(
        conf_d / "bravo",
        {"conf/top.json": unit(imports=["@personal/left.json", "@personal/base.json"])},
    )
    sources = discover(conf_d, uid=UID)

    assert run(sources, "top.json").settings == {"model": "left"}


def test_a_cycle_across_sources_is_reported_with_source_names(tmp_path: Path) -> None:
    conf_d = tmp_path / "conf.d"
    _ = make_source(
        conf_d / "personal", {"conf/a.json": unit(imports=["@bravo/b.json"])}
    )
    _ = make_source(
        conf_d / "bravo", {"conf/b.json": unit(imports=["@personal/a.json"])}
    )
    sources = discover(conf_d, uid=UID)

    with pytest.raises(
        CycleError,
        match=re.escape("@personal/a.json -> @bravo/b.json -> @personal/a.json"),
    ):
        _ = run(sources, "@personal/a.json")


def test_errors_name_units_by_source_and_path(tmp_path: Path) -> None:
    conf_d = tmp_path / "conf.d"
    _ = make_source(conf_d / "personal", {"conf/a.json": unit()})
    _ = make_source(
        conf_d / "bravo", {"conf/projects/x.json": unit({"permisions": {}})}
    )
    sources = discover(conf_d, uid=UID)

    with pytest.raises(UnitError, match=re.escape("@bravo/projects/x.json:")):
        _ = run(sources, "projects/x.json")


def test_a_missing_cross_source_import_names_importer_and_reference(
    tmp_path: Path,
) -> None:
    conf_d = tmp_path / "conf.d"
    _ = make_source(conf_d / "personal", {"conf/a.json": unit()})
    _ = make_source(
        conf_d / "bravo", {"conf/x.json": unit(imports=["@personal/nowhere.json"])}
    )
    sources = discover(conf_d, uid=UID)

    with pytest.raises(
        PathError,
        match=re.escape("@bravo/x.json: imports '@personal/nowhere.json'"),
    ):
        _ = run(sources, "x.json")


def test_a_transform_sees_each_unit_once_with_its_own_settings(
    tmp_path: Path,
) -> None:
    sources = single(
        tmp_path / "conf.d",
        {
            "base.json": unit({"model": "sonnet"}),
            "a.json": unit({"theme": "dark"}, imports=["base.json"]),
            "b.json": unit({"outputStyle": "x"}, imports=["base.json"]),
        },
    )
    seen: list[tuple[str, JsonObject]] = []

    def record(found: Located, settings: JsonObject) -> JsonObject:
        seen.append((found.display, settings))
        return settings

    _ = resolve(
        sources, located(sources, "a.json", "b.json"), lax=False, transform=record
    )

    assert seen == [
        ("base.json", {"model": "sonnet"}),
        ("a.json", {"theme": "dark"}),
        ("b.json", {"outputStyle": "x"}),
    ]


def test_what_a_transform_returns_is_what_gets_merged(tmp_path: Path) -> None:
    sources = single(
        tmp_path / "conf.d",
        {
            "base.json": unit({"model": "sonnet"}),
            "top.json": unit(imports=["base.json"]),
        },
    )

    def replace(found: Located, settings: JsonObject) -> JsonObject:
        return {"model": found.display} if settings else settings

    resolved = resolve(
        sources, located(sources, "top.json"), lax=False, transform=replace
    )

    assert resolved.settings == {"model": "base.json"}


def test_a_transform_runs_before_the_key_check(tmp_path: Path) -> None:
    sources = single(tmp_path / "conf.d", {"a.json": unit({"permisions": {}})})

    def drop_everything(_found: Located, _settings: JsonObject) -> JsonObject:
        return {}

    resolved = resolve(
        sources, located(sources, "a.json"), lax=False, transform=drop_everything
    )

    assert resolved.settings == {}


def test_an_error_from_a_transform_stops_the_resolution(tmp_path: Path) -> None:
    sources = single(tmp_path / "conf.d", {"a.json": unit({"model": "opus"})})

    def refuse(found: Located, _settings: JsonObject) -> JsonObject:
        raise UnitError(f"{found.display}: refused")

    with pytest.raises(UnitError, match=r"a\.json: refused"):
        _ = resolve(sources, located(sources, "a.json"), lax=False, transform=refuse)
