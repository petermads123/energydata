"""Tests for get_dayahead_prices (two-era stitching, DKK/kWh, multi-zone)."""

from typing import Any

import pandas as pd
import pytest

from energydata.common.errors import NoDataError, UnknownAreaError
from energydata.dayahead import dayahead as dayahead_mod
from energydata.dayahead.dayahead import get_dayahead_prices


def _hourly(time_utc: str, zone: str, dkk_mwh: float) -> dict[str, Any]:
    return {"HourUTC": time_utc, "PriceArea": zone, "SpotPriceDKK": dkk_mwh}


def _quarter(time_utc: str, zone: str, dkk_mwh: float) -> dict[str, Any]:
    return {"TimeUTC": time_utc, "PriceArea": zone, "DayAheadPriceDKK": dkk_mwh}


def _install_fetch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    hourly: list[dict[str, Any]] | None = None,
    quarter: list[dict[str, Any]] | None = None,
) -> None:
    def _fetch(dataset: str, **_kwargs: Any) -> list[dict[str, Any]]:
        if dataset == "Elspotprices":
            return hourly or []
        if dataset == "DayAheadPrices":
            return quarter or []
        return []

    monkeypatch.setattr(dayahead_mod, "fetch_dataset", _fetch)


# --- fully hourly era ---------------------------------------------------


def test_fully_hourly_era_upsampled_and_scaled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 2025-09-01 00:00..02:00 DK-local (CEST) is entirely pre-transition.
    hourly = [
        _hourly("2025-08-31T22:00:00", "DK2", 100.0),
        _hourly("2025-08-31T23:00:00", "DK2", 200.0),
    ]
    _install_fetch(monkeypatch, hourly=hourly)
    df = get_dayahead_prices("2025-09-01T00:00", "2025-09-01T02:00", "DK2")

    assert list(df.columns) == ["UTC", "DK2"]
    assert len(df) == 8
    # DKK/MWh -> DKK/kWh (divide by 1000) and hourly ffilled across 4 slots.
    assert df["DK2"].tolist() == pytest.approx([0.1, 0.1, 0.1, 0.1, 0.2, 0.2, 0.2, 0.2])


def test_utc_column_is_tz_aware_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fetch(monkeypatch, hourly=[_hourly("2025-08-31T22:00:00", "DK2", 100.0)])
    df = get_dayahead_prices("2025-09-01T00:00", "2025-09-01T01:00", "DK2")
    assert str(df["UTC"].dt.tz) == "UTC"
    assert df["UTC"].iloc[0] == pd.Timestamp("2025-08-31T22:00", tz="UTC")


# --- fully 15-min era ---------------------------------------------------


def test_fully_quarter_era_scaled(monkeypatch: pytest.MonkeyPatch) -> None:
    # 2025-11-01 00:00..01:00 DK-local (CET) is entirely post-transition.
    quarter = [
        _quarter("2025-10-31T23:00:00", "DK2", 400.0),
        _quarter("2025-10-31T23:15:00", "DK2", 410.0),
        _quarter("2025-10-31T23:30:00", "DK2", 420.0),
        _quarter("2025-10-31T23:45:00", "DK2", 430.0),
    ]
    _install_fetch(monkeypatch, quarter=quarter)
    df = get_dayahead_prices("2025-11-01T00:00", "2025-11-01T01:00", "DK2")
    assert len(df) == 4
    assert df["DK2"].tolist() == pytest.approx([0.4, 0.41, 0.42, 0.43])


# --- straddling the transition -----------------------------------------


def test_straddling_transition_continuity(monkeypatch: pytest.MonkeyPatch) -> None:
    # 2025-09-30 23:00..2025-10-01 01:00 DK-local straddles 22:00 UTC.
    hourly = [_hourly("2025-09-30T21:00:00", "DK2", 300.0)]
    quarter = [
        _quarter("2025-09-30T22:00:00", "DK2", 400.0),
        _quarter("2025-09-30T22:15:00", "DK2", 410.0),
        _quarter("2025-09-30T22:30:00", "DK2", 420.0),
        _quarter("2025-09-30T22:45:00", "DK2", 430.0),
    ]
    _install_fetch(monkeypatch, hourly=hourly, quarter=quarter)
    df = get_dayahead_prices("2025-09-30T23:00", "2025-10-01T01:00", "DK2")
    assert len(df) == 8
    # First 4 slots (21:00..21:45 UTC) from the hourly era, ffilled + scaled.
    assert df["DK2"].iloc[:4].tolist() == pytest.approx([0.3, 0.3, 0.3, 0.3])
    # Next 4 slots (22:00..22:45 UTC) from the native 15-min era.
    assert df["DK2"].iloc[4:].tolist() == pytest.approx([0.4, 0.41, 0.42, 0.43])
    # No NaN gap at the seam.
    assert not df["DK2"].isna().any()


# --- multi-zone columns, order, dedupe ---------------------------------


def test_multi_zone_column_order(monkeypatch: pytest.MonkeyPatch) -> None:
    hourly = [
        _hourly("2025-08-31T22:00:00", "DK1", 100.0),
        _hourly("2025-08-31T22:00:00", "DK2", 200.0),
    ]
    _install_fetch(monkeypatch, hourly=hourly)
    df = get_dayahead_prices("2025-09-01T00:00", "2025-09-01T01:00", ["DK1", "DK2"])
    assert list(df.columns) == ["UTC", "DK1", "DK2"]
    assert df["DK1"].iloc[0] == pytest.approx(0.1)
    assert df["DK2"].iloc[0] == pytest.approx(0.2)


def test_zone_dedupe_preserves_first_order(monkeypatch: pytest.MonkeyPatch) -> None:
    hourly = [
        _hourly("2025-08-31T22:00:00", "DK1", 100.0),
        _hourly("2025-08-31T22:00:00", "DK2", 200.0),
    ]
    _install_fetch(monkeypatch, hourly=hourly)
    df = get_dayahead_prices(
        "2025-09-01T00:00", "2025-09-01T01:00", ["DK2", "DK1", "DK2"]
    )
    assert list(df.columns) == ["UTC", "DK2", "DK1"]


# --- error / edge cases -------------------------------------------------


def test_unknown_zone_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fetch(monkeypatch)
    with pytest.raises(UnknownAreaError):
        get_dayahead_prices("2025-09-01T00:00", "2025-09-01T01:00", "DK3")


def test_empty_both_datasets_raises_no_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A straddling period with zero records in both eras -> NoDataError.
    _install_fetch(monkeypatch, hourly=[], quarter=[])
    with pytest.raises(NoDataError):
        get_dayahead_prices("2025-09-30T23:00", "2025-10-01T01:00", "DK2")


def test_partial_future_leaves_nan_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    # Quarter records only cover the first half of the requested window.
    quarter = [
        _quarter("2025-10-31T23:00:00", "DK2", 400.0),
        _quarter("2025-10-31T23:15:00", "DK2", 410.0),
    ]
    _install_fetch(monkeypatch, quarter=quarter)
    df = get_dayahead_prices("2025-11-01T00:00", "2025-11-01T01:00", "DK2")
    assert len(df) == 4
    assert df["DK2"].iloc[:2].tolist() == pytest.approx([0.4, 0.41])
    # The unpublished tail stays NaN rather than raising.
    assert df["DK2"].iloc[2:].isna().all()


def test_missing_zone_in_records_is_all_nan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # DK1 requested but only DK2 present -> DK1 column is all NaN, full grid.
    hourly = [_hourly("2025-08-31T22:00:00", "DK2", 200.0)]
    _install_fetch(monkeypatch, hourly=hourly)
    df = get_dayahead_prices("2025-09-01T00:00", "2025-09-01T01:00", ["DK1", "DK2"])
    assert df["DK1"].isna().all()
    assert df["DK2"].iloc[0] == pytest.approx(0.2)
