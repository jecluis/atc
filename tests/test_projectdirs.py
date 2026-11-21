"""Where a project with its own configuration directories keeps them."""

from pathlib import Path

from atc.paths import Dirs
from atc.projectdirs import (
    account_dirs,
    common_dir,
    config_dir,
    config_dir_link,
    memory_dir,
)
from atc.registry import parse_registry

REGISTRY = """\
version: 1
default-account: personal
accounts:
  personal: {agent: claude, dir: ~/.claude}
  work: {agent: claude, dir: ~/.config/work}
projects:
  bar: {repo: /r/bar, own-config-dir: true}
  baz: {repo: /r/baz, own-config-dir: true}
  qux: {repo: /r/qux}
"""


def test_config_dirs_go_by_project_then_account_in_the_state_dir(
    home: Path, dirs: Dirs
) -> None:
    registry = parse_registry(REGISTRY, home / "machine.yaml", home)
    bar = registry.project("bar")

    assert config_dir(dirs, registry.account("work"), bar) == (
        dirs.state_dir / "projects" / "bar" / "work"
    )
    assert common_dir(dirs, bar) == dirs.state_dir / "projects" / "bar" / "_common"
    assert memory_dir(dirs, bar) == dirs.data_dir / "projects" / "bar" / "memory"


def test_account_dirs_are_its_own_then_the_project_dirs_that_exist(
    home: Path, dirs: Dirs
) -> None:
    registry = parse_registry(REGISTRY, home / "machine.yaml", home)
    personal = registry.account("personal")
    baz = config_dir(dirs, personal, registry.project("baz"))
    baz.mkdir(parents=True)

    found = account_dirs(dirs, registry, personal)

    assert [there.dir for there in found] == [personal.dir, baz]


def test_a_link_anywhere_on_the_way_to_a_config_dir_is_found(
    home: Path, dirs: Dirs, tmp_path: Path
) -> None:
    registry = parse_registry(REGISTRY, home / "machine.yaml", home)
    personal, bar = registry.account("personal"), registry.project("bar")
    (dirs.state_dir / "projects").mkdir(parents=True)
    link = dirs.state_dir / "projects" / "bar"
    link.symlink_to(tmp_path)

    assert config_dir_link(dirs, personal, bar) == link
    link.unlink()
    assert config_dir_link(dirs, personal, bar) is None


def test_account_dirs_include_those_left_behind_by_the_registry(
    home: Path, dirs: Dirs
) -> None:
    registry = parse_registry(REGISTRY, home / "machine.yaml", home)
    personal = registry.account("personal")
    left = dirs.state_dir / "projects" / "gone" / "personal"
    left.mkdir(parents=True)

    found = account_dirs(dirs, registry, personal)

    assert [there.dir for there in found] == [personal.dir, left]


def test_a_link_at_a_shared_directory_is_found(
    home: Path, dirs: Dirs, tmp_path: Path
) -> None:
    registry = parse_registry(REGISTRY, home / "machine.yaml", home)
    personal, bar = registry.account("personal"), registry.project("bar")
    memory_dir(dirs, bar).parent.mkdir(parents=True)
    memory_dir(dirs, bar).symlink_to(tmp_path)

    assert config_dir_link(dirs, personal, bar) == memory_dir(dirs, bar)
