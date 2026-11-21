"""What ``atc sync`` remembers about an account between runs."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from atc.errors import UnitError
from atc.fsutil import atomic_write, make_private_dir
from atc.jsonval import JsonObject, dumps, parse_json
from atc.sources import Source

SETTINGS_RECORD = "settings.json"
LINKS_RECORD = "links.json"


@dataclass(frozen=True, slots=True)
class SyncState:
    """
    The record that ``atc sync`` keeps for one account, under ``state_dir``.

    ``root`` holds the settings it last wrote, the links it made and the
    ``backups_dir`` of what it replaced. With a ``project``, they are those of
    the project's configuration directory for the account. ``rendered_dir``
    holds the templated assets rendered for the account, whichever directory
    the record is for. It all stays private to the user, because settings can
    hold secrets.
    """

    state_dir: Path
    account: str
    project: str | None = None

    @property
    def root(self) -> Path:
        account = self.state_dir / "sync" / self.account
        return account if self.project is None else account / "projects" / self.project

    @property
    def backups_dir(self) -> Path:
        return self.root / "backups"

    @property
    def rendered_dir(self) -> Path:
        return self.state_dir / "sync" / self.account / "assets"

    def rendered(self, source: Source) -> Path:
        """Give where the templated assets of ``source`` are rendered."""
        return self.rendered_dir / source.name if source.name else self.rendered_dir

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

    def links(self) -> dict[str, str]:
        """
        Return the links atc made, as the path of each entry and its target.

        Both are absolute. A record that is missing, unreadable or not an object
        of strings is no record, so a damaged one never claims a link for atc.
        """
        path = self.root / LINKS_RECORD
        try:
            recorded = parse_json(path.read_bytes().decode("utf-8"), str(path))
        except (OSError, UnicodeDecodeError, UnitError):
            return {}
        if not isinstance(recorded, dict):
            return {}
        links: dict[str, str] = {}
        for entry, target in recorded.items():
            if not isinstance(target, str):
                return {}
            links[entry] = target
        return links

    def record_links(self, links: Mapping[str, str]) -> None:
        """
        Remember ``links`` as the ones atc made, replacing the record.

        Raises:
            OSError: the record cannot be written.

        """
        make_private_dir(self.root)
        atomic_write(self.root / LINKS_RECORD, dumps(dict(links)))
