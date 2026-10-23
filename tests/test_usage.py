"""The usage records the statusline writes, and ``atc usage``, which shows them."""

import contextlib
import os
import re
import threading
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from atc.cli import main
from atc.exitcodes import CONFIG, OK
from atc.jsonval import JsonObject, JsonValue
from atc.paths import Dirs
from atc.usage import (
    MAX_RECORD_BYTES,
    UsageRecord,
    choose_auto,
    format_age,
    read_usage,
)
from tests.conftest import RegistryWriter
from tests.helpers import RECORDED, ago, five_hour, record_path, write_record

REGISTRY = """\
    version: 1
    default-account: personal
    accounts:
      personal: {agent: claude, dir: ~/.claude}
      work: {agent: claude, dir: ~/.config/work}
      side: {agent: claude, dir: ~/.config/side}
"""
YELLOW = "\x1b[33m"
RED = "\x1b[31m"
RESET = "\x1b[0m"


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
        pytest.param("[" * 60_000, "invalid JSON", id="deeply-nested"),
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
        pytest.param(
            '{"recorded_at": "2026-10-23T09:00:00Z", "rate_limits": '
            + '{"five_hour": {"used_percentage": 1e999}}}',
            "1e999 is too large for a number",
            id="infinite",
        ),
        pytest.param(
            '{"recorded_at": "2026-10-23T09:00:00Z", "rate_limits": '
            + '{"five_hour": {"used_percentage": -1e999}}}',
            "-1e999 is too large for a number",
            id="minus-infinite",
        ),
        pytest.param(
            '{"recorded_at": "2026-10-23T09:00:00Z", "rate_limits": '
            + '{"five_hour": {"used_percentage": NaN}}}',
            "NaN is not valid JSON",
            id="nan-literal",
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


def test_a_parser_that_gives_up_on_nesting_is_no_data_with_a_warning(
    dirs: Dirs, monkeypatch: pytest.MonkeyPatch
) -> None:
    # What nests deeply enough to do that within the size limit depends on the
    # interpreter, so the parser is made to give up.
    def give_up(*_args: object, **_kwargs: object) -> JsonValue:
        raise RecursionError

    monkeypatch.setattr("atc.jsonval.json.loads", give_up)
    path = write_record(dirs, "personal", "[[[")

    assert read_usage(dirs, "personal") == (
        None,
        f"{path}: invalid JSON: nested too deeply",
    )


def test_a_nan_percentage_is_not_valid_even_if_the_parser_lets_it_through(
    dirs: Dirs, monkeypatch: pytest.MonkeyPatch
) -> None:
    # parse_json refuses the NaN literal, so only a parser that did not would
    # hand one over.
    def parse(_text: str, _origin: str) -> JsonValue:
        return five_hour(float("nan"))

    monkeypatch.setattr("atc.usage.parse_json", parse)
    path = write_record(dirs, "personal", "{}")

    assert read_usage(dirs, "personal") == (
        None,
        f"{path}: /rate_limits/five_hour/used_percentage: not a finite number",
    )


def test_a_directory_is_not_read(dirs: Dirs) -> None:
    path = record_path(dirs, "personal")
    path.mkdir(parents=True)

    assert read_usage(dirs, "personal") == (
        None,
        f"{path}: cannot be read: not a regular file",
    )


def open_write_end(path: Path) -> None:
    """Open and close the write end of a FIFO that a reader is waiting on."""
    with contextlib.suppress(OSError):
        os.close(os.open(path, os.O_WRONLY | os.O_NONBLOCK))


def test_a_fifo_is_not_read_and_does_not_block(dirs: Dirs) -> None:
    path = record_path(dirs, "personal")
    path.parent.mkdir(parents=True)
    os.mkfifo(path)
    # A reader that blocked would be let go by a writer, so a regression fails
    # the test and does not hang the run.
    release = threading.Timer(5, open_write_end, args=(path,))
    release.start()

    try:
        result = read_usage(dirs, "personal")
    finally:
        release.cancel()

    assert result == (None, f"{path}: cannot be read: not a regular file")


def test_a_symbolic_link_to_a_device_is_not_read(dirs: Dirs) -> None:
    zero = Path("/dev/zero")
    if not zero.exists():
        pytest.skip("no /dev/zero")
    path = record_path(dirs, "personal")
    path.parent.mkdir(parents=True)
    path.symlink_to(zero)

    assert read_usage(dirs, "personal") == (
        None,
        f"{path}: cannot be read: a symbolic link, not a regular file",
    )


def test_a_symbolic_link_to_a_regular_file_is_not_followed(
    dirs: Dirs, tmp_path: Path
) -> None:
    target = tmp_path / "elsewhere.json"
    _ = target.write_text('{"recorded_at": "2026-10-23T09:00:00Z"}', encoding="utf-8")
    path = record_path(dirs, "personal")
    path.parent.mkdir(parents=True)
    path.symlink_to(target)

    assert read_usage(dirs, "personal") == (
        None,
        f"{path}: cannot be read: a symbolic link, not a regular file",
    )


def test_a_directory_symbolic_link_above_the_file_is_followed(
    dirs: Dirs, tmp_path: Path
) -> None:
    real = tmp_path / "real-usage"
    real.mkdir()
    _ = (real / "personal.json").write_text(
        '{"recorded_at": "2026-10-23T09:00:00Z"}', encoding="utf-8"
    )
    dirs.state_dir.mkdir(parents=True)
    (dirs.state_dir / "usage").symlink_to(real)

    record, warning = read_usage(dirs, "personal")

    assert warning is None
    assert record == UsageRecord(datetime(2026, 10, 23, 9, 0, tzinfo=UTC), None)


def padded(size: int) -> str:
    """Make a valid record, with spaces after it up to ``size`` bytes."""
    text = '{"recorded_at": "2026-10-23T09:00:00Z"}'
    return text + " " * (size - len(text))


def test_a_file_at_the_size_limit_is_read(dirs: Dirs) -> None:
    _ = write_record(dirs, "personal", padded(MAX_RECORD_BYTES))

    record, warning = read_usage(dirs, "personal")

    assert warning is None
    assert record is not None


def test_a_file_over_the_size_limit_is_no_data_with_a_warning(dirs: Dirs) -> None:
    path = write_record(dirs, "personal", padded(MAX_RECORD_BYTES + 1))

    assert read_usage(dirs, "personal") == (
        None,
        f"{path}: cannot be read: larger than {MAX_RECORD_BYTES // 1024} KiB",
    )


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


@pytest.mark.parametrize(
    ("percentage", "kept"),
    [
        pytest.param(100.4, 100.0, id="just-above"),
        pytest.param(250, 100.0, id="far-above"),
        pytest.param(10**400, 100.0, id="huge-int"),
        pytest.param(1e300, 100.0, id="huge-float"),
        pytest.param(-0.5, 0.0, id="just-below"),
        pytest.param(-3, 0.0, id="below-int"),
        pytest.param(-(10**400), 0.0, id="huge-negative-int"),
    ],
)
def test_a_percentage_outside_0_to_100_is_clamped_with_a_warning(
    dirs: Dirs, percentage: float, kept: float
) -> None:
    path = write_record(dirs, "personal", five_hour(percentage))

    record, warning = read_usage(dirs, "personal")

    assert record == UsageRecord(datetime(2026, 10, 23, 9, 0, tzinfo=UTC), kept)
    assert warning == (
        f"{path}: /rate_limits/five_hour/used_percentage: outside 0 to 100, "
        f"clamped to {kept:.0f}"
    )


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


NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


def seen(percentage: float | None, **age: float) -> UsageRecord:
    """Make a record that was recorded ``age`` before ``NOW``."""
    return UsageRecord(NOW - timedelta(**age), percentage)


def test_a_record_dated_in_the_future_counts_as_recent() -> None:
    # read_usage refuses such a record, so this is only a clock that moved back.
    records = {"a": seen(10, hours=1), "b": seen(90, hours=-1)}

    assert choose_auto(["a", "b"], records, NOW) == (
        "a",
        "5-hour usage 10%, recorded 1h 0m ago",
    )
    assert choose_auto(["b"], records, NOW) == (
        "b",
        "5-hour usage 90%, recorded just now",
    )


def test_the_lowest_recent_usage_wins() -> None:
    records = {
        "a": seen(60, hours=1),
        "b": seen(20, hours=2),
        "c": seen(45, minutes=10),
    }

    assert choose_auto(["a", "b", "c"], records, NOW) == (
        "b",
        "5-hour usage 20%, recorded 2h 0m ago",
    )


def test_a_stale_record_scores_zero() -> None:
    records = {"a": seen(10, hours=1), "b": seen(90, hours=6, minutes=3)}

    assert choose_auto(["a", "b"], records, NOW) == (
        "b",
        "recorded 6h 3m ago, so its 5-hour window has rolled over",
    )


def test_a_record_is_stale_from_five_hours_on() -> None:
    fresh = {"a": seen(10, hours=1), "b": seen(90, hours=4, minutes=59)}
    stale = {"a": seen(10, hours=1), "b": seen(90, hours=5)}

    assert choose_auto(["a", "b"], fresh, NOW)[0] == "a"
    assert choose_auto(["a", "b"], stale, NOW)[0] == "b"


def test_a_tie_goes_to_the_first_listed() -> None:
    records = {"a": seen(30, hours=1), "b": seen(30, hours=1)}

    assert choose_auto(["a", "b"], records, NOW)[0] == "a"
    assert choose_auto(["b", "a"], records, NOW)[0] == "b"


def test_scores_of_zero_tie_whatever_made_them_zero() -> None:
    records = {"a": seen(99, hours=9), "b": None, "c": seen(0, hours=1)}

    assert choose_auto(["b", "a", "c"], records, NOW)[0] == "b"
    assert choose_auto(["a", "b", "c"], records, NOW)[0] == "a"
    assert choose_auto(["c", "a", "b"], records, NOW)[0] == "c"


def test_no_data_scores_zero() -> None:
    records = {"a": seen(5, hours=1), "b": None}

    assert choose_auto(["a", "b"], records, NOW) == ("b", "no data")


def test_an_account_missing_from_the_records_is_no_data() -> None:
    assert choose_auto(["a", "b"], {"a": seen(5, hours=1)}, NOW) == ("b", "no data")


def test_a_recent_record_without_a_five_hour_figure_scores_zero() -> None:
    records: Mapping[str, UsageRecord | None] = {
        "a": seen(5, hours=1),
        "b": seen(None, minutes=30),
    }

    assert choose_auto(["a", "b"], records, NOW) == (
        "b",
        "no 5-hour figure, recorded 30m ago",
    )


def test_a_clamped_exhausted_account_is_not_the_least_used(dirs: Dirs) -> None:
    _ = write_record(dirs, "a", five_hour(100.4, ago(minutes=5)))
    _ = write_record(dirs, "b", five_hour(40, ago(minutes=5)))
    records = {name: read_usage(dirs, name)[0] for name in ("a", "b")}

    assert choose_auto(["a", "b"], records, datetime.now(UTC))[0] == "b"


def test_choosing_among_no_candidates_is_an_error() -> None:
    with pytest.raises(ValueError, match="no candidates"):
        _ = choose_auto([], {}, NOW)


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


def test_usage_shows_a_clamped_percentage_with_a_warning(
    dirs: Dirs,
    write_registry: RegistryWriter,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _ = write_registry(REGISTRY)
    path = write_record(dirs, "work", five_hour(100.4, ago(minutes=1)))

    status = main(["usage"])

    captured = capsys.readouterr()
    assert status == OK
    assert captured.err.startswith(f"warning: {path}: ")
    assert "clamped to 100" in captured.err
    rows = [" ".join(line.split()) for line in captured.out.splitlines()]
    assert "work 100% 1m ago" in rows


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
