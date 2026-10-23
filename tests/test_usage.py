"""The usage records the statusline writes, and ``atc usage``, which shows them."""

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from atc.cli import main
from atc.exitcodes import CONFIG, OK
from atc.jsonval import JsonObject, JsonValue
from atc.paths import Dirs
from atc.usage import UsageRecord, format_age, read_usage
from tests.conftest import RegistryWriter

REGISTRY = """\
    version: 1
    default-account: personal
    accounts:
      personal: {agent: claude, dir: ~/.claude}
      work: {agent: claude, dir: ~/.config/work}
      side: {agent: claude, dir: ~/.config/side}
"""
RECORDED = "2026-10-23T09:00:00Z"
YELLOW = "\x1b[33m"
RED = "\x1b[31m"
RESET = "\x1b[0m"


def record_path(dirs: Dirs, account: str) -> Path:
    return dirs.state_dir / "usage" / f"{account}.json"


def write_record(dirs: Dirs, account: str, document: JsonValue | str) -> Path:
    """Store ``document``, as JSON unless it is text, as the record of ``account``."""
    path = record_path(dirs, account)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = document if isinstance(document, str) else json.dumps(document)
    _ = path.write_text(text, encoding="utf-8")
    return path


def five_hour(percentage: JsonValue, recorded_at: str = RECORDED) -> JsonValue:
    """Build a record as the statusline writes it, with other limits beside."""
    return {
        "recorded_at": recorded_at,
        "rate_limits": {
            "five_hour": {"used_percentage": percentage, "resets_at": 1792000000},
            "seven_day": {"used_percentage": 3, "resets_at": 1792500000},
        },
    }


def ago(**delta: float) -> str:
    """Write the time ``delta`` before now, as the statusline does."""
    return (datetime.now(UTC) - timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_a_valid_record_is_read(dirs: Dirs) -> None:
    _ = write_record(dirs, "personal", five_hour(42.5))

    assert read_usage(dirs, "personal") == (
        UsageRecord(datetime(2026, 10, 23, 9, 0, tzinfo=UTC), 42.5),
        None,
    )


def test_a_missing_file_is_no_data(dirs: Dirs) -> None:
    assert read_usage(dirs, "personal") == (None, None)


@pytest.mark.parametrize(
    ("document", "problem"),
    [
        pytest.param("{not json", "invalid JSON", id="not-json"),
        pytest.param("", "invalid JSON", id="empty"),
        pytest.param("[" * 200_000, "nested too deeply", id="deeply-nested"),
        pytest.param("[1, 2]", "must be a JSON object", id="array"),
        pytest.param("null", "must be a JSON object", id="null"),
        pytest.param({"rate_limits": {}}, "/recorded_at: missing", id="no-time"),
        pytest.param(
            {"recorded_at": None, "rate_limits": {}}, "/recorded_at", id="null-time"
        ),
        pytest.param(
            {"recorded_at": 1792000000, "rate_limits": {}}, "/recorded_at", id="number"
        ),
        pytest.param(five_hour(1, "yesterday"), "/recorded_at", id="bad-time"),
        pytest.param(five_hour(1, "2026-10-23"), "/recorded_at", id="date-only"),
        pytest.param(
            five_hour(1, "2026-10-23T09:00:00"), "/recorded_at", id="no-offset"
        ),
        pytest.param(five_hour(1, "2999-01-01T00:00:00Z"), "future", id="future"),
        pytest.param(
            {"recorded_at": RECORDED, "rate_limits": "five"},
            "/rate_limits",
            id="limits-not-object",
        ),
        pytest.param(
            {"recorded_at": RECORDED, "rate_limits": {"five_hour": 12}},
            "/rate_limits/five_hour",
            id="five-hour-not-object",
        ),
        pytest.param(
            five_hour("12"), "/rate_limits/five_hour/used_percentage", id="str"
        ),
        pytest.param(
            five_hour(True), "/rate_limits/five_hour/used_percentage", id="bool"
        ),
        pytest.param(
            five_hour([12]), "/rate_limits/five_hour/used_percentage", id="list"
        ),
        pytest.param(five_hour(-0.5), "outside 0 to 100", id="below"),
        pytest.param(five_hour(100.5), "outside 0 to 100", id="above"),
        pytest.param(five_hour(10**400), "outside 0 to 100", id="huge-int"),
        pytest.param(
            '{"recorded_at": "2026-10-23T09:00:00Z", "rate_limits": '
            + '{"five_hour": {"used_percentage": 1e999}}}',
            "outside 0 to 100",
            id="infinite",
        ),
    ],
)
def test_a_malformed_file_is_no_data_with_a_warning(
    dirs: Dirs, document: JsonValue | str, problem: str
) -> None:
    path = write_record(dirs, "personal", document)

    record, warning = read_usage(dirs, "personal")

    assert record is None
    assert warning is not None
    assert warning.startswith(f"{path}: ")
    assert problem in warning


def test_a_file_that_cannot_be_read_is_no_data_with_a_warning(dirs: Dirs) -> None:
    path = record_path(dirs, "personal")
    path.mkdir(parents=True)

    record, warning = read_usage(dirs, "personal")

    assert record is None
    assert warning is not None
    assert warning.startswith(f"{path}: cannot be read")


def test_a_file_that_is_not_text_is_no_data_with_a_warning(dirs: Dirs) -> None:
    path = record_path(dirs, "personal")
    path.parent.mkdir(parents=True)
    _ = path.write_bytes(b'{"recorded_at": "\xff"}')

    record, warning = read_usage(dirs, "personal")

    assert record is None
    assert warning is not None
    assert warning.startswith(f"{path}: cannot be read")


@pytest.mark.parametrize(
    "limits",
    [
        pytest.param({}, id="no-limits"),
        pytest.param({"rate_limits": None}, id="null-limits"),
        pytest.param({"rate_limits": {}}, id="empty-limits"),
        pytest.param(
            {"rate_limits": {"seven_day": {"used_percentage": 3}}}, id="other-window"
        ),
        pytest.param({"rate_limits": {"five_hour": {}}}, id="no-percentage"),
        pytest.param(
            {"rate_limits": {"five_hour": {"used_percentage": None}}},
            id="null-percentage",
        ),
        pytest.param({"rate_limits": {"five_hour": None}}, id="null-window"),
    ],
)
def test_a_record_without_five_hour_data_has_no_percentage(
    dirs: Dirs, limits: JsonObject
) -> None:
    _ = write_record(dirs, "personal", {"recorded_at": RECORDED, **limits})

    assert read_usage(dirs, "personal") == (
        UsageRecord(datetime(2026, 10, 23, 9, 0, tzinfo=UTC), None),
        None,
    )


@pytest.mark.parametrize("percentage", [0, 0.0, 100, 100.0, 7, 69.99])
def test_a_percentage_in_range_is_kept_as_a_float(
    dirs: Dirs, percentage: float
) -> None:
    _ = write_record(dirs, "personal", five_hour(percentage))

    record, warning = read_usage(dirs, "personal")

    assert warning is None
    assert record is not None
    assert record.five_hour_pct == percentage
    assert isinstance(record.five_hour_pct, float)


def test_a_time_with_an_offset_is_the_same_instant(dirs: Dirs) -> None:
    _ = write_record(dirs, "personal", five_hour(5, "2026-10-23T11:00:00+02:00"))

    record, _ = read_usage(dirs, "personal")

    assert record is not None
    assert record.recorded_at == datetime(2026, 10, 23, 9, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("age", "text"),
    [
        (timedelta(seconds=0), "just now"),
        (timedelta(seconds=59), "just now"),
        (timedelta(seconds=-5), "just now"),
        (timedelta(minutes=1), "1m ago"),
        (timedelta(minutes=59, seconds=59), "59m ago"),
        (timedelta(hours=1), "1h 0m ago"),
        (timedelta(hours=3, minutes=20), "3h 20m ago"),
        (timedelta(hours=23, minutes=59), "23h 59m ago"),
        (timedelta(days=1), "1d 0h ago"),
        (timedelta(days=2, hours=4, minutes=30), "2d 4h ago"),
    ],
)
def test_an_age_is_written_in_its_two_largest_units(age: timedelta, text: str) -> None:
    assert format_age(age) == text


def test_usage_lists_every_account(
    dirs: Dirs,
    write_registry: RegistryWriter,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _ = write_registry(REGISTRY)
    _ = write_record(dirs, "personal", five_hour(42.4, ago(hours=2, minutes=5)))
    _ = write_record(dirs, "side", {"recorded_at": ago(minutes=3), "rate_limits": {}})

    status = main(["usage"])

    captured = capsys.readouterr()
    assert status == OK
    assert captured.err == ""
    lines = [
        " ".join(line.split()) for line in captured.out.splitlines() if line.strip()
    ]
    assert lines[0] == "Account 5-hour Recorded"
    assert lines[2:] == [
        "personal 42% 2h 5m ago",
        "work no data -",
        "side - 3m ago",
    ]


def test_usage_warns_about_a_malformed_record_and_shows_no_data(
    dirs: Dirs,
    write_registry: RegistryWriter,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _ = write_registry(REGISTRY)
    path = write_record(dirs, "work", "{not json")

    status = main(["usage"])

    captured = capsys.readouterr()
    assert status == OK
    assert captured.err.startswith(f"warning: {path}: invalid JSON")
    rows = [" ".join(line.split()) for line in captured.out.splitlines()]
    assert "work no data -" in rows


@pytest.mark.parametrize(
    ("percentage", "colour", "shown"),
    [
        (0, YELLOW, "0%"),
        (42.4, YELLOW, "42%"),
        (69.4, YELLOW, "69%"),
        (69.6, RED, "70%"),
        (70, RED, "70%"),
        (100, RED, "100%"),
    ],
)
def test_usage_is_yellow_below_70_and_red_from_70_on_a_terminal(
    dirs: Dirs,
    write_registry: RegistryWriter,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    percentage: float,
    colour: str,
    shown: str,
) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("FORCE_COLOR", "1")
    _ = write_registry(REGISTRY)
    _ = write_record(dirs, "personal", five_hour(percentage, ago(minutes=1)))

    _ = main(["usage"])

    # The cell is padded to its column inside the style.
    assert re.search(
        rf"{re.escape(colour)}{shown} *{re.escape(RESET)}", capsys.readouterr().out
    )


def test_usage_has_no_colour_when_it_is_not_a_terminal(
    dirs: Dirs,
    write_registry: RegistryWriter,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    _ = write_registry(REGISTRY)
    _ = write_record(dirs, "personal", five_hour(90, ago(minutes=1)))

    _ = main(["usage"])

    assert "\x1b" not in capsys.readouterr().out


def test_usage_without_a_registry_is_a_config_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["usage"]) == CONFIG
    assert "machine.yaml: not found" in capsys.readouterr().err
