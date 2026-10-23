"""What is specific to one coding agent."""

from pathlib import Path
from typing import Protocol

from atc.backends.claude import ClaudeBackend
from atc.errors import RegistryError
from atc.registry import Account


class Backend(Protocol):
    """Where an agent keeps an account's files."""

    @property
    def name(self) -> str:
        """The agent's name, as the registry spells it."""
        ...

    def default_dir(self) -> Path:
        """Return the directory the agent uses when nothing selects another."""
        ...

    def is_default(self, account: Account) -> bool:
        """Say whether ``account`` lives in the agent's default directory."""
        ...

    def files(self, account: Account) -> tuple[Path, ...]:
        """Return the files ``account`` owns outside its directory."""
        ...

    def secrets(self, account: Account) -> tuple[Path, ...]:
        """Return the credential and state files its own sessions must not read."""
        ...


def backend_for(agent: str, home: Path) -> Backend:
    """
    Find the backend of the agent called ``agent``.

    Raises:
        RegistryError: atc has no backend for ``agent``.

    """
    if agent == "claude":
        return ClaudeBackend(home)
    raise RegistryError(f"unknown agent '{agent}'; atc supports claude")
