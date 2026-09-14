"""Tests for time-handling helpers (to_utc_timestamp, build_utc_15min_index)."""

import pandas as pd
import pytest

from energydata.common.errors import InvalidPeriodError
from energydata.common.timeutils import (
    DK_TZ,
    SPOT_TRANSITION_UTC,
    build_utc_15min_index,
    to_utc_timestamp,
)

# --- to_utc_timestamp: normal cases ------------------------------------


def test_naive_string_interpreted_as_dk_local() -> None:
    # 2026-07-10 is summer time (CEST, UTC+2), so 12:00 local == 10:00 UTC.
    result = to_utc_timestamp("2026-07-10T12:00")
    assert result == pd.Timestamp("2026-07-10T10:00", tz="UTC")
    assert result.tzinfo is not None
    assert str(result.tz) == "UTC"


def test_naive_winter_string_uses_utc_plus_one() -> None:
    # January is winter time (CET, UTC+1): 12:00 local == 11:00 UTC.
    result = to_utc_timestamp("2026-01-15T12:00")
    assert result == pd.Timestamp("2026-01-15T11:00", tz="UTC")


def test_aware_utc_input_respected() -> None:
    result = to_utc_timestamp(pd.Timestamp("2026-07-10T12:00", tz="UTC"))
    assert result == pd.Timestamp("2026-07-10T12:00", tz="UTC")


def test_aware_other_timezone_converted_to_utc() -> None:
    # A tz-aware New York timestamp must be respected (not reinterpreted as DK).
    ny = pd.Timestamp("2026-07-10T08:00", tz="America/New_York")
    result = to_utc_timestamp(ny)
    # 2026-07-10 New York is EDT (UTC-4): 08:00 -> 12:00 UTC.
    assert result == pd.Timestamp("2026-07-10T12:00", tz="UTC")


def test_naive_datetime_object_input() -> None:
    # A naive datetime.datetime is treated as DK local wall-clock time.
    naive_dt = pd.Timestamp("2026-07-10T12:00").to_pydatetime()
    assert naive_dt.tzinfo is None
    result = to_utc_timestamp(naive_dt)
    assert result == pd.Timestamp("2026-07-10T10:00", tz="UTC")


def test_aware_datetime_object_input() -> None:
    aware_dt = pd.Timestamp("2026-07-10T12:00", tz="UTC").to_pydatetime()
    result = to_utc_timestamp(aware_dt)
    assert result == pd.Timestamp("2026-07-10T12:00", tz="UTC")


def test_naive_timestamp_input() -> None:
    result = to_utc_timestamp(pd.Timestamp("2026-07-10T12:00"))
    assert result == pd.Timestamp("2026-07-10T10:00", tz="UTC")


def test_date_only_string_is_local_midnight() -> None:
    # A bare date is DK-local midnight; summer -> UTC-2.
    result = to_utc_timestamp("2026-07-10")
    assert result == pd.Timestamp("2026-07-09T22:00", tz="UTC")


# --- to_utc_timestamp: edge / adversarial ------------------------------


def test_garbage_string_raises_invalid_period() -> None:
    with pytest.raises(InvalidPeriodError):
        to_utc_timestamp("not a real timestamp")


def test_none_input_raises_invalid_period() -> None:
    # pd.Timestamp(None) yields NaT rather than raising; must be rejected.
    with pytest.raises(InvalidPeriodError):
        to_utc_timestamp(None)  # type: ignore[arg-type]


def test_ambiguous_fall_back_local_time_raises() -> None:
    # 2025-10-26 02:30 local occurs twice (autumn fall-back) -> ambiguous.
    with pytest.raises(InvalidPeriodError):
        to_utc_timestamp("2025-10-26T02:30")


def test_nonexistent_spring_forward_local_time_raises() -> None:
    # 2026-03-29 02:30 local is skipped (spring-forward) -> nonexistent.
    with pytest.raises(InvalidPeriodError):
        to_utc_timestamp("2026-03-29T02:30")


def test_spot_transition_constant_value() -> None:
    assert pd.Timestamp("2025-09-30 22:00:00", tz="UTC") == SPOT_TRANSITION_UTC


def test_dk_tz_constant() -> None:
    assert DK_TZ == "Europe/Copenhagen"


# --- build_utc_15min_index ---------------------------------------------


def test_index_length_one_hour() -> None:
    start = pd.Timestamp("2026-07-10T00:00", tz="UTC")
    end = pd.Timestamp("2026-07-10T01:00", tz="UTC")
    index = build_utc_15min_index(start, end)
    assert len(index) == 4


def test_index_is_utc_and_15min_spaced() -> None:
    start = pd.Timestamp("2026-07-10T00:00", tz="UTC")
    end = pd.Timestamp("2026-07-10T01:00", tz="UTC")
    index = build_utc_15min_index(start, end)
    assert str(index.tz) == "UTC"
    assert (index[1] - index[0]) == pd.Timedelta(minutes=15)


def test_index_is_half_open_excludes_end() -> None:
    start = pd.Timestamp("2026-07-10T00:00", tz="UTC")
    end = pd.Timestamp("2026-07-10T01:00", tz="UTC")
    index = build_utc_15min_index(start, end)
    # End must be excluded even though it lands exactly on a grid point.
    assert end not in index
    assert index[-1] == pd.Timestamp("2026-07-10T00:45", tz="UTC")


def test_index_floors_start_to_quarter() -> None:
    start = pd.Timestamp("2026-07-10T00:07", tz="UTC")
    end = pd.Timestamp("2026-07-10T01:00", tz="UTC")
    index = build_utc_15min_index(start, end)
    # 00:07 floors down to 00:00.
    assert index[0] == pd.Timestamp("2026-07-10T00:00", tz="UTC")
    assert len(index) == 4


def test_index_normal_day_has_96_slots() -> None:
    start = to_utc_timestamp("2026-07-10")
    end = to_utc_timestamp("2026-07-11")
    index = build_utc_15min_index(start, end)
    assert len(index) == 96


def test_index_start_equal_end_raises() -> None:
    ts = pd.Timestamp("2026-07-10T00:00", tz="UTC")
    with pytest.raises(InvalidPeriodError):
        build_utc_15min_index(ts, ts)


def test_index_start_after_end_raises() -> None:
    start = pd.Timestamp("2026-07-10T01:00", tz="UTC")
    end = pd.Timestamp("2026-07-10T00:00", tz="UTC")
    with pytest.raises(InvalidPeriodError):
        build_utc_15min_index(start, end)


def test_index_fall_back_day_has_100_slots() -> None:
    # 2025-10-26 is a 25-hour local day (autumn fall-back).
    start = to_utc_timestamp("2025-10-26")
    end = to_utc_timestamp("2025-10-27")
    index = build_utc_15min_index(start, end)
    assert len(index) == 100


def test_index_spring_forward_day_has_92_slots() -> None:
    # 2026-03-29 is a 23-hour local day (spring-forward).
    start = to_utc_timestamp("2026-03-29")
    end = to_utc_timestamp("2026-03-30")
    index = build_utc_15min_index(start, end)
    assert len(index) == 92
