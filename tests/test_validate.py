"""Checking every unit in every source."""

from pathlib import Path

from atc.sources import discover
from atc.validate import validate_sources
from tests.helpers import UID, make_source, unit


def test_every_error_is_collected(tmp_path: Path) -> None:
    conf_d = make_source(
        tmp_path / "conf.d",
        {
            "conf/good.json": unit(),
            "conf/dangling.json": unit(imports=["gone.json"]),
            "conf/unknown.json": unit({"permisions": {}}),
        },
    )

    result = validate_sources(discover(conf_d, uid=UID), lax=False)

    assert result.units == 3
    assert len(result.errors) == 2
    assert result.warnings == ()


def test_lax_turns_unknown_settings_keys_into_warnings(tmp_path: Path) -> None:
    conf_d = make_source(
        tmp_path / "conf.d", {"conf/unknown.json": unit({"permisions": {}})}
    )

    result = validate_sources(discover(conf_d, uid=UID), lax=True)

    assert result.errors == ()
    assert len(result.warnings) == 1
    assert "permisions" in result.warnings[0]


def test_a_warning_shared_by_several_units_is_given_once(tmp_path: Path) -> None:
    conf_d = make_source(
        tmp_path / "conf.d",
        {
            "conf/base.json": unit({"permisions": {}}),
            "conf/one.json": unit(imports=["base.json"]),
            "conf/two.json": unit(imports=["base.json"]),
        },
    )

    result = validate_sources(discover(conf_d, uid=UID), lax=True)

    assert len(result.warnings) == 1


def test_discovery_warnings_come_first(tmp_path: Path) -> None:
    conf_d = tmp_path / "conf.d"
    _ = make_source(conf_d / "personal", {"conf/a.json": unit({"permisions": {}})})
    _ = make_source(conf_d / "stray", {"notes.md": "x"})

    result = validate_sources(discover(conf_d, uid=UID), lax=True)

    assert "stray" in result.warnings[0]
    assert "permisions" in result.warnings[1]


def test_a_merge_conflict_inside_one_unit_tree_is_an_error(tmp_path: Path) -> None:
    conf_d = make_source(
        tmp_path / "conf.d",
        {
            "conf/base.json": unit({"permissions": {}}),
            "conf/top.json": unit({"permissions": []}, imports=["base.json"]),
        },
    )

    result = validate_sources(discover(conf_d, uid=UID), lax=False)

    assert len(result.errors) == 1
    assert "conflicting types" in result.errors[0]


def test_a_unit_in_a_directory_starting_with_at_is_checked(tmp_path: Path) -> None:
    conf_d = make_source(tmp_path / "conf.d", {"conf/@odd/a.json": unit()})

    result = validate_sources(discover(conf_d, uid=UID), lax=False)

    assert (result.units, result.errors) == (1, ())


def test_a_unit_that_leads_out_of_conf_is_an_error(tmp_path: Path) -> None:
    conf_d = make_source(tmp_path / "conf.d", {"conf/a.json": unit()})
    sources = discover(conf_d, uid=UID)
    outside = tmp_path / "outside.json"
    _ = outside.write_text("{}", encoding="utf-8")
    (conf_d / "conf" / "link.json").symlink_to(outside)

    result = validate_sources(sources, lax=False)

    assert result.units == 2
    assert len(result.errors) == 1
    assert "resolves outside" in result.errors[0]
