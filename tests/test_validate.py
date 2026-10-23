"""Checking every unit in every source."""

from pathlib import Path

from atc.jsonval import JsonObject
from atc.paths import Dirs
from atc.registry import REGISTRY_FILE, load_registry
from atc.sources import discover
from atc.validate import Validation, validate_config, validate_sources
from tests.conftest import ConfigWriter
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


TWO_ACCOUNTS = """\
    version: 1
    accounts:
      personal: {dir: ~/.claude}
      work: {dir: ~/.config/work, unit: profiles/work.json}
"""


def check_config(
    dirs: Dirs, home: Path, configure: ConfigWriter, files: dict[str, JsonObject]
) -> Validation:
    configure(files, TWO_ACCOUNTS)
    return validate_config(
        discover(dirs.conf_d, uid=UID),
        load_registry(dirs.config_dir / REGISTRY_FILE, home),
        lax=False,
    )


def test_validate_config_passes_when_every_account_has_its_unit(
    dirs: Dirs, home: Path, configure: ConfigWriter
) -> None:
    result = check_config(
        dirs,
        home,
        configure,
        {"conf/accounts/personal.json": unit(), "conf/profiles/work.json": unit()},
    )

    assert (result.units, result.errors, result.warnings) == (2, (), ())


def test_validate_config_reports_every_account_whose_unit_is_missing(
    dirs: Dirs, home: Path, configure: ConfigWriter
) -> None:
    result = check_config(dirs, home, configure, {"conf/other.json": unit()})

    assert len(result.errors) == 2
    assert "account 'personal'" in result.errors[0]
    assert "account 'work'" in result.errors[1]
    assert "profiles/work.json" in result.errors[1]


def test_validate_config_keeps_the_errors_of_the_sources(
    dirs: Dirs, home: Path, configure: ConfigWriter
) -> None:
    result = check_config(
        dirs,
        home,
        configure,
        {
            "conf/accounts/personal.json": unit(),
            "conf/profiles/work.json": unit(),
            "conf/stray.json": unit(imports=["gone.json"]),
        },
    )

    assert result.units == 3
    assert len(result.errors) == 1
    assert "gone.json" in result.errors[0]
