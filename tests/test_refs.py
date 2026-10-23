"""Unit references: imports within and across sources, and registry lookups."""

from pathlib import Path

import pytest

from atc.errors import PathError
from atc.refs import locate, resolve_import, resolve_registry
from atc.sources import Sources, discover
from tests.helpers import UID, make_source, unit


def multi(conf_d: Path, *files: str) -> Sources:
    """Make sources `personal` and `bravo`, both holding every unit in `files`."""
    for name in ("personal", "bravo"):
        _ = make_source(conf_d / name, {f"conf/{path}": unit() for path in files})
    return discover(conf_d, uid=UID)


def test_an_import_without_at_stays_in_its_own_source(tmp_path: Path) -> None:
    sources = multi(tmp_path / "conf.d", "common/base.json")
    bravo = sources.named("bravo")

    located = resolve_import(sources, bravo, "common/base.json")

    assert located.source == bravo
    assert located.path == bravo.conf / "common/base.json"
    assert located.display == "@bravo/common/base.json"


def test_at_name_crosses_into_another_source(tmp_path: Path) -> None:
    sources = multi(tmp_path / "conf.d", "common/base.json")
    personal, bravo = sources.named("personal"), sources.named("bravo")

    located = resolve_import(sources, bravo, "@personal/common/base.json")

    assert located.source == personal
    assert located.path == personal.conf / "common/base.json"
    assert located.display == "@personal/common/base.json"


def test_at_name_is_an_error_in_the_single_layout(tmp_path: Path) -> None:
    conf_d = make_source(tmp_path / "conf.d", {"conf/a.json": unit()})
    sources = discover(conf_d, uid=UID)

    with pytest.raises(PathError, match="@"):
        _ = resolve_import(sources, sources.items[0], "@bravo/a.json")


def test_at_name_must_name_a_source(tmp_path: Path) -> None:
    sources = multi(tmp_path / "conf.d", "a.json")

    with pytest.raises(PathError, match="no config source named 'nobody'"):
        _ = resolve_import(sources, sources.named("bravo"), "@nobody/a.json")


@pytest.mark.parametrize("text", ["@bravo", "@/a.json", "@bravo/"])
def test_a_malformed_at_reference_is_refused(tmp_path: Path, text: str) -> None:
    sources = multi(tmp_path / "conf.d", "a.json")

    with pytest.raises(PathError, match="@name/"):
        _ = resolve_import(sources, sources.named("bravo"), text)


def test_registry_lookup_finds_the_one_source_that_has_it(tmp_path: Path) -> None:
    conf_d = tmp_path / "conf.d"
    _ = make_source(conf_d / "personal", {"conf/common/x.json": unit()})
    _ = make_source(conf_d / "bravo", {"conf/common/y.json": unit()})
    sources = discover(conf_d, uid=UID)

    located = resolve_registry(sources, "common/y.json")

    assert located is not None
    assert located.source == sources.named("bravo")
    assert located.display == "@bravo/common/y.json"


def test_registry_lookup_needs_exactly_one_match(tmp_path: Path) -> None:
    sources = multi(tmp_path / "conf.d", "common/x.json")

    with pytest.raises(PathError, match="@name/"):
        _ = resolve_registry(sources, "common/x.json")


def test_registry_lookup_returns_none_when_no_source_has_it(tmp_path: Path) -> None:
    sources = multi(tmp_path / "conf.d", "common/x.json")

    assert resolve_registry(sources, "common/absent.json") is None


def test_a_qualified_registry_lookup_picks_the_named_source(tmp_path: Path) -> None:
    sources = multi(tmp_path / "conf.d", "common/x.json")

    located = resolve_registry(sources, "@personal/common/x.json")

    assert located is not None
    assert located.source == sources.named("personal")


def test_a_qualified_registry_lookup_of_a_missing_unit_is_an_error(
    tmp_path: Path,
) -> None:
    sources = multi(tmp_path / "conf.d", "common/x.json")

    with pytest.raises(PathError, match="no such unit"):
        _ = resolve_registry(sources, "@personal/common/absent.json")


def test_a_qualified_registry_lookup_is_an_error_in_the_single_layout(
    tmp_path: Path,
) -> None:
    conf_d = make_source(tmp_path / "conf.d", {"conf/a.json": unit()})

    with pytest.raises(PathError, match="@"):
        _ = resolve_registry(discover(conf_d, uid=UID), "@bravo/a.json")


def test_the_registry_finds_a_unit_in_the_single_layout(tmp_path: Path) -> None:
    conf_d = make_source(tmp_path / "conf.d", {"conf/langs/rust.json": unit()})
    sources = discover(conf_d, uid=UID)

    located = resolve_registry(sources, "langs/rust.json")

    assert located is not None
    assert located.path == conf_d / "conf" / "langs" / "rust.json"
    assert located.display == "langs/rust.json"


@pytest.mark.parametrize("text", ["../assets/x.json", "a/../../assets/x.json"])
def test_imports_cannot_escape_conf(tmp_path: Path, text: str) -> None:
    sources = multi(tmp_path / "conf.d", "a.json")
    _ = make_source(sources.named("bravo").root, {"assets/x.json": "{}"})

    with pytest.raises(PathError, match="resolves outside"):
        _ = resolve_import(sources, sources.named("bravo"), text)


def test_a_symlink_cannot_lead_out_of_conf(tmp_path: Path) -> None:
    sources = multi(tmp_path / "conf.d", "a.json")
    bravo = sources.named("bravo")
    outside = tmp_path / "outside.json"
    _ = outside.write_text("{}", encoding="utf-8")
    (bravo.conf / "link.json").symlink_to(outside)

    with pytest.raises(PathError, match="resolves outside"):
        _ = resolve_import(sources, bravo, "link.json")


def test_unit_references_must_end_in_json(tmp_path: Path) -> None:
    sources = multi(tmp_path / "conf.d", "a.json")

    with pytest.raises(PathError, match=r"must end in \.json"):
        _ = resolve_import(sources, sources.named("bravo"), "langs/rust")
    with pytest.raises(PathError, match=r"must end in \.json"):
        _ = resolve_registry(sources, "@bravo/langs/rust")


def test_unit_references_must_be_relative(tmp_path: Path) -> None:
    sources = multi(tmp_path / "conf.d", "a.json")

    with pytest.raises(PathError, match="relative"):
        _ = resolve_import(sources, sources.named("bravo"), "/etc/a.json")


def test_an_empty_unit_reference_is_refused(tmp_path: Path) -> None:
    sources = multi(tmp_path / "conf.d", "a.json")

    with pytest.raises(PathError, match="empty"):
        _ = resolve_import(sources, sources.named("bravo"), "")


def test_locate_does_not_read_an_at_in_a_directory_name(tmp_path: Path) -> None:
    conf_d = make_source(tmp_path / "conf.d", {"conf/@odd/a.json": unit()})
    source = discover(conf_d, uid=UID).items[0]

    located = locate(source, "@odd/a.json")

    assert located.path == source.conf / "@odd" / "a.json"


def assets(conf_d: Path, *files: str) -> Sources:
    """Make sources `personal` and `bravo`, both holding every asset in `files`."""
    for name in ("personal", "bravo"):
        _ = make_source(conf_d / name, {f"assets/{path}": "x\n" for path in files})
    return discover(conf_d, uid=UID)


def test_an_asset_is_found_in_the_single_layout(tmp_path: Path) -> None:
    conf_d = make_source(tmp_path / "conf.d", {"assets/docs/AGENTS.md": "x\n"})
    sources = discover(conf_d, uid=UID)

    located = resolve_registry(sources, "docs/AGENTS.md", "asset")

    assert located is not None
    assert located.path == conf_d / "assets" / "docs" / "AGENTS.md"
    assert located.display == "docs/AGENTS.md"
    assert located.source == sources.items[0]


def test_an_asset_is_looked_up_in_assets_not_in_conf(tmp_path: Path) -> None:
    conf_d = make_source(
        tmp_path / "conf.d",
        {"conf/AGENTS.md": "x\n", "assets/units/a.json": "{}"},
    )
    sources = discover(conf_d, uid=UID)

    assert resolve_registry(sources, "AGENTS.md", "asset") is None
    assert resolve_registry(sources, "a.json", "asset") is None
    located = resolve_registry(sources, "units/a.json", "asset")
    assert located is not None
    assert located.path == conf_d / "assets" / "units" / "a.json"


def test_an_asset_lookup_finds_the_one_source_that_has_it(tmp_path: Path) -> None:
    conf_d = tmp_path / "conf.d"
    _ = make_source(conf_d / "personal", {"assets/x.md": "x\n"})
    _ = make_source(conf_d / "bravo", {"assets/y.md": "y\n"})
    sources = discover(conf_d, uid=UID)

    located = resolve_registry(sources, "y.md", "asset")

    assert located is not None
    assert located.source == sources.named("bravo")
    assert located.display == "@bravo/y.md"


def test_an_asset_lookup_needs_exactly_one_match(tmp_path: Path) -> None:
    sources = assets(tmp_path / "conf.d", "AGENTS.md")

    with pytest.raises(
        PathError, match=r"'AGENTS.md' exists as .*add an @name/ prefix"
    ):
        _ = resolve_registry(sources, "AGENTS.md", "asset")


def test_a_qualified_asset_lookup_picks_the_named_source(tmp_path: Path) -> None:
    sources = assets(tmp_path / "conf.d", "AGENTS.md")

    located = resolve_registry(sources, "@personal/AGENTS.md", "asset")

    assert located is not None
    assert located.source == sources.named("personal")


def test_a_qualified_asset_lookup_of_a_missing_asset_is_an_error(
    tmp_path: Path,
) -> None:
    sources = assets(tmp_path / "conf.d", "AGENTS.md")

    with pytest.raises(PathError, match=r"'@personal/gone\.md': no such asset"):
        _ = resolve_registry(sources, "@personal/gone.md", "asset")


def test_an_asset_that_is_a_directory_is_not_found(tmp_path: Path) -> None:
    conf_d = make_source(tmp_path / "conf.d", {"assets/docs/AGENTS.md": "x\n"})

    assert resolve_registry(discover(conf_d, uid=UID), "docs", "asset") is None


def test_asset_references_must_be_relative_and_non_empty(tmp_path: Path) -> None:
    sources = assets(tmp_path / "conf.d", "AGENTS.md")

    with pytest.raises(PathError, match="asset references must be relative"):
        _ = resolve_registry(sources, "/etc/passwd", "asset")
    with pytest.raises(PathError, match="empty asset reference"):
        _ = resolve_registry(sources, "", "asset")


def test_asset_references_cannot_escape_assets(tmp_path: Path) -> None:
    sources = assets(tmp_path / "conf.d", "AGENTS.md")
    _ = make_source(sources.named("bravo").root, {"conf/a.json": "{}"})

    with pytest.raises(PathError, match="resolves outside"):
        _ = resolve_registry(sources, "../conf/a.json", "asset")


def test_a_symlink_cannot_lead_out_of_assets(tmp_path: Path) -> None:
    sources = assets(tmp_path / "conf.d", "AGENTS.md")
    bravo = sources.named("bravo")
    outside = tmp_path / "outside.md"
    _ = outside.write_text("x\n", encoding="utf-8")
    (bravo.assets / "link.md").symlink_to(outside)

    with pytest.raises(PathError, match="resolves outside"):
        _ = resolve_registry(sources, "@bravo/link.md", "asset")


def test_a_malformed_qualified_asset_reference_is_refused(tmp_path: Path) -> None:
    sources = assets(tmp_path / "conf.d", "AGENTS.md")

    with pytest.raises(PathError, match="@name/"):
        _ = resolve_registry(sources, "@bravo", "asset")


def test_a_qualified_asset_lookup_is_an_error_in_the_single_layout(
    tmp_path: Path,
) -> None:
    conf_d = make_source(tmp_path / "conf.d", {"assets/AGENTS.md": "x\n"})

    with pytest.raises(PathError, match="@"):
        _ = resolve_registry(discover(conf_d, uid=UID), "@bravo/AGENTS.md", "asset")


def test_a_skill_is_a_directory_with_skill_md_under_skills(tmp_path: Path) -> None:
    conf_d = make_source(
        tmp_path / "conf.d",
        {
            "skills/review/SKILL.md": "x\n",
            "skills/draft/notes.md": "x\n",
            "conf/review/SKILL.md": "x\n",
        },
    )
    sources = discover(conf_d, uid=UID)

    located = resolve_registry(sources, "review", "skill")

    assert located is not None
    assert located.path == conf_d / "skills" / "review"
    assert located.display == "review"
    assert resolve_registry(sources, "draft", "skill") is None
    assert resolve_registry(sources, "SKILL.md", "skill") is None


def test_a_malformed_qualified_skill_reference_is_refused(tmp_path: Path) -> None:
    conf_d = tmp_path / "conf.d"
    _ = make_source(conf_d / "bravo", {"skills/a/SKILL.md": "x\n"})

    with pytest.raises(PathError, match=r"@name/<skill>"):
        _ = resolve_registry(discover(conf_d, uid=UID), "@bravo", "skill")
