from datetime import UTC, date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from energydata.utils import DANISH_TZ, resolve_period

Q = timedelta(minutes=15)
H = timedelta(hours=1)
CPH = "Europe/Copenhagen"


def _naive(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _slots(period: tuple[pd.Timestamp, pd.Timestamp], size: timedelta = Q) -> float:
    return (period[1] - period[0]) / pd.Timedelta(size)


def test_danish_tz_is_europe_copenhagen() -> None:
    assert DANISH_TZ == CPH


@pytest.mark.parametrize(
    ("day", "slots"),
    [
        (date(2025, 1, 15), 96),
        ("2025-01-15", 96),
        (date(2026, 3, 29), 92),  # spring forward: 23 hours
        ("2025-03-30", 92),
        (date(2025, 10, 26), 100),  # fall back: 25 hours
        ("2025-10-26", 100),
        ("20251001", 96),  # basic ISO date
    ],
)
def test_resolve_period_lone_date_spans_the_whole_local_day(
    day: date | str, slots: int
) -> None:
    first, last = resolve_period(day)

    assert _slots((first, last)) == slots
    assert first.hour == 0
    assert first.minute == 0
    assert last.hour == 0
    assert str(first.tz) == CPH
    assert str(last.tz) == CPH
    assert first.unit == "ns"


@pytest.mark.parametrize(
    "stamp",
    [
        "2025-01-15T12:30",
        _naive("2025-01-15T12:30"),
        _naive("2025-01-15T00:00"),  # a midnight datetime is a timestamp, not a day
        pd.Timestamp("2025-01-15"),
        "2025-01-15T00:00",
        "2025-01-15 12:30",
    ],
)
def test_resolve_period_lone_timestamp_is_one_slot(stamp: datetime | str) -> None:
    first, last = resolve_period(stamp)

    assert last - first == pd.Timedelta(Q)


def test_resolve_period_date_string_is_a_day_but_timestamp_string_is_a_slot() -> None:
    assert _slots(resolve_period("2025-10-01")) == 96
    assert _slots(resolve_period("2025-10-01T00:00")) == 1


def test_resolve_period_naive_input_is_read_as_danish_time() -> None:
    first, last = resolve_period("2025-01-15T00:00", "2025-01-15T01:00")

    assert first == pd.Timestamp("2025-01-14T23:00", tz="UTC")
    assert last == pd.Timestamp("2025-01-15T00:00", tz="UTC")
    assert first.utcoffset() == timedelta(hours=1)


def test_resolve_period_naive_summer_input_uses_the_summer_offset() -> None:
    first, _ = resolve_period("2025-07-01T12:00")

    assert first.utcoffset() == timedelta(hours=2)


def test_resolve_period_end_is_exclusive() -> None:
    first, last = resolve_period(date(2025, 1, 1), date(2025, 1, 2))

    assert _slots((first, last)) == 96
    assert last == pd.Timestamp("2025-01-02", tz=CPH)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (datetime(2025, 1, 15, 11, tzinfo=UTC), "2025-01-15T13:00+01:00"),
        ("2025-01-15T11:00Z", "2025-01-15T12:00Z"),
        (datetime(2025, 1, 15, 6, tzinfo=timezone(timedelta(hours=-5))), None),
    ],
)
def test_resolve_period_aware_input_is_converted_not_relabelled(
    start: datetime | str, end: datetime | str | None
) -> None:
    first, last = resolve_period(start, end)

    assert first == pd.Timestamp("2025-01-15T11:00", tz="UTC")
    assert str(first.tz) == CPH
    assert first.hour == 12
    assert str(last.tz) == CPH


def test_resolve_period_aware_time_in_the_repeated_hour_is_accepted() -> None:
    first, last = resolve_period("2025-10-26T02:30+01:00")

    assert first == pd.Timestamp("2025-10-26T01:30", tz="UTC")
    assert last - first == pd.Timedelta(Q)


def test_resolve_period_aware_first_pass_of_repeated_hour_is_distinct() -> None:
    first, _ = resolve_period("2025-10-26T02:45+02:00")

    assert first == pd.Timestamp("2025-10-26T00:45", tz="UTC")


def test_resolve_period_mixed_date_start_and_timestamp_end() -> None:
    first, last = resolve_period(date(2025, 1, 1), "2025-01-01T06:00")

    assert _slots((first, last)) == 24


def test_resolve_period_timestamp_start_and_date_end_skips_alignment_of_the_date() -> (
    None
):
    first, last = resolve_period("2025-01-15T12:00", date(2025, 1, 16))

    assert last == pd.Timestamp("2025-01-16", tz=CPH)
    assert first < last


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2025-01-15", "2025-01-15"),
        ("2025-01-15T01:00", "2025-01-15T00:45"),
        ("2025-01-16", date(2025, 1, 15)),
        (date(2025, 1, 2), date(2025, 1, 1)),
        ("2025-01-01T01:00", "2025-01-01T01:00"),
    ],
)
def test_resolve_period_rejects_an_end_not_after_start(
    start: date | str, end: date | str
) -> None:
    with pytest.raises(ValueError, match="end must be after start") as caught:
        resolve_period(start, end)

    assert "start=" in str(caught.value)
    assert "end=" in str(caught.value)


@pytest.mark.parametrize(
    ("start", "end", "named"),
    [
        ("2025-01-15T12:07", None, "12:07"),
        ("2025-01-15T12:07", "2025-01-15T13:00", "12:07"),
        ("2025-01-15T12:00", "2025-01-15T13:01", "13:01"),
        ("2025-01-15T12:15:00.000001", None, "12:15:00.000001"),
        ("2025-01-15T00:00:30", None, "00:00:30"),
        (_naive("2025-01-01T00:00:00.000001"), None, "00:00:00.000001"),
    ],
)
def test_resolve_period_rejects_a_misaligned_bound_naming_it(
    start: date | str, end: date | str | None, named: str
) -> None:
    with pytest.raises(ValueError, match="boundary") as caught:
        resolve_period(start, end)

    assert named in str(caught.value)


def test_resolve_period_misaligned_aware_input_is_reported_in_local_time() -> None:
    with pytest.raises(ValueError, match="boundary") as caught:
        resolve_period("2025-01-15T11:07Z")

    assert "2025-01-15T12:07:00+01:00" in str(caught.value)


@pytest.mark.parametrize("stamp", ["2025-03-30T02:30", _naive("2025-03-30T02:30")])
def test_resolve_period_rejects_a_nonexistent_naive_time_naming_it(
    stamp: datetime | str,
) -> None:
    with pytest.raises(ValueError, match="does not exist or is ambiguous") as caught:
        resolve_period(stamp)

    assert "2025-03-30T02:30" in str(caught.value)


@pytest.mark.parametrize(
    "stamp",
    [
        "2025-10-26T02:30",
        _naive("2025-10-26T02:30"),
        _naive("2025-10-26T02:30").replace(fold=1),
    ],
)
def test_resolve_period_rejects_an_ambiguous_naive_time_naming_it(
    stamp: datetime | str,
) -> None:
    with pytest.raises(ValueError, match="does not exist or is ambiguous") as caught:
        resolve_period(stamp)

    assert "2025-10-26T02:30" in str(caught.value)


@pytest.mark.parametrize(
    "text",
    [
        "yesterday",
        "2025-13-01",
        "not a date",
        " 2025-10-01",
        "2025-10-01 ",
        "1 Oct 2025",
        "now",
        "today",
        "2025-10",
        "2025",
    ],
)
def test_resolve_period_rejects_a_non_iso_string_naming_it(text: str) -> None:
    with pytest.raises(ValueError) as caught:
        resolve_period(text)

    assert repr(text) in str(caught.value)


def test_resolve_period_rejects_an_empty_string_naming_it() -> None:
    with pytest.raises(ValueError) as caught:
        resolve_period("")

    assert "''" in str(caught.value)


def test_resolve_period_names_which_bound_is_bad() -> None:
    with pytest.raises(ValueError, match="end is not an ISO"):
        resolve_period("2025-01-01", "garbage")


@pytest.mark.parametrize(
    "bad", [None, 0, 1.5, pd.NaT, np.datetime64("2025-01-01"), b"2025-01-01"]
)
def test_resolve_period_rejects_a_value_that_is_not_a_time(bad: object) -> None:
    with pytest.raises(ValueError):
        resolve_period(bad)  # type: ignore[arg-type]  # malformed on purpose


@pytest.mark.parametrize(
    "resolution",
    [timedelta(0), -Q, timedelta(minutes=7), timedelta(hours=2), timedelta(days=1)],
)
def test_resolve_period_rejects_a_resolution_that_does_not_divide_an_hour(
    resolution: timedelta,
) -> None:
    with pytest.raises(ValueError, match="divisor of one hour") as caught:
        resolve_period("2025-01-01", resolution=resolution)

    assert repr(resolution) in str(caught.value)


@pytest.mark.parametrize(
    "resolution", [H, timedelta(minutes=30), timedelta(minutes=5), timedelta(minutes=1)]
)
def test_resolve_period_accepts_a_whole_divisor_of_an_hour(
    resolution: timedelta,
) -> None:
    first, last = resolve_period("2025-01-01T01:00", resolution=resolution)

    assert last - first == pd.Timedelta(resolution)


def test_resolve_period_honours_a_non_default_resolution_for_alignment() -> None:
    first, last = resolve_period("2025-01-01T00:05", resolution=timedelta(minutes=5))

    assert last - first == pd.Timedelta(minutes=5)
    with pytest.raises(ValueError, match="boundary"):
        resolve_period("2025-01-01T00:05", resolution=Q)


def test_resolve_period_hourly_resolution_rejects_a_quarter_hour_timestamp() -> None:
    with pytest.raises(ValueError, match="boundary"):
        resolve_period("2025-01-01T00:15", resolution=H)


def test_resolve_period_reads_naive_input_in_another_zone() -> None:
    first, _ = resolve_period("2025-01-01T00:00", tz="UTC")

    assert str(first.tz) == "UTC"
    assert first == pd.Timestamp("2025-01-01", tz="UTC")


@pytest.mark.parametrize("stamp", ["2025-01-01T00:00", "2025-01-01T00:00Z"])
def test_resolve_period_rejects_an_unknown_zone_for_naive_and_aware_input(
    stamp: str,
) -> None:
    with pytest.raises(ValueError, match="Mars/Base"):
        resolve_period(stamp, tz="Mars/Base")


def test_resolve_period_returns_nanosecond_timestamps() -> None:
    first, last = resolve_period("2025-01-01T00:00", "2025-01-01T01:00")

    assert first.unit == "ns"
    assert last.unit == "ns"


def test_resolve_period_is_idempotent() -> None:
    assert resolve_period("2025-01-15") == resolve_period("2025-01-15")


def test_resolve_period_accepts_its_own_output() -> None:
    first, last = resolve_period("2025-01-15")

    assert resolve_period(first, last) == (first, last)
