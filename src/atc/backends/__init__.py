"""What is specific to one coding agent."""

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

from atc.backends.claude import ClaudeBackend
from atc.errors import RegistryError
from atc.jsonval import JsonObject
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

    def settings_file(self, account: Account) -> Path:
        """Return the file ``account``'s generated settings are written to."""
        ...

    def instructions_file(self, account: Account) -> Path:
        """Return the file ``account``'s instructions are read from."""
        ...

    def skills_dir(self, account: Account) -> Path:
        """Return the directory ``account``'s skills are read from."""
        ...

    def environment(self, account: Account, env: Mapping[str, str]) -> dict[str, str]:
        """Return ``env`` changed so that the agent runs in ``account``."""
        ...

    def argv(self, bin: str, fragment: JsonObject, args: Sequence[str]) -> list[str]:
        """
        Return the command that starts the agent at ``bin``.

        ``fragment`` is the settings for this session only, and ``args`` are the
        agent's own arguments, which come last.
        """
        ...

    def session_fragment(self, common_dir: Path) -> JsonObject:
        """
        Return the settings that protect the repository's packed history.

        ``common_dir`` is the repository's git directory. The result is empty
        when there is nothing in it to protect yet.
        """
        ...

    def render(self, settings: JsonObject) -> str:
        """Return ``settings`` as the text of that file."""
        ...

    def parse(self, text: str, origin: str) -> JsonObject:
        """
        Read the text of a settings file, naming ``origin`` in errors.

        Raises:
            UnitError: ``text`` is not a JSON object.

        """
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
