"""Claude Code: where it keeps an account's files."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from atc.errors import UnitError
from atc.guard import protected_paths
from atc.jsonval import JsonObject, JsonValue, canonical, dumps, json_kind, parse_json
from atc.registry import Account, normalize_path

CONFIG_DIR_VARIABLE = "CLAUDE_CONFIG_DIR"
"""The variable that selects Claude Code's configuration directory."""


@dataclass(frozen=True, slots=True)
class ClaudeBackend:
    """Claude Code, for the user whose home directory is ``home``."""

    home: Path

    @property
    def name(self) -> str:
        return "claude"

    def default_dir(self) -> Path:
        """``~/.claude``, normalized the way the registry normalizes ``dir``."""
        return normalize_path("~/.claude", self.home)

    def is_default(self, account: Account) -> bool:
        return account.dir == self.default_dir()

    def files(self, account: Account) -> tuple[Path, ...]:
        """``~/.claude.json`` for the default directory, and nothing otherwise."""
        return (self._home_state(),) if self.is_default(account) else ()

    def secrets(self, account: Account) -> tuple[Path, ...]:
        """Return the credentials in the account's directory, and its state file."""
        state = (
            self._home_state()
            if self.is_default(account)
            else account.dir / ".claude.json"
        )
        return (account.dir / ".credentials.json", state)

    def settings_file(self, account: Account) -> Path:
        """``settings.json`` in the account's directory."""
        return account.dir / "settings.json"

    def instructions_file(self, account: Account) -> Path:
        """``CLAUDE.md`` in the account's directory."""
        return account.dir / "CLAUDE.md"

    def skills_dir(self, account: Account) -> Path:
        """``skills`` in the account's directory."""
        return account.dir / "skills"

    def environment(self, account: Account, env: Mapping[str, str]) -> dict[str, str]:
        """
        Select the account's directory with ``CLAUDE_CONFIG_DIR``.

        The default directory is selected by leaving the variable out.
        """
        changed = dict(env)
        if self.is_default(account):
            _ = changed.pop(CONFIG_DIR_VARIABLE, None)
        else:
            changed[CONFIG_DIR_VARIABLE] = str(account.dir)
        return changed

    def argv(self, bin: str, fragment: JsonObject, args: Sequence[str]) -> list[str]:
        """Pass the fragment as ``--settings``, unless it is empty."""
        settings = ["--settings", canonical(fragment)] if fragment else []
        return [bin, *settings, *args]

    def session_fragment(self, common_dir: Path) -> JsonObject:
        """
        Deny writes to the repository's packed history, to commands and to Claude.

        Commands are held by the sandbox's ``denyWrite``, and Claude's file tools
        by ``Edit`` rules. The rule of a path starts with ``//``: ``/`` plus an
        absolute path, which is how Claude spells an absolute path in a rule.
        """
        paths = protected_paths(common_dir)
        if not paths:
            return {}
        deny_write: list[JsonValue] = [str(path) for path in paths]
        rules: list[JsonValue] = [_edit_rule(path) for path in paths]
        return {
            "sandbox": {"filesystem": {"denyWrite": deny_write}},
            "permissions": {"deny": rules},
        }

    def render(self, settings: JsonObject) -> str:
        return dumps(settings)

    def parse(self, text: str, origin: str) -> JsonObject:
        document = parse_json(text, origin)
        if not isinstance(document, dict):
            raise UnitError(
                f"{origin}: expected a JSON object, found {json_kind(document)}"
            )
        return document

    def _home_state(self) -> Path:
        return self.home / ".claude.json"


def _edit_rule(path: Path) -> str:
    """Write the rule that denies Claude's file tools the directory or file ``path``."""
    return f"Edit(/{path}/**)" if path.is_dir() else f"Edit(/{path})"
