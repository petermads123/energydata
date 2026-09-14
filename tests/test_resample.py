"""Tests for upsample_hourly_to_15min (hourly -> 15-min forward fill)."""

import math

import pandas as pd

from energydata.common.resample import upsample_hourly_to_15min


def _grid(start: str, end: str) -> pd.DatetimeIndex:
    return pd.date_range(start, end, freq="15min", tz="UTC", inclusive="left")


def test_each_hour_fills_its_three_trailing_quarters() -> None:
    hourly = pd.Series(
        [10.0, 20.0],
        index=pd.DatetimeIndex(["2026-07-10T00:00", "2026-07-10T01:00"], tz="UTC"),
    )
    index = _grid("2026-07-10T00:00", "2026-07-10T02:00")
    out = upsample_hourly_to_15min(hourly, index)
    assert list(out.to_numpy()) == [10.0, 10.0, 10.0, 10.0, 20.0, 20.0, 20.0, 20.0]


def test_genuine_gap_left_as_nan_after_three_slots() -> None:
    # A single hour at 00:00 with a missing 01:00: ffill(limit=3) covers
    # 00:00..00:45, then everything from 01:00 onward stays NaN.
    hourly = pd.Series(
        [5.0], index=pd.DatetimeIndex(["2026-07-10T00:00"], tz="UTC")
    )
    index = _grid("2026-07-10T00:00", "2026-07-10T02:00")
    out = upsample_hourly_to_15min(hourly, index)
    values = out.to_numpy()
    assert list(values[:4]) == [5.0, 5.0, 5.0, 5.0]
    assert all(math.isnan(v) for v in values[4:])


def test_defensive_sort_of_unsorted_source() -> None:
    # Source deliberately out of order; result must still be chronological.
    hourly = pd.Series(
        [20.0, 10.0],
        index=pd.DatetimeIndex(["2026-07-10T01:00", "2026-07-10T00:00"], tz="UTC"),
    )
    index = _grid("2026-07-10T00:00", "2026-07-10T02:00")
    out = upsample_hourly_to_15min(hourly, index)
    assert out.iloc[0] == 10.0
    assert out.iloc[4] == 20.0


def test_slot_before_first_hour_is_nan() -> None:
    # Grid starts a quarter before the first source hour -> nothing to fill.
    hourly = pd.Series(
        [7.0], index=pd.DatetimeIndex(["2026-07-10T00:00"], tz="UTC")
    )
    index = _grid("2026-07-09T23:45", "2026-07-10T00:30")
    out = upsample_hourly_to_15min(hourly, index)
    assert math.isnan(out.iloc[0])
    assert out.iloc[1] == 7.0


def test_result_indexed_on_target_grid() -> None:
    hourly = pd.Series(
        [1.0], index=pd.DatetimeIndex(["2026-07-10T00:00"], tz="UTC")
    )
    index = _grid("2026-07-10T00:00", "2026-07-10T01:00")
    out = upsample_hourly_to_15min(hourly, index)
    assert out.index.equals(index)
