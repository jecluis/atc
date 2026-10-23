"""Claude Code: where it keeps an account's files."""

from dataclasses import dataclass
from pathlib import Path

from atc.registry import Account, normalize_path


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

    def _home_state(self) -> Path:
        return self.home / ".claude.json"
