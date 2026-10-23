"""``atc check``: what a session's protections look like from inside it."""

import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path

import pytest

from atc.check import (
    CheckResult,
    RealProbe,
    Report,
    current_account,
    in_sandbox,
    run_checks,
)
from atc.cli import main
from atc.errors import GitError
from atc.exitcodes import CHECK, CONFIG, OK, USAGE
from atc.git import run_git
from atc.paths import Dirs
from atc.registry import Registry, parse_registry
from tests.conftest import RegistryWriter
from tests.helpers import git_repo

REGISTRY = """\
version: 1
default-account: personal
accounts:
  personal:
    agent: claude
    dir: ~/.claude
    skills: [grill-me]
  bravo:
    agent: claude
    dir: ~/.config/bravo/claude
"""

TOP_LEVEL = ("rev-parse", "--show-toplevel")
GC_AUTO = ("config", "--get", "gc.auto")
NOT_IN_A_REPOSITORY = "not in a git repository, so the repository checks are skipped"
REPOSITORY_CHECKS = 7
PROTECTED = 18
"""How many checks a session in a repository, in the `personal` account, makes."""


@dataclass(slots=True)
class FakeProbe:
    """
    A session's view of files and git, as sets of what it can do.

    A path exists when it is in ``existing`` or in ``writable_paths``. Asking
    whether a path that does not exist is writable is a mistake in the test or
    in the check, so it raises rather than answering.
    """

    writable_paths: set[Path] = field(default_factory=set[Path])
    readable_paths: set[Path] = field(default_factory=set[Path])
    existing: set[Path] = field(default_factory=set[Path])
    answers: dict[tuple[str, ...], str] = field(
        default_factory=dict[tuple[str, ...], str]
    )
    common: Path | GitError | None = None
    links: dict[Path, Path] = field(default_factory=dict[Path, Path])
    listings: dict[Path, list[Path]] = field(default_factory=dict[Path, list[Path]])
    unlistable: set[Path] = field(default_factory=set[Path])

    def writable(self, path: Path) -> bool:
        if not self.exists(path):
            raise AssertionError(
                f"asked whether {path}, which is not there, is writable"
            )
        return path in self.writable_paths

    def readable_contents(self, path: Path) -> bool:
        return path in self.readable_paths

    def exists(self, path: Path) -> bool:
        return path in self.existing or path in self.writable_paths

    def git(self, args: Sequence[str]) -> str:
        if tuple(args) not in self.answers:
            raise GitError(f"git {' '.join(args)}: exit status 1")
        return self.answers[tuple(args)]

    def common_dir(self) -> Path | None:
        if isinstance(self.common, GitError):
            raise self.common
        return self.common

    def readlink(self, path: Path) -> Path | None:
        return self.links.get(path)

    def entries(self, directory: Path) -> list[Path]:
        if directory in self.unlistable:
            raise PermissionError(13, "Permission denied", str(directory))
        return self.listings.get(directory, [])


@dataclass(frozen=True, slots=True)
class World:
    """A session of the `personal` account in a repository, protected throughout."""

    probe: FakeProbe
    registry: Registry
    dirs: Dirs
    home: Path
    env: dict[str, str]
    git_dir: Path

    @property
    def repo(self) -> Path:
        return self.git_dir.parent

    @property
    def install_dir(self) -> Path:
        return self.home / "tools" / "atc"

    @property
    def python_dir(self) -> Path:
        return self.home / "python" / "cpython"

    @property
    def personal(self) -> Path:
        return self.registry.account("personal").dir

    @property
    def bravo(self) -> Path:
        return self.registry.account("bravo").dir

    def run(self) -> Report:
        return run_checks(
            self.probe,
            registry=self.registry,
            dirs=self.dirs,
            install_dir=self.install_dir,
            python_dir=self.python_dir,
            home=self.home,
            env=self.env,
        )


def make_world(home: Path, dirs: Dirs, registry: Registry) -> World:
    git_dir = home / "work" / "repo" / ".git"
    personal = registry.account("personal").dir
    probe = FakeProbe(
        writable_paths={git_dir / "objects"},
        existing={
            git_dir / "objects",
            git_dir / "objects" / "pack",
            git_dir / "packed-refs",
            git_dir / "hooks",
            git_dir / "config",
            git_dir.parent.parent,
            personal / ".credentials.json",
            home / ".claude.json",
            registry.account("bravo").dir,
            dirs.config_dir,
            dirs.conf_d,
            dirs.state_dir,
            dirs.runtime_dir,
            home / "tools" / "atc",
            home / "python" / "cpython",
            dirs.conf_d / "assets" / "AGENTS.md",
            dirs.conf_d / "skills" / "grill-me",
        },
        answers={TOP_LEVEL: str(git_dir.parent), GC_AUTO: "0"},
        common=git_dir,
        links={
            personal / "CLAUDE.md": dirs.conf_d / "assets" / "AGENTS.md",
            personal / "skills" / "grill-me": dirs.conf_d / "skills" / "grill-me",
        },
        listings={personal / "skills": [personal / "skills" / "grill-me"]},
    )
    return World(probe, registry, dirs, home, {"ATC_ACCOUNT": "personal"}, git_dir)


@pytest.fixture
def registry(home: Path) -> Registry:
    return parse_registry(REGISTRY, home / "machine.yaml", home)


@pytest.fixture
def world(home: Path, dirs: Dirs, registry: Registry) -> World:
    return make_world(home, dirs, registry)


def failures(report: Report) -> list[str]:
    return [result.what for result in report.results if not result.ok]


def test_everything_protected_passes(world: World) -> None:
    report = world.run()

    assert failures(report) == []
    assert len(report.results) == PROTECTED
    assert report.notes == ()
    assert report.ok


def test_the_checks_come_in_the_order_of_the_sections(world: World) -> None:
    whats = [result.what for result in world.run().results]

    markers = (
        "objects/pack",
        "automatic gc",
        "worktree",
        ".credentials.json",
        str(world.bravo),
        "configuration directory",
        "config sources directory",
        "install directory",
        "Python directory",
        "CLAUDE.md",
        "grill-me",
    )
    positions = [next(i for i, w in enumerate(whats) if m in w) for m in markers]
    assert positions == sorted(positions)


def test_a_writable_packed_refs_fails(world: World) -> None:
    world.probe.writable_paths.add(world.git_dir / "packed-refs")

    report = world.run()

    assert failures(report) == [f"{world.git_dir}/packed-refs is read-only"]
    assert not report.ok


@pytest.mark.parametrize("name", ["objects/pack", "hooks", "config"])
def test_a_writable_git_path_fails(world: World, name: str) -> None:
    world.probe.writable_paths.add(world.git_dir / name)

    assert failures(world.run()) == [f"{world.git_dir}/{name} is read-only"]


def test_packed_refs_hooks_and_config_are_checked_only_when_present(
    world: World,
) -> None:
    gone = {world.git_dir / name for name in ("packed-refs", "hooks", "config")}
    world.probe.existing -= gone

    report = world.run()

    assert failures(report) == []
    assert len(report.results) == PROTECTED - 3


def test_unwritable_objects_fails(world: World) -> None:
    world.probe.writable_paths.remove(world.git_dir / "objects")

    assert failures(world.run()) == ["new objects can be written, so commits work"]


@pytest.mark.parametrize("value", ["1", "", None])
def test_automatic_gc_must_be_off(world: World, value: str | None) -> None:
    if value is None:
        del world.probe.answers[GC_AUTO]
    else:
        world.probe.answers[GC_AUTO] = value

    (failed,) = failures(world.run())

    assert failed.startswith("automatic gc is off in this session")


def test_a_writable_worktree_parent_fails(world: World) -> None:
    world.probe.writable_paths.add(world.repo.parent)

    assert failures(world.run()) == [
        f"the worktree can't be removed ({world.repo.parent} is read-only)"
    ]


def test_a_worktree_git_cannot_find_fails(world: World) -> None:
    del world.probe.answers[TOP_LEVEL]

    (failed,) = failures(world.run())

    assert "worktree can't be found" in failed
    assert "git rev-parse --show-toplevel: exit status 1" in failed


def test_a_repository_git_refuses_fails_instead_of_skipping(world: World) -> None:
    world.probe.common = GitError("git rev-parse: unknown repository extension")

    report = world.run()

    (failed,) = failures(report)
    assert failed.startswith("git cannot inspect this repository")
    assert "unknown repository extension" in failed
    assert report.notes == ()
    assert len(report.results) == PROTECTED - REPOSITORY_CHECKS + 1
    assert not report.ok


def test_outside_a_repository_the_repository_checks_are_skipped(world: World) -> None:
    world.probe.common = None
    world.probe.writable_paths |= {
        world.git_dir / "objects" / "pack",
        world.repo.parent,
    }

    report = world.run()

    assert failures(report) == []
    assert report.notes == (NOT_IN_A_REPOSITORY,)
    assert len(report.results) == PROTECTED - REPOSITORY_CHECKS
    assert report.ok


@pytest.mark.parametrize("name", [".credentials.json"])
def test_a_readable_secret_in_the_account_dir_fails(world: World, name: str) -> None:
    secret = world.personal / name
    world.probe.readable_paths.add(secret)

    assert failures(world.run()) == [f"{secret} is not readable (account personal)"]


def test_a_readable_state_file_fails(world: World) -> None:
    state = world.home / ".claude.json"
    world.probe.readable_paths.add(state)

    assert failures(world.run()) == [f"{state} is not readable (account personal)"]


def test_a_secret_that_does_not_exist_is_not_checked(world: World) -> None:
    gone = world.personal / ".credentials.json"
    world.probe.existing.remove(gone)
    world.probe.readable_paths.add(gone)

    assert failures(world.run()) == []


def test_a_readable_peer_dir_fails(world: World) -> None:
    world.probe.readable_paths.add(world.bravo)

    assert failures(world.run()) == [f"{world.bravo} is not readable (account bravo)"]


def test_a_readable_peer_file_fails(world: World) -> None:
    world.env["ATC_ACCOUNT"] = "bravo"
    world.probe.readable_paths.add(world.home / ".claude.json")

    assert failures(world.run()) == [
        f"{world.home / '.claude.json'} is not readable (account personal)"
    ]


def test_the_current_account_is_not_its_own_peer(world: World) -> None:
    world.probe.existing.add(world.personal)
    world.probe.readable_paths.add(world.personal)

    assert failures(world.run()) == []


def test_an_unidentified_account_fails_and_every_account_is_a_peer(
    world: World,
) -> None:
    world.env.clear()
    world.env["CLAUDE_CONFIG_DIR"] = "/somewhere/else"
    world.probe.existing.add(world.personal)
    world.probe.readable_paths.add(world.personal)

    report = world.run()

    failed = failures(report)
    assert len(failed) == 2
    assert "cannot tell which account this session is in" in failed[0]
    assert "ATC_ACCOUNT is unset" in failed[0]
    assert (
        "CLAUDE_CONFIG_DIR=/somewhere/else is not an account's directory" in failed[0]
    )
    assert failed[1] == f"{world.personal} is not readable (account personal)"
    assert len(report.notes) == 1
    assert "instructions and skills checks are skipped" in report.notes[0]


def test_the_unidentified_account_message_is_accurate_without_the_variable(
    home: Path, dirs: Dirs
) -> None:
    text = REGISTRY.replace("dir: ~/.claude", "dir: ~/.claude-personal")
    world = make_world(home, dirs, parse_registry(text, home / "m.yaml", home))
    world.env["ATC_ACCOUNT"] = "gone"

    (failed, *_) = failures(world.run())

    assert "ATC_ACCOUNT=gone names no account" in failed
    assert "CLAUDE_CONFIG_DIR is unset and no account uses the default directory" in (
        failed
    )


@pytest.mark.parametrize(
    ("directory", "label"),
    [
        ("config_dir", "configuration"),
        ("conf_d", "config sources"),
        ("state_dir", "state"),
        ("runtime_dir", "runtime"),
    ],
)
def test_a_writable_atc_directory_fails(
    world: World, directory: str, label: str
) -> None:
    path = {
        "config_dir": world.dirs.config_dir,
        "conf_d": world.dirs.conf_d,
        "state_dir": world.dirs.state_dir,
        "runtime_dir": world.dirs.runtime_dir,
    }[directory]
    world.probe.writable_paths.add(path)

    assert failures(world.run()) == [f"atc's {label} directory {path} is not writable"]


def test_a_writable_install_dir_fails(world: World) -> None:
    world.probe.writable_paths.add(world.install_dir)

    assert failures(world.run()) == [
        f"atc's install directory {world.install_dir} is not writable"
    ]


def test_a_writable_python_dir_fails(world: World) -> None:
    world.probe.writable_paths.add(world.python_dir)

    assert failures(world.run()) == [
        f"atc's Python directory {world.python_dir} is not writable"
    ]


def test_a_python_dir_that_is_the_install_dir_passes_and_fails_alike(
    world: World,
) -> None:
    def run() -> Report:
        return run_checks(
            world.probe,
            registry=world.registry,
            dirs=world.dirs,
            install_dir=world.install_dir,
            python_dir=world.install_dir,
            home=world.home,
            env=world.env,
        )

    assert failures(run()) == []
    assert len(run().results) == PROTECTED

    world.probe.writable_paths.add(world.install_dir)
    assert failures(run()) == [
        f"atc's install directory {world.install_dir} is not writable",
        f"atc's Python directory {world.install_dir} is not writable",
    ]


def test_a_writable_instructions_target_fails(world: World) -> None:
    target = world.dirs.conf_d / "assets" / "AGENTS.md"
    world.probe.writable_paths.add(target)

    assert failures(world.run()) == [
        f"{target} is not writable (linked from {world.personal / 'CLAUDE.md'})"
    ]


def test_a_writable_skill_target_fails(world: World) -> None:
    target = world.dirs.conf_d / "skills" / "grill-me"
    world.probe.writable_paths.add(target)
    entry = world.personal / "skills" / "grill-me"

    assert failures(world.run()) == [f"{target} is not writable (linked from {entry})"]


def test_a_skill_link_the_registry_does_not_select_is_checked(world: World) -> None:
    entry = world.personal / "skills" / "legacy"
    checkout = world.home / "claude-stuff" / "skills" / "legacy"
    world.probe.listings[world.personal / "skills"].append(entry)
    world.probe.links[entry] = checkout
    world.probe.writable_paths.add(checkout)

    report = world.run()

    assert failures(report) == [f"{checkout} is not writable (linked from {entry})"]
    assert len(report.results) == PROTECTED + 1


def test_a_relative_link_target_is_taken_from_the_links_directory(
    world: World,
) -> None:
    entry = world.personal / "CLAUDE.md"
    target = world.personal / "notes" / "AGENTS.md"
    world.probe.links[entry] = Path("notes") / "AGENTS.md"
    world.probe.writable_paths.add(target)

    assert failures(world.run()) == [f"{target} is not writable (linked from {entry})"]


def test_an_instructions_file_that_is_not_a_link_must_not_be_writable(
    world: World,
) -> None:
    entry = world.personal / "CLAUDE.md"
    del world.probe.links[entry]
    world.probe.existing.add(entry)

    assert failures(world.run()) == []

    world.probe.writable_paths.add(entry)
    assert failures(world.run()) == [f"{entry} is not writable (not a link)"]


def test_a_missing_instructions_file_is_not_checked(world: World) -> None:
    entry = world.personal / "CLAUDE.md"
    del world.probe.links[entry]
    world.probe.writable_paths.add(world.personal)

    report = world.run()

    assert failures(report) == []
    assert len(report.results) == PROTECTED - 1


def test_a_skill_that_is_not_a_link_must_not_be_writable(world: World) -> None:
    entry = world.personal / "skills" / "own"
    world.probe.listings[world.personal / "skills"].append(entry)
    world.probe.existing.add(entry)
    world.probe.writable_paths.add(entry)

    assert failures(world.run()) == [f"{entry} is not writable (not a link)"]


def test_a_dangling_skill_link_under_a_writable_parent_fails(world: World) -> None:
    entry = world.personal / "skills" / "legacy"
    parent = world.home / "claude-stuff" / "skills"
    target = parent / "legacy"
    world.probe.listings[world.personal / "skills"].append(entry)
    world.probe.links[entry] = target
    world.probe.writable_paths.add(parent)

    assert failures(world.run()) == [
        f"{target} is missing and could be created in {parent} (linked from {entry})"
    ]


def test_a_link_to_a_dangling_link_is_followed(world: World) -> None:
    entry = world.personal / "skills" / "legacy"
    middle = world.home / "links" / "legacy"
    target = world.home / "claude-stuff" / "legacy"
    world.probe.listings[world.personal / "skills"].append(entry)
    world.probe.links |= {entry: middle, middle: target}
    world.probe.existing.add(middle)
    world.probe.writable_paths.add(target.parent)

    assert failures(world.run()) == [
        f"{middle} is missing and could be created in {target.parent} "
        + f"(linked from {entry})"
    ]


def test_a_missing_state_dir_under_a_writable_parent_fails(world: World) -> None:
    state = world.dirs.state_dir
    world.probe.existing.remove(state)
    world.probe.writable_paths.add(state.parent)

    assert failures(world.run()) == [
        f"atc's state directory {state} is missing and could be created in "
        + str(state.parent)
    ]


def test_a_missing_path_under_a_read_only_parent_passes(world: World) -> None:
    state = world.dirs.state_dir
    world.probe.existing.remove(state)
    world.probe.existing.add(state.parent.parent)

    report = world.run()

    assert failures(report) == []
    assert CheckResult(
        True, f"atc's state directory {state} is missing and can't be created"
    ) in (report.results)


def test_a_missing_path_with_nothing_above_it_fails(world: World) -> None:
    runtime = world.dirs.runtime_dir
    world.probe.existing.remove(runtime)

    assert failures(world.run()) == [
        f"atc's runtime directory {runtime} is missing, and no directory above it "
        + "exists"
    ]


def test_a_missing_path_under_a_dangling_link_is_checked_where_it_leads(
    world: World,
) -> None:
    state = world.dirs.state_dir
    elsewhere = world.home / "elsewhere"
    world.probe.existing.remove(state)
    world.probe.existing.add(state.parent)
    world.probe.links[state.parent] = elsewhere / "state"
    world.probe.writable_paths.add(elsewhere)

    assert failures(world.run()) == [
        f"atc's state directory {state} is missing and could be created in "
        + str(elsewhere)
    ]


def test_a_loop_of_links_fails(world: World) -> None:
    install = world.install_dir
    other = install.parent / "other"
    world.probe.links |= {install: other, other: install}
    world.probe.existing.add(other)
    world.probe.writable_paths.add(install.parent)

    assert failures(world.run()) == [
        f"atc's install directory {install} leads through a loop of symbolic "
        + "links, or too many of them, so it is unchecked"
    ]


@pytest.mark.parametrize("hole", ["dangling-ancestor", "loop"])
def test_links_on_disk_are_followed_before_the_verdict(
    tmp_path: Path,
    home: Path,
    dirs: Dirs,
    registry: Registry,
    monkeypatch: pytest.MonkeyPatch,
    hole: str,
) -> None:
    room = tmp_path / "room"
    room.mkdir()
    if hole == "dangling-ancestor":
        (room / "a").symlink_to(room / "c")
        python = room / "a" / "python"
        expected = (
            f"atc's Python directory {python} is missing and could be created in "
            + str(room)
        )
    else:
        (room / "x").symlink_to(room / "y")
        (room / "y").symlink_to(room / "x")
        python = room / "x"
        expected = (
            f"atc's Python directory {python} leads through a loop of symbolic "
            + "links, or too many of them, so it is unchecked"
        )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))

    report = run_checks(
        RealProbe(),
        registry=registry,
        dirs=dirs,
        install_dir=tmp_path / "atc",
        python_dir=python,
        home=home,
        env={"ATC_ACCOUNT": "personal"},
    )

    assert CheckResult(False, expected) in report.results


def test_a_missing_pack_directory_under_writable_objects_fails(world: World) -> None:
    pack = world.git_dir / "objects" / "pack"
    world.probe.existing.remove(pack)

    assert failures(world.run()) == [
        f"{pack} is missing and could be created in {world.git_dir / 'objects'}"
    ]


def test_a_skills_directory_that_cannot_be_listed_fails(world: World) -> None:
    skills = world.personal / "skills"
    world.probe.unlistable.add(skills)

    (failed,) = failures(world.run())

    assert failed.startswith(f"{skills} cannot be listed, so its skills are unchecked")
    assert "Permission denied" in failed


def test_a_dangling_link_on_disk_fails_when_its_target_could_be_made(
    tmp_path: Path,
    home: Path,
    dirs: Dirs,
    registry: Registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skills = home / ".claude" / "skills"
    skills.mkdir(parents=True)
    checkout = tmp_path / "claude-stuff" / "skills"
    checkout.mkdir(parents=True)
    (skills / "foo").symlink_to(checkout / "foo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))

    report = run_checks(
        RealProbe(),
        registry=registry,
        dirs=dirs,
        install_dir=tmp_path / "atc",
        python_dir=tmp_path / "python",
        home=home,
        env={"ATC_ACCOUNT": "personal"},
    )

    target = checkout / "foo"
    assert CheckResult(
        False,
        f"{target} is missing and could be created in {checkout} "
        + f"(linked from {skills / 'foo'})",
    ) in (report.results)


def test_home_is_in_a_sandbox_only_when_it_is_not_writable(home: Path) -> None:
    probe = FakeProbe(existing={home})
    assert in_sandbox(probe, home)

    probe.writable_paths.add(home)
    assert not in_sandbox(probe, home)


def named(registry: Registry, env: dict[str, str], home: Path) -> str | None:
    found = current_account(registry, env, home)
    return None if found is None else found.name


def test_the_current_account_comes_from_atc_account_first(
    registry: Registry, home: Path
) -> None:
    env = {"ATC_ACCOUNT": "bravo", "CLAUDE_CONFIG_DIR": str(home / ".claude")}

    assert named(registry, env, home) == "bravo"


def test_the_current_account_falls_back_to_claude_config_dir(
    registry: Registry, home: Path
) -> None:
    bravo = str(home / ".config" / "bravo" / "claude")

    assert named(registry, {"CLAUDE_CONFIG_DIR": bravo}, home) == "bravo"
    assert named(
        registry, {"ATC_ACCOUNT": "gone", "CLAUDE_CONFIG_DIR": bravo}, home
    ) == ("bravo")


def test_claude_config_dir_is_compared_normalized(
    registry: Registry, home: Path
) -> None:
    real = home / ".config" / "bravo" / "claude"
    real.mkdir(parents=True)
    link = home / "via-link"
    link.symlink_to(real)

    for spelling in (f"{link}/", f"{home}/.config/bravo/../bravo/claude/"):
        assert named(registry, {"CLAUDE_CONFIG_DIR": spelling}, home) == "bravo"


def test_an_unset_or_empty_variable_means_the_default_directory(
    registry: Registry, home: Path
) -> None:
    assert named(registry, {}, home) == "personal"
    assert named(registry, {"ATC_ACCOUNT": "", "CLAUDE_CONFIG_DIR": ""}, home) == (
        "personal"
    )


@pytest.mark.parametrize("configured", ["/somewhere/else", "relative/dir"])
def test_a_directory_that_is_no_account_gives_none(
    registry: Registry, home: Path, configured: str
) -> None:
    assert named(registry, {"CLAUDE_CONFIG_DIR": configured}, home) is None


def test_no_account_in_the_default_directory_gives_none(home: Path) -> None:
    text = REGISTRY.replace("dir: ~/.claude", "dir: ~/.claude-personal")

    registry = parse_registry(text, home / "machine.yaml", home)

    assert named(registry, {}, home) is None


def test_readable_contents_sees_through_what_a_sandbox_hides(tmp_path: Path) -> None:
    probe = RealProbe()
    filled = tmp_path / "token"
    _ = filled.write_text("secret", encoding="utf-8")
    empty = tmp_path / "empty"
    _ = empty.write_text("", encoding="utf-8")
    account = tmp_path / "account"
    account.mkdir()

    assert probe.readable_contents(filled)
    assert not probe.readable_contents(empty)
    assert not probe.readable_contents(tmp_path / "gone")
    assert not probe.readable_contents(Path(os.devnull))
    assert not probe.readable_contents(account)
    _ = (account / "settings.json").write_text("{}", encoding="utf-8")
    assert probe.readable_contents(account)


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read anything")
def test_readable_contents_needs_the_read_permission(tmp_path: Path) -> None:
    probe = RealProbe()
    hidden = tmp_path / "hidden"
    _ = hidden.write_text("secret", encoding="utf-8")
    account = tmp_path / "account"
    account.mkdir()
    _ = (account / "settings.json").write_text("{}", encoding="utf-8")
    hidden.chmod(0o000)
    account.chmod(0o000)
    try:
        assert not probe.readable_contents(hidden)
        assert not probe.readable_contents(account)
    finally:
        hidden.chmod(0o600)
        account.chmod(0o700)


def test_the_real_probe_asks_the_file_system(tmp_path: Path) -> None:
    probe = RealProbe()
    skills = tmp_path / "skills"
    skills.mkdir()
    dangling = skills / "dangling"
    dangling.symlink_to("target")
    file = skills / "file"
    _ = file.write_text("x", encoding="utf-8")

    assert probe.writable(skills)
    assert not probe.writable(skills / "gone")
    assert probe.exists(dangling)
    assert not probe.exists(skills / "gone")
    assert probe.readlink(dangling) == Path("target")
    assert probe.readlink(file) is None
    assert probe.readlink(skills / "gone") is None
    assert probe.entries(skills) == [dangling, file]
    assert probe.entries(skills / "gone") == []
    with pytest.raises(NotADirectoryError):
        _ = probe.entries(file)


def test_the_real_probe_runs_git_in_the_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _ = (repo / "file").write_text("x", encoding="utf-8")
    _ = git_repo(repo)
    monkeypatch.chdir(repo)
    probe = RealProbe()

    assert probe.git(["rev-parse", "--is-inside-work-tree"]) == "true"
    assert probe.common_dir() == repo.resolve() / ".git"
    with pytest.raises(GitError):
        _ = probe.git(["config", "--get", "no.such.key"])


def test_the_real_probe_finds_no_repository_outside_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))

    assert RealProbe().common_dir() is None


def test_the_real_probe_raises_for_a_repository_git_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _ = run_git(["init", "-q"], cwd=repo)
    config = repo / ".git" / "config"
    _ = config.write_text(
        config.read_text(encoding="utf-8")
        + "[core]\n\trepositoryformatversion = 1\n[extensions]\n\tnosuchext = true\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)

    with pytest.raises(GitError, match="nosuchext"):
        _ = RealProbe().common_dir()


def use_probe(monkeypatch: pytest.MonkeyPatch, world: World) -> None:
    # The command checks the environment it runs in, which this one is.
    world.probe.existing |= {
        world.home,
        Path(sys.prefix),
        Path(sys.base_prefix).resolve(),
    }
    # `atc.cli.check` names the command in the package, not the module.
    module = import_module("atc.cli.check")
    monkeypatch.setattr(module, "RealProbe", lambda: world.probe)
    monkeypatch.setenv("ATC_ACCOUNT", "personal")


def test_outside_a_sandbox_exits_2_before_reading_anything(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["check"]) == USAGE

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error:")
    assert "home directory is writable" in captured.err
    assert "machine.yaml" not in captured.err


def test_a_protected_session_exits_0_with_a_pass_line_per_check(
    world: World,
    write_registry: RegistryWriter,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _ = write_registry(REGISTRY)
    use_probe(monkeypatch, world)
    assert not world.dirs.conf_d.exists()

    status = main(["check"])

    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert status == OK
    assert captured.err == ""
    assert len(lines) == PROTECTED
    assert all(line.startswith("PASS  ") for line in lines)
    assert f"PASS  {world.git_dir}/objects/pack is read-only" in lines
    assert f"PASS  atc's install directory {sys.prefix} is not writable" in lines
    python = Path(sys.base_prefix).resolve()
    assert f"PASS  atc's Python directory {python} is not writable" in lines


def test_a_failed_check_exits_3(
    world: World,
    write_registry: RegistryWriter,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _ = write_registry(REGISTRY)
    world.probe.writable_paths.add(world.git_dir / "packed-refs")
    use_probe(monkeypatch, world)

    status = main(["check"])

    out = capsys.readouterr().out
    assert status == CHECK
    assert f"FAIL  {world.git_dir}/packed-refs is read-only\n" in out
    assert out.count("FAIL  ") == 1


def test_a_repository_git_refuses_exits_3(
    world: World,
    write_registry: RegistryWriter,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _ = write_registry(REGISTRY)
    world.probe.common = GitError("git rev-parse: unknown repository extension")
    use_probe(monkeypatch, world)

    status = main(["check"])

    out = capsys.readouterr().out
    assert status == CHECK
    assert "FAIL  git cannot inspect this repository" in out
    assert "unknown repository extension" in out


def test_notes_are_plain_lines_and_do_not_fail_the_check(
    world: World,
    write_registry: RegistryWriter,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _ = write_registry(REGISTRY)
    world.probe.common = None
    use_probe(monkeypatch, world)

    status = main(["check"])

    lines = capsys.readouterr().out.splitlines()
    assert status == OK
    assert lines[0] == NOT_IN_A_REPOSITORY
    assert all(line.startswith("PASS  ") for line in lines[1:])
    assert len(lines) == PROTECTED - REPOSITORY_CHECKS + 1


def test_output_is_plain_text_when_not_a_terminal(
    world: World,
    write_registry: RegistryWriter,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _ = write_registry(REGISTRY)
    world.probe.writable_paths.add(world.git_dir / "packed-refs")
    use_probe(monkeypatch, world)
    monkeypatch.setenv("TERM", "xterm-256color")

    _ = main(["check"])

    out = capsys.readouterr().out
    assert "\x1b" not in out
    assert "PASS  " in out
    assert "FAIL  " in out


def test_a_missing_registry_is_a_config_error(
    world: World,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    use_probe(monkeypatch, world)

    assert main(["check"]) == CONFIG

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error:")
    assert "machine.yaml" in captured.err
