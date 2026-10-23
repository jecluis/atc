"""The usage records that sessions leave for each account."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from atc.errors import UnitError
from atc.jsonval import JsonValue, parse_json
from atc.paths import Dirs

USAGE_DIR = "usage"
"""The directory of ``state_dir`` that holds one record file for each account."""

_MAX_PERCENT = 100


@dataclass(frozen=True, slots=True)
class UsageRecord:
    """
    What was last recorded for an account.

    ``recorded_at`` is when, with its time zone. ``five_hour_pct`` is how much
    of the five-hour limit had been used then, from 0 to 100, or ``None`` when
    the record has no figure for it.
    """

    recorded_at: datetime
    five_hour_pct: float | None


class _InvalidError(Exception):
    """A record is not what the statusline writes. The message says what."""


def read_usage(dirs: Dirs, account: str) -> tuple[UsageRecord | None, str | None]:
    """
    Read the record of ``account``, which the statusline keeps in ``state_dir``.

    The file is ``usage/<account>.json`` and holds ``recorded_at``, an ISO 8601
    time with a UTC offset, and ``rate_limits``, as Claude Code gave it to the
    statusline. The five-hour figure is ``rate_limits.five_hour.used_percentage``
    and may be missing. Anyone who can write the state directory can write the
    file, so nothing in it is trusted.

    Returns the record and no warning; ``(None, None)`` when there is no file;
    and ``(None, warning)`` when the file cannot be read or is not a valid
    record, which includes a time in the future and a percentage outside 0 to
    100. The warning names the file, and the JSON pointer where there is one.
    """
    path = dirs.state_dir / USAGE_DIR / f"{account}.json"
    try:
        text = path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        return None, None
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"{path}: cannot be read: {exc}"
    try:
        document = parse_json(text, str(path))
    except UnitError as exc:
        return None, str(exc)
    except RecursionError:
        # The parser gives up on deeply nested arrays and objects this way.
        return None, f"{path}: invalid JSON: nested too deeply"
    try:
        return _record(document), None
    except _InvalidError as exc:
        return None, f"{path}: {exc}"


def format_age(age: timedelta) -> str:
    """
    Say how long ago something was, with its two largest units.

    The result is ``just now``, ``5m ago``, ``3h 20m ago`` or ``2d 4h ago``. An
    age below a minute, including a negative one, is ``just now``.
    """
    minutes = int(age.total_seconds()) // 60
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes}m ago"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m ago"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h ago"


def _record(document: JsonValue) -> UsageRecord:
    """
    Check ``document`` and build the record from it.

    Raises:
        _InvalidError: it is not a valid record.

    """
    if not isinstance(document, dict):
        raise _InvalidError("must be a JSON object")
    return UsageRecord(
        recorded_at=_recorded_at(document.get("recorded_at")),
        five_hour_pct=_five_hour(document.get("rate_limits")),
    )


def _recorded_at(value: JsonValue) -> datetime:
    """Read the time of a record, which must not be in the future."""
    pointer = "/recorded_at"
    match value:
        case None:
            raise _InvalidError(f"{pointer}: missing")
        case str():
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError:
                parsed = None
            if parsed is None or parsed.utcoffset() is None:
                message = f"{pointer}: not an ISO 8601 time with a UTC offset"
                raise _InvalidError(message)
            if parsed > datetime.now(UTC):
                raise _InvalidError(f"{pointer}: is in the future")
            return parsed
        case bool() | int() | float() | list() | dict():
            raise _InvalidError(f"{pointer}: must be a string")


def _five_hour(limits: JsonValue) -> float | None:
    """Read the five-hour percentage, if ``limits`` has one."""
    window = _member(limits, "five_hour", "/rate_limits")
    return _percentage(
        None
        if window is None
        else _member(window, "used_percentage", "/rate_limits/five_hour")
    )


def _member(value: JsonValue, key: str, pointer: str) -> JsonValue:
    """Read ``key`` of the object ``value`` found at ``pointer``, ``None`` if absent."""
    match value:
        case None:
            return None
        case dict():
            return value.get(key)
        case str() | bool() | int() | float() | list():
            raise _InvalidError(f"{pointer}: must be an object")


def _percentage(value: JsonValue) -> float | None:
    """Read a percentage, which is a number from 0 to 100."""
    pointer = "/rate_limits/five_hour/used_percentage"
    match value:
        case None:
            return None
        case bool() | str() | list() | dict():
            raise _InvalidError(f"{pointer}: must be a number")
        case int() | float():
            # Compared before it becomes a float: a huge integer cannot be one.
            if not 0 <= value <= _MAX_PERCENT:
                raise _InvalidError(f"{pointer}: outside 0 to {_MAX_PERCENT}")
            return float(value)
