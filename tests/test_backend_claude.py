"""The Claude backend: where an account keeps its files and settings."""

from pathlib import Path

import pytest

from atc.backends import backend_for
from atc.backends.claude import ClaudeBackend
from atc.errors import RegistryError, UnitError
from atc.jsonval import dumps
from atc.registry import Registry, parse_registry

REGISTRY = """\
version: 1
default-account: personal
accounts:
  personal: {agent: claude, dir: ~/.claude}
  bravo: {agent: claude, dir: ~/.config/bravo/claude}
"""


def load(home: Path, text: str = REGISTRY) -> Registry:
    return parse_registry(text, Path("machine.yaml"), home)


def test_the_default_dir_is_under_home(home: Path) -> None:
    backend = ClaudeBackend(home)

    assert backend.name == "claude"
    assert backend.default_dir() == home / ".claude"


def test_the_default_dir_account_owns_the_home_state_file(home: Path) -> None:
    registry = load(home)
    backend = ClaudeBackend(home)

    assert backend.is_default(registry.account("personal"))
    assert backend.files(registry.account("personal")) == (home / ".claude.json",)
    assert not backend.is_default(registry.account("bravo"))
    assert backend.files(registry.account("bravo")) == ()


def test_secrets_for_the_default_and_other_dirs(home: Path) -> None:
    registry = load(home)
    backend = ClaudeBackend(home)
    d = home / ".config" / "bravo" / "claude"

    assert backend.secrets(registry.account("personal")) == (
        home / ".claude" / ".credentials.json",
        home / ".claude.json",
    )
    assert backend.secrets(registry.account("bravo")) == (
        d / ".credentials.json",
        d / ".claude.json",
    )


def test_default_dir_detection_survives_a_trailing_slash(home: Path) -> None:
    text = (
        "version: 1\ndefault-account: a\n"
        "accounts: {a: {agent: claude, dir: ~/.claude/}}\n"
    )
    account = load(home, text).account("a")

    assert ClaudeBackend(home).is_default(account)


def test_default_dir_detection_follows_a_symlinked_home(
    home: Path, tmp_path: Path
) -> None:
    link = tmp_path / "link"
    link.symlink_to(home)
    account = load(home).account("personal")

    backend = ClaudeBackend(link)

    assert backend.is_default(account)


def test_backend_for_names_the_agents_it_supports(home: Path) -> None:
    assert isinstance(backend_for("claude", home), ClaudeBackend)
    with pytest.raises(RegistryError, match=r"'nope'.*claude"):
        _ = backend_for("nope", home)


def test_settings_live_in_the_account_directory(home: Path) -> None:
    account = load(home).account("bravo")

    path = ClaudeBackend(home).settings_file(account)

    assert path == home / ".config" / "bravo" / "claude" / "settings.json"


def test_render_is_the_tools_json_output(home: Path) -> None:
    assert ClaudeBackend(home).render({"b": 1, "a": [True]}) == dumps(
        {"b": 1, "a": [True]}
    )


def test_parse_reads_an_object_strictly(home: Path) -> None:
    backend = ClaudeBackend(home)

    assert backend.parse('{"a": {"b": 1}}', "live") == {"a": {"b": 1}}
    for text in ("{nope", '{"a": NaN}', '{"a": 1, "a": 2}'):
        with pytest.raises(UnitError, match=r"^live: invalid JSON"):
            _ = backend.parse(text, "live")


def test_parse_refuses_what_is_not_an_object(home: Path) -> None:
    with pytest.raises(UnitError, match=r"^live: .*object.*array"):
        _ = ClaudeBackend(home).parse("[1]", "live")


def test_the_instructions_file_lives_in_the_account_directory(home: Path) -> None:
    registry = load(home)
    backend = ClaudeBackend(home)

    assert backend.instructions_file(registry.account("personal")) == (
        home / ".claude" / "CLAUDE.md"
    )
    assert backend.instructions_file(registry.account("bravo")) == (
        home / ".config" / "bravo" / "claude" / "CLAUDE.md"
    )
