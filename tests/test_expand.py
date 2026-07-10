"""Tests for validity-window selection and 15-min pricelist expansion."""

import math
from typing import Any

import pandas as pd
import pytest

from energydata.common.timeutils import (
    DK_TZ,
    build_utc_15min_index,
    to_utc_timestamp,
)
from energydata.tariffs.expand import (
    expand_pricelist_to_15min,
    select_active_record,
)


def _index(start: str, end: str) -> pd.DatetimeIndex:
    return build_utc_15min_index(to_utc_timestamp(start), to_utc_timestamp(end))


def _hourly_record(
    valid_from: str,
    valid_to: str | None,
    price_by_hour: dict[int, float] | float,
) -> dict[str, Any]:
    """Build a PT1H record; price_by_hour maps 1..24 or is a constant."""
    if isinstance(price_by_hour, dict):
        prices = {f"Price{h}": price_by_hour.get(h, float(h)) for h in range(1, 25)}
    else:
        prices = {f"Price{h}": price_by_hour for h in range(1, 25)}
    return {
        "ValidFrom": valid_from,
        "ValidTo": valid_to,
        "ResolutionDuration": "PT1H",
        **prices,
    }


def _flat_record(
    valid_from: str, valid_to: str | None, price: float, resolution: str = "P1D"
) -> dict[str, Any]:
    return {
        "ValidFrom": valid_from,
        "ValidTo": valid_to,
        "ResolutionDuration": resolution,
        "Price1": price,
    }


# --- select_active_record ----------------------------------------------


def test_select_active_within_window() -> None:
    records = [_flat_record("2026-01-01T00:00:00", "2026-02-01T00:00:00", 1.0)]
    at = pd.Timestamp("2026-01-15T12:00")
    assert select_active_record(records, at) is records[0]


def test_select_active_valid_from_inclusive() -> None:
    records = [_flat_record("2026-01-01T00:00:00", None, 1.0)]
    at = pd.Timestamp("2026-01-01T00:00:00")
    assert select_active_record(records, at) is records[0]


def test_select_active_valid_to_exclusive() -> None:
    records = [_flat_record("2026-01-01T00:00:00", "2026-02-01T00:00:00", 1.0)]
    at = pd.Timestamp("2026-02-01T00:00:00")
    # ValidTo is exclusive, so the boundary instant is not covered.
    assert select_active_record(records, at) is None


def test_select_active_open_ended_valid_to() -> None:
    records = [_flat_record("2026-01-01T00:00:00", None, 1.0)]
    at = pd.Timestamp("2099-01-01T00:00:00")
    assert select_active_record(records, at) is records[0]


def test_select_active_before_window_is_none() -> None:
    records = [_flat_record("2026-01-01T00:00:00", None, 1.0)]
    at = pd.Timestamp("2025-12-31T23:59:00")
    assert select_active_record(records, at) is None


def test_select_active_transition_mid_period() -> None:
    records = [
        _flat_record("2026-01-01T00:00:00", "2026-01-10T00:00:00", 1.0),
        _flat_record("2026-01-10T00:00:00", None, 2.0),
    ]
    assert select_active_record(records, pd.Timestamp("2026-01-05T00:00")) is records[0]
    assert select_active_record(records, pd.Timestamp("2026-01-15T00:00")) is records[1]


def test_select_active_overlap_latest_valid_from_wins() -> None:
    older = _flat_record("2026-01-01T00:00:00", None, 1.0)
    newer = _flat_record("2026-01-05T00:00:00", None, 2.0)
    # Both windows cover 2026-01-10; the correction (later ValidFrom) wins.
    at = pd.Timestamp("2026-01-10T00:00")
    assert select_active_record([older, newer], at) is newer
    # Order in the list must not matter.
    assert select_active_record([newer, older], at) is newer


def test_select_active_tz_aware_query_stripped() -> None:
    records = [_flat_record("2026-01-01T00:00:00", None, 1.0)]
    at = pd.Timestamp("2026-01-15T12:00", tz=DK_TZ)
    assert select_active_record(records, at) is records[0]


# --- expand_pricelist_to_15min: basics ---------------------------------


def test_expand_empty_index_returns_empty() -> None:
    records = [_flat_record("2020-01-01T00:00:00", None, 1.0)]
    empty = pd.DatetimeIndex([], tz="UTC")
    out = expand_pricelist_to_15min(records, empty)
    assert len(out) == 0
    assert out.dtype == "float64"


def test_expand_flat_daily_constant() -> None:
    records = [_flat_record("2026-01-01T00:00:00", None, 0.5)]
    index = _index("2026-01-15", "2026-01-16")
    out = expand_pricelist_to_15min(records, index)
    assert len(out) == 96
    assert (out == 0.5).all()


def test_expand_no_record_gives_nan() -> None:
    records = [_flat_record("2020-01-01T00:00:00", "2020-02-01T00:00:00", 0.5)]
    index = _index("2026-01-15", "2026-01-16")
    out = expand_pricelist_to_15min(records, index)
    assert out.isna().all()


def test_expand_missing_price_field_is_nan() -> None:
    # A PT1H record with a null Price yields NaN for that local hour.
    record = _hourly_record("2026-01-01T00:00:00", None, {h: float(h) for h in range(1, 25)})
    record["Price5"] = None
    index = _index("2026-01-15", "2026-01-16")
    out = expand_pricelist_to_15min([record], index)
    local_hour = index.tz_convert(DK_TZ).hour
    # Local hour 4 -> Price5 -> null -> NaN.
    hour4_values = out.to_numpy()[local_hour == 4]
    assert all(math.isnan(v) for v in hour4_values)


# --- expand_pricelist_to_15min: PT1H hour mapping ----------------------


def test_expand_hourly_maps_local_hour_to_price() -> None:
    # Price{h} == h, so local hour k should read Price{k+1} == k+1.
    record = _hourly_record("2026-01-01T00:00:00", None, {h: float(h) for h in range(1, 25)})
    index = _index("2026-01-15", "2026-01-16")  # winter, UTC+1
    out = expand_pricelist_to_15min([record], index)
    local_hour = index.tz_convert(DK_TZ).hour
    for slot_value, hour in zip(out.to_numpy(), local_hour, strict=True):
        assert slot_value == hour + 1


def test_expand_fall_back_day_100_rows_doubled_hour_same_price() -> None:
    # 2025-10-26: 25-hour local day. Both 02:xx occurrences map to Price3.
    record = _hourly_record("2025-01-01T00:00:00", None, {h: float(h) for h in range(1, 25)})
    index = _index("2025-10-26", "2025-10-27")
    out = expand_pricelist_to_15min([record], index)
    assert len(out) == 100
    local_hour = index.tz_convert(DK_TZ).hour
    hour2_values = out.to_numpy()[local_hour == 2]
    # The fall-back doubles local hour 2 (8 quarter-slots), all Price3 == 3.0.
    assert len(hour2_values) == 8
    assert all(v == 3.0 for v in hour2_values)


def test_expand_spring_forward_day_92_rows_no_hour_two() -> None:
    # 2026-03-29: 23-hour local day; local hour 2 is skipped entirely.
    record = _hourly_record("2026-01-01T00:00:00", None, {h: float(h) for h in range(1, 25)})
    index = _index("2026-03-29", "2026-03-30")
    out = expand_pricelist_to_15min([record], index)
    assert len(out) == 92
    local_hour = index.tz_convert(DK_TZ).hour
    assert 2 not in set(local_hour)


def test_expand_overlap_latest_valid_from_wins() -> None:
    older = _flat_record("2026-01-01T00:00:00", None, 1.0)
    newer = _flat_record("2026-01-10T00:00:00", None, 2.0)
    index = _index("2026-01-15", "2026-01-16")
    out = expand_pricelist_to_15min([older, newer], index)
    # 2026-01-15 is covered by both; the later ValidFrom (2.0) wins.
    assert (out == 2.0).all()


def test_expand_window_transition_within_grid() -> None:
    # Two flat windows meeting at DK-local midnight 2026-01-16.
    records = [
        _flat_record("2026-01-01T00:00:00", "2026-01-16T00:00:00", 1.0),
        _flat_record("2026-01-16T00:00:00", None, 2.0),
    ]
    index = _index("2026-01-15", "2026-01-17")
    out = expand_pricelist_to_15min(records, index)
    # DK-local midnight 2026-01-16 == 2026-01-15T23:00 UTC (winter).
    boundary = pd.Timestamp("2026-01-15T23:00", tz="UTC")
    before = out[out.index < boundary]
    after = out[out.index >= boundary]
    assert (before == 1.0).all()
    assert (after == 2.0).all()


def test_expand_monthly_resolution_is_flat() -> None:
    records = [_flat_record("2026-01-01T00:00:00", None, 3.0, resolution="P1M")]
    index = _index("2026-01-15", "2026-01-16")
    out = expand_pricelist_to_15min(records, index)
    assert (out == 3.0).all()
