"""The usage records that sessions leave for each account."""

import errno
import math
import os
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from atc.errors import UnitError
from atc.jsonval import JsonValue, parse_json
from atc.paths import Dirs

USAGE_DIR = "usage"
"""The directory of ``state_dir`` that holds one record file for each account."""

MAX_RECORD_BYTES = 64 * 1024
"""The largest record file :func:`read_usage` reads; the statusline's is far smaller."""

FIVE_HOURS = timedelta(hours=5)
"""How long a record counts for choosing an account: its window then rolls over."""

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


class _UnreadableError(Exception):
    """A record file is not one to read. The message says why."""


def read_usage(dirs: Dirs, account: str) -> tuple[UsageRecord | None, str | None]:
    """
    Read the record of ``account``, which the statusline keeps in ``state_dir``.

    The file is ``usage/<account>.json`` and holds ``recorded_at``, an ISO 8601
    time with a UTC offset, and ``rate_limits``, as Claude Code gave it to the
    statusline. The five-hour figure is ``rate_limits.five_hour.used_percentage``
    and may be missing. Anyone who can write the state directory can write the
    file, so nothing in it is trusted: only a regular file of at most
    ``MAX_RECORD_BYTES`` is read, so a FIFO, a device, a directory or a
    symbolic link there is refused unread, and never waited for.

    Returns the record and no warning; ``(None, None)`` when there is no file;
    and ``(None, warning)`` when the file cannot be read or is not a valid
    record, which includes a time in the future. A percentage outside 0 to 100
    is clamped to the nearest end, and the record comes with a warning that says
    so; one that is not a finite number is not valid. The warning names the
    file, and the JSON pointer where there is one.
    """
    path = dirs.state_dir / USAGE_DIR / f"{account}.json"
    try:
        text = _read_text(path)
    except FileNotFoundError:
        return None, None
    except (OSError, UnicodeDecodeError, _UnreadableError) as exc:
        return None, f"{path}: cannot be read: {exc}"
    try:
        document = parse_json(text, str(path))
    except UnitError as exc:
        return None, str(exc)
    except RecursionError:
        # The parser gives up on deeply nested arrays and objects this way.
        return None, f"{path}: invalid JSON: nested too deeply"
    try:
        record, note = _record(document)
    except _InvalidError as exc:
        return None, f"{path}: {exc}"
    return record, None if note is None else f"{path}: {note}"


def choose_auto(
    candidates: Sequence[str],
    records: Mapping[str, UsageRecord | None],
    now: datetime,
) -> tuple[str, str]:
    """
    Choose the candidate whose account is the least used, and say why.

    An account scores its five-hour percentage if it was recorded less than five
    hours before ``now``, and 0 otherwise: with no record, with none that has a
    five-hour figure, or with one old enough that its window has rolled over. A
    record dated after ``now`` counts as recent. The lowest score wins, and the
    first listed of equal scores. ``records`` has each
    candidate's record, or ``None``; a candidate it leaves out has no data.

    Returns the account, and the reason as a phrase that follows its name.

    Raises:
        ValueError: ``candidates`` is empty.

    """
    if not candidates:
        raise ValueError("no candidates to choose from")
    # min returns the first of equal scores, which is the tie rule.
    chosen = min(candidates, key=lambda name: _score(records.get(name), now))
    return chosen, _reason(records.get(chosen), now)


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


def _read_text(path: Path) -> str:
    """
    Read the regular file at ``path``, which is at most ``MAX_RECORD_BYTES``.

    The file is opened without following a symbolic link and without waiting
    for a writer, then checked through the descriptor it gave, so that nothing
    can be swapped in between the check and the read. A FIFO opens at once and
    is refused unread, and a link is refused without its target being opened.

    Raises:
        FileNotFoundError: there is no file.
        OSError: the file cannot be opened or read.
        UnicodeDecodeError: the file is not UTF-8.
        _UnreadableError: it is not a regular file, or is too large.

    """
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOCTTY | os.O_CLOEXEC
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        if exc.errno == errno.ELOOP and path.is_symlink():
            raise _UnreadableError("a symbolic link, not a regular file") from exc
        raise
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise _UnreadableError("not a regular file")
        data = _read_up_to(descriptor, MAX_RECORD_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(data) > MAX_RECORD_BYTES:
        raise _UnreadableError(f"larger than {MAX_RECORD_BYTES // 1024} KiB")
    return data.decode("utf-8")


def _read_up_to(descriptor: int, size: int) -> bytes:
    """Read from ``descriptor`` until ``size`` bytes or the end of the file."""
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0 and (chunk := os.read(descriptor, remaining)):
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _recent(record: UsageRecord, now: datetime) -> bool:
    """Say whether ``record`` is from the last five hours."""
    return now - record.recorded_at < FIVE_HOURS


def _score(record: UsageRecord | None, now: datetime) -> float:
    """Score an account for :func:`choose_auto`: how used it is, as far as known."""
    if record is None or record.five_hour_pct is None or not _recent(record, now):
        return 0.0
    return record.five_hour_pct


def _reason(record: UsageRecord | None, now: datetime) -> str:
    """Say what scored an account in :func:`choose_auto`."""
    if record is None:
        return "no data"
    age = format_age(now - record.recorded_at)
    if not _recent(record, now):
        return f"recorded {age}, so its 5-hour window has rolled over"
    if record.five_hour_pct is None:
        return f"no 5-hour figure, recorded {age}"
    return f"5-hour usage {round(record.five_hour_pct)}%, recorded {age}"


def _record(document: JsonValue) -> tuple[UsageRecord, str | None]:
    """
    Check ``document`` and build the record from it.

    Returns the record, and a note, with the pointer it is about, when a value
    had to be adjusted to make it valid.

    Raises:
        _InvalidError: it is not a valid record.

    """
    if not isinstance(document, dict):
        raise _InvalidError("must be a JSON object")
    recorded_at = _recorded_at(document.get("recorded_at"))
    five_hour_pct, note = _five_hour(document.get("rate_limits"))
    return UsageRecord(recorded_at, five_hour_pct), note


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


def _five_hour(limits: JsonValue) -> tuple[float | None, str | None]:
    """Read the five-hour percentage, if ``limits`` has one, and any note on it."""
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


def _percentage(value: JsonValue) -> tuple[float | None, str | None]:
    """
    Read a percentage, which is a number from 0 to 100.

    A finite number outside that range is clamped to 0 or 100, with a note.
    """
    pointer = "/rate_limits/five_hour/used_percentage"
    match value:
        case None:
            return None, None
        case bool() | str() | list() | dict():
            raise _InvalidError(f"{pointer}: must be a number")
        case int() | float():
            if isinstance(value, float) and not math.isfinite(value):
                raise _InvalidError(f"{pointer}: not a finite number")
            # Compared before it becomes a float: a huge integer cannot be one.
            if 0 <= value <= _MAX_PERCENT:
                return float(value), None
            bound = 0 if value < 0 else _MAX_PERCENT
            note = f"{pointer}: outside 0 to {_MAX_PERCENT}, clamped to {bound}"
            return float(bound), note
