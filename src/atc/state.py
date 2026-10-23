"""What ``atc sync`` remembers about an account between runs."""

from dataclasses import dataclass
from pathlib import Path

from atc.errors import UnitError
from atc.fsutil import atomic_write, make_private_dir
from atc.jsonval import JsonObject, dumps, parse_json

SETTINGS_RECORD = "settings.json"


@dataclass(frozen=True, slots=True)
class SyncState:
    """
    The record that ``atc sync`` keeps for one account, under ``state_dir``.

    ``root`` holds the settings it last wrote and the ``backups_dir`` of what it
    replaced. It stays private to the user, because settings can hold secrets.
    """

    state_dir: Path
    account: str

    @property
    def root(self) -> Path:
        return self.state_dir / "sync" / self.account

    @property
    def backups_dir(self) -> Path:
        return self.root / "backups"

    def last_settings(self) -> JsonObject | None:
        """
        Return the settings last written, or ``None`` when they cannot be told.

        A record that is missing, unreadable or not a JSON object is no record,
        so a damaged one is never trusted to say what atc wrote.
        """
        path = self.root / SETTINGS_RECORD
        try:
            recorded = parse_json(path.read_bytes().decode("utf-8"), str(path))
        except (OSError, UnicodeDecodeError, UnitError):
            return None
        return recorded if isinstance(recorded, dict) else None

    def record_settings(self, settings: JsonObject) -> None:
        """
        Remember ``settings`` as the ones last written.

        Raises:
            OSError: the record cannot be written.

        """
        make_private_dir(self.root)
        atomic_write(self.root / SETTINGS_RECORD, dumps(settings))
