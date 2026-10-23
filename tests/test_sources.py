"""Discovering config sources, and refusing the ones that are not trusted."""

import re
from pathlib import Path

import pytest

from atc.errors import PathError, SourceError
from atc.sources import Source, discover
from tests.helpers import UID, make_source, unit


def test_single_layout(tmp_path: Path) -> None:
    conf_d = make_source(tmp_path / "conf.d", {"conf/a.json": unit()})
    found = discover(conf_d, uid=UID)
    assert found.layout == "single"
    assert [s.name for s in found.items] == [""]


def test_multi_layout_names_sources_after_their_directories(tmp_path: Path) -> None:
    conf_d = tmp_path / "conf.d"
    _ = make_source(conf_d / "personal", {"conf/a.json": unit()})
    _ = make_source(conf_d / "bravo", {"assets/x.sh": "#!/bin/sh\n"})
    found = discover(conf_d, uid=UID)
    assert found.layout == "multi"
    assert [s.name for s in found.items] == ["bravo", "personal"]
    assert found.items[0].root == conf_d / "bravo"


def test_a_source_with_only_skills_counts(tmp_path: Path) -> None:
    conf_d = make_source(tmp_path / "conf.d", {"skills/review/SKILL.md": "x"})
    assert discover(conf_d, uid=UID).layout == "single"


def test_other_directories_do_not_disturb_the_single_layout(tmp_path: Path) -> None:
    conf_d = make_source(
        tmp_path / "conf.d", {"conf/a.json": unit(), "docs/notes.md": "x"}
    )
    found = discover(conf_d, uid=UID)
    assert [s.name for s in found.items] == [""]
    assert found.warnings == ()


def test_sources_at_both_levels_are_an_error(tmp_path: Path) -> None:
    conf_d = make_source(
        tmp_path / "conf.d", {"conf/a.json": unit(), "personal/conf/b.json": unit()}
    )
    with pytest.raises(SourceError, match="both"):
        _ = discover(conf_d, uid=UID)


def test_nothing_deeper_than_one_level_is_read(tmp_path: Path) -> None:
    conf_d = tmp_path / "conf.d"
    _ = make_source(conf_d / "personal", {"conf/a.json": unit()})
    _ = make_source(conf_d / "group" / "deep", {"conf/b.json": unit()})
    found = discover(conf_d, uid=UID)
    assert [s.name for s in found.items] == ["personal"]
    assert any("group" in w for w in found.warnings)


def test_hidden_entries_and_plain_files_are_ignored(tmp_path: Path) -> None:
    conf_d = tmp_path / "conf.d"
    _ = make_source(conf_d / "personal", {"conf/a.json": unit()})
    _ = make_source(conf_d, {".git/HEAD": "x", "README": "x"})
    assert discover(conf_d, uid=UID).warnings == ()


def test_a_directory_with_no_sources_in_it_is_an_error(tmp_path: Path) -> None:
    conf_d = make_source(tmp_path / "conf.d", {"README": "x"})
    with pytest.raises(SourceError, match="no config sources"):
        _ = discover(conf_d, uid=UID)


@pytest.mark.parametrize("name", ["a", "my_src", "src2"])
def test_source_names_follow_the_name_rule(tmp_path: Path, name: str) -> None:
    conf_d = tmp_path / "conf.d"
    _ = make_source(conf_d / name, {"conf/a.json": unit()})
    assert [s.name for s in discover(conf_d, uid=UID).items] == [name]


@pytest.mark.parametrize("name", ["Personal", "my-src", "1src", "_src"])
def test_source_names_that_break_the_rule_are_refused(
    tmp_path: Path, name: str
) -> None:
    conf_d = tmp_path / "conf.d"
    _ = make_source(conf_d / name, {"conf/a.json": unit()})
    with pytest.raises(SourceError, match=f"'{name}' is not a valid source name"):
        _ = discover(conf_d, uid=UID)


def test_a_symlink_under_conf_is_refused(tmp_path: Path) -> None:
    conf_d = make_source(tmp_path / "conf.d", {"conf/a.json": unit()})
    (conf_d / "conf" / "b.json").symlink_to(conf_d / "conf" / "a.json")
    with pytest.raises(SourceError, match=r"b\.json"):
        _ = discover(conf_d, uid=UID)


def test_a_symlinked_source_directory_is_refused(tmp_path: Path) -> None:
    real = make_source(tmp_path / "checkout", {"conf/a.json": unit()})
    (tmp_path / "conf.d").mkdir()
    (tmp_path / "conf.d" / "personal").symlink_to(real)
    with pytest.raises(SourceError, match="personal"):
        _ = discover(tmp_path / "conf.d", uid=UID)


def test_a_symlinked_conf_d_is_refused(tmp_path: Path) -> None:
    real = make_source(tmp_path / "checkout", {"conf/a.json": unit()})
    (tmp_path / "conf.d").symlink_to(real)
    with pytest.raises(SourceError, match="symbolic link"):
        _ = discover(tmp_path / "conf.d", uid=UID)


def test_a_symlinked_conf_directory_is_refused(tmp_path: Path) -> None:
    real = make_source(tmp_path / "checkout", {"conf/a.json": unit()})
    conf_d = tmp_path / "conf.d"
    conf_d.mkdir()
    (conf_d / "conf").symlink_to(real / "conf")
    with pytest.raises(SourceError, match="symbolic link"):
        _ = discover(conf_d, uid=UID)


def test_a_group_writable_file_is_refused_with_the_fix(tmp_path: Path) -> None:
    conf_d = make_source(tmp_path / "conf.d", {"assets/hook.sh": "#!/bin/sh\n"})
    (conf_d / "assets" / "hook.sh").chmod(0o664)
    with pytest.raises(SourceError, match=r"hook\.sh.*chmod go-w"):
        _ = discover(conf_d, uid=UID)


def test_a_world_writable_directory_is_refused(tmp_path: Path) -> None:
    conf_d = make_source(tmp_path / "conf.d", {"skills/review/SKILL.md": "x"})
    (conf_d / "skills" / "review").chmod(0o757)
    with pytest.raises(SourceError, match=r"review.*chmod go-w"):
        _ = discover(conf_d, uid=UID)


def writable_message(path: Path) -> str:
    return f"{re.escape(str(path))}: group- or world-writable; run chmod go-w"


def test_a_group_writable_conf_d_is_refused_with_the_fix(tmp_path: Path) -> None:
    conf_d = make_source(tmp_path / "conf.d", {"conf/a.json": unit()})
    conf_d.chmod(0o775)
    with pytest.raises(SourceError, match=writable_message(conf_d)):
        _ = discover(conf_d, uid=UID)


def test_a_group_writable_conf_d_is_refused_in_the_multi_layout(
    tmp_path: Path,
) -> None:
    conf_d = tmp_path / "conf.d"
    _ = make_source(conf_d / "personal", {"conf/a.json": unit()})
    conf_d.chmod(0o775)
    with pytest.raises(SourceError, match=writable_message(conf_d)):
        _ = discover(conf_d, uid=UID)


def test_a_group_writable_source_directory_is_refused_with_the_fix(
    tmp_path: Path,
) -> None:
    conf_d = tmp_path / "conf.d"
    source = make_source(conf_d / "personal", {"conf/a.json": unit()})
    source.chmod(0o775)
    with pytest.raises(SourceError, match=writable_message(source)):
        _ = discover(conf_d, uid=UID)


def test_a_world_writable_source_directory_is_refused(tmp_path: Path) -> None:
    conf_d = tmp_path / "conf.d"
    source = make_source(conf_d / "personal", {"conf/a.json": unit()})
    source.chmod(0o757)
    with pytest.raises(SourceError, match=writable_message(source)):
        _ = discover(conf_d, uid=UID)


def test_entries_owned_by_someone_else_are_refused(tmp_path: Path) -> None:
    conf_d = make_source(tmp_path / "conf.d", {"conf/a.json": unit()})
    with pytest.raises(SourceError, match="owned"):
        _ = discover(conf_d, uid=UID + 1)


def test_a_missing_conf_d_says_how_to_add_a_source(tmp_path: Path) -> None:
    with pytest.raises(SourceError, match="git clone"):
        _ = discover(tmp_path / "nope", uid=UID)


def test_a_source_names_its_files_for_display(tmp_path: Path) -> None:
    assert Source("bravo", tmp_path).display("a.json") == "@bravo/a.json"
    assert Source("", tmp_path).display("a.json") == "a.json"


def test_a_source_knows_its_directories(tmp_path: Path) -> None:
    source = Source("bravo", tmp_path)
    assert (source.conf, source.assets, source.skills) == (
        tmp_path / "conf",
        tmp_path / "assets",
        tmp_path / "skills",
    )


def test_a_source_is_found_by_name(tmp_path: Path) -> None:
    conf_d = tmp_path / "conf.d"
    _ = make_source(conf_d / "personal", {"conf/a.json": unit()})
    bravo = make_source(conf_d / "bravo", {"conf/a.json": unit()})
    assert discover(conf_d, uid=UID).named("bravo").root == bravo


def test_an_unknown_source_name_lists_the_known_ones(tmp_path: Path) -> None:
    conf_d = tmp_path / "conf.d"
    _ = make_source(conf_d / "personal", {"conf/a.json": unit()})
    _ = make_source(conf_d / "bravo", {"conf/a.json": unit()})
    with pytest.raises(PathError, match=r"'nobody'.*bravo, personal"):
        _ = discover(conf_d, uid=UID).named("nobody")


def test_the_single_layout_has_no_name_to_look_up(tmp_path: Path) -> None:
    conf_d = make_source(tmp_path / "conf.d", {"conf/a.json": unit()})
    with pytest.raises(PathError, match="no config source named 'bravo'"):
        _ = discover(conf_d, uid=UID).named("bravo")
