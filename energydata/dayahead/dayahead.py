"""Day-ahead (spot) electricity price retrieval.

Danish/Nordic day-ahead spot prices are published across two Energi Data
Service datasets:

- ``Elspotprices`` — legacy hourly resolution, covering all data strictly
  before :data:`~energydata.common.timeutils.SPOT_TRANSITION_UTC`.
- ``DayAheadPrices`` — current 15-minute resolution, covering all data
  at/after that same transition point.

:func:`get_dayahead_prices` stitches both sources together onto a single
uniform 15-minute UTC grid, upsampling the hourly era via forward fill.
"""

from typing import Any

import pandas as pd

from energydata.common.client import fetch_dataset
from energydata.common.errors import InvalidPeriodError, NoDataError, UnknownAreaError
from energydata.common.resample import upsample_hourly_to_15min
from energydata.common.timeutils import (
    DK_TZ,
    SPOT_TRANSITION_UTC,
    TimeInput,
    build_utc_15min_index,
    to_utc_timestamp,
)

# Distinct PriceArea values live-verified against the DayAheadPrices dataset
# on 2026-07-10 (see also Elspotprices, whose historical PriceArea coverage
# is a subset of these).
ALLOWED_BZ: frozenset[str] = frozenset({"DE", "DK1", "DK2", "NO2", "SE3", "SE4"})

# Dataset names on the Energi Data Service API.
_HOURLY_DATASET = "Elspotprices"
_QUARTER_DATASET = "DayAheadPrices"

# DKK/MWh -> DKK/kWh.
_MWH_TO_KWH = 1000.0


def get_dayahead_prices(
    start: TimeInput, end: TimeInput, bz: str | list[str] = "DK2"
) -> pd.DataFrame:
    """Fetch day-ahead (spot) electricity prices for one or more bidding zones.

    Args:
        start: Start of the requested period (inclusive). Naive values are
            interpreted as Danish local time. See
            :func:`~energydata.common.timeutils.to_utc_timestamp`.
        end: End of the requested period (exclusive). Same interpretation
            rules as ``start``.
        bz: A single bidding-zone string (e.g. ``"DK2"``) or a list of
            bidding-zone strings (e.g. ``["DK1", "DK2"]``). Duplicates are
            de-duplicated, preserving first occurrence. Must be one of
            :data:`ALLOWED_BZ`.

    Returns:
        A ``pandas.DataFrame`` with a ``UTC`` column (tz-aware UTC
        timestamps at 15-minute steps, half-open ``[start, end)``) plus one
        float column per requested bidding zone (named exactly as requested,
        e.g. ``"DK1"``), in DKK/kWh. Columns appear in requested order.
        Timestamps with no published price (e.g. beyond the day-ahead
        publication horizon) are ``NaN`` rather than causing an error.

    Raises:
        UnknownAreaError: If any requested zone is not in :data:`ALLOWED_BZ`.
        InvalidPeriodError: If ``start``/``end`` cannot be parsed, or
            ``start >= end``.
        NoDataError: If zero records are returned across both datasets for
            the whole requested period (e.g. the period is entirely in the
            future, well beyond any published prices).
    """
    zones = _normalize_zones(bz)
    start_utc = to_utc_timestamp(start)
    end_utc = to_utc_timestamp(end)
    # Validates start_utc < end_utc and builds the master 15-min UTC grid.
    index = build_utc_15min_index(start_utc, end_utc)

    # An empty zone list short-circuits to a UTC-only frame without any API
    # call (an empty PriceArea filter has undefined API semantics), mirroring
    # get_dso_tariffs' behaviour for an empty dso list.
    if not zones:
        return pd.DataFrame({"UTC": index})

    # The grid floors `start` down to its 15-min boundary, so fetch from the
    # floored bound too — otherwise a sub-hour-aligned `start` could exclude
    # the source bucket that covers the first grid slot(s).
    grid_start = index[0]

    total_records = 0
    # Per-zone Series aligned to `index`, combining both eras.
    zone_series: dict[str, pd.Series] = {zone: pd.Series(float("nan"), index=index, dtype=float) for zone in zones}

    # --- Hourly era (Elspotprices), only if part of the period is < transition ---
    if start_utc < SPOT_TRANSITION_UTC:
        hourly_end = min(end_utc, SPOT_TRANSITION_UTC)
        hourly_records = fetch_dataset(
            _HOURLY_DATASET,
            # Floor to the hour: the hourly bucket covering the first grid
            # slot starts at (up to) 59 minutes before it.
            start=_to_dk_naive_str(grid_start.floor("h")),
            end=_to_dk_naive_str(hourly_end),
            filters={"PriceArea": zones},
            columns=["HourUTC", "PriceArea", "SpotPriceDKK"],
            sort="HourUTC ASC",
        )
        total_records += len(hourly_records)
        if hourly_records:
            hourly_pivot = _pivot_records(
                hourly_records, time_field="HourUTC", price_field="SpotPriceDKK"
            )
            for zone in zones:
                if zone in hourly_pivot.columns:
                    # Upsampling directly onto the full grid is safe: source
                    # rows only exist before the transition, so ffill(limit=3)
                    # naturally leaves everything at/after the transition NaN
                    # here (it gets filled in by the quarter-hour era below).
                    zone_series[zone] = upsample_hourly_to_15min(hourly_pivot[zone], index)

    # --- 15-min era (DayAheadPrices), only if part of the period is >= transition ---
    if end_utc > SPOT_TRANSITION_UTC:
        quarter_start = max(grid_start, SPOT_TRANSITION_UTC)
        quarter_records = fetch_dataset(
            _QUARTER_DATASET,
            start=_to_dk_naive_str(quarter_start),
            end=_to_dk_naive_str(end_utc),
            filters={"PriceArea": zones},
            columns=["TimeUTC", "PriceArea", "DayAheadPriceDKK"],
            sort="TimeUTC ASC",
        )
        total_records += len(quarter_records)
        if quarter_records:
            quarter_pivot = _pivot_records(
                quarter_records, time_field="TimeUTC", price_field="DayAheadPriceDKK"
            )
            for zone in zones:
                if zone in quarter_pivot.columns:
                    quarter_col = quarter_pivot[zone].reindex(index)
                    # The two eras are time-disjoint, so fillna is a safe
                    # merge: hourly-era NaNs (at/after the transition) get
                    # replaced by the native 15-min values, and vice versa.
                    zone_series[zone] = zone_series[zone].fillna(quarter_col)

    if total_records == 0:
        raise NoDataError(
            f"no day-ahead price data found for zones {zones} between "
            f"{start_utc} and {end_utc}"
        )

    # Assemble the final frame: a `UTC` column plus one column per zone, in
    # requested order. Building on the shared DatetimeIndex first guarantees
    # correct per-timestamp alignment before resetting to a plain RangeIndex.
    result = pd.DataFrame({"UTC": index}, index=index)
    for zone in zones:
        result[zone] = zone_series[zone]
    return result.reset_index(drop=True)


def _normalize_zones(bz: str | list[str]) -> list[str]:
    """Normalize and validate a bidding-zone argument.

    Args:
        bz: A single bidding-zone string or a list of bidding-zone strings.

    Returns:
        A de-duplicated list of zone strings (first occurrence preserved).

    Raises:
        UnknownAreaError: If any zone is not a member of :data:`ALLOWED_BZ`.
    """
    raw_zones = [bz] if isinstance(bz, str) else list(bz)

    seen: set[str] = set()
    zones: list[str] = []
    for zone in raw_zones:
        if zone not in ALLOWED_BZ:
            allowed = ", ".join(sorted(ALLOWED_BZ))
            raise UnknownAreaError(f"unknown bidding zone {zone!r}; allowed values: {allowed}")
        if zone not in seen:
            seen.add(zone)
            zones.append(zone)
    return zones


def _pivot_records(
    records: list[dict[str, Any]], *, time_field: str, price_field: str
) -> pd.DataFrame:
    """Pivot raw API records into a UTC-indexed, per-zone price DataFrame.

    Args:
        records: Raw JSON records from :func:`~energydata.common.client.fetch_dataset`,
            each containing ``time_field``, ``"PriceArea"``, and ``price_field``.
        time_field: Name of the naive-UTC timestamp field (``"HourUTC"`` or
            ``"TimeUTC"``).
        price_field: Name of the DKK/MWh price field.

    Returns:
        A ``pandas.DataFrame`` indexed by tz-aware UTC timestamp, with one
        column per ``PriceArea`` value present in ``records``, values
        converted to DKK/kWh.
    """
    df = pd.DataFrame.from_records(records)
    # The source strings are naive UTC (not Danish local time); utc=True
    # localizes them directly as UTC rather than reinterpreting them.
    df[time_field] = pd.to_datetime(df[time_field], utc=True)
    pivot = df.pivot_table(index=time_field, columns="PriceArea", values=price_field, aggfunc="first")
    # DKK/MWh -> DKK/kWh.
    return pivot / _MWH_TO_KWH


def _to_dk_naive_str(ts_utc: pd.Timestamp) -> str:
    """Format a tz-aware UTC timestamp as a DK-local naive string.

    The Energi Data Service API's ``start``/``end`` query parameters filter
    using Danish local (wall-clock) time, so UTC timestamps must be
    converted before being sent as query parameters.

    Args:
        ts_utc: A tz-aware UTC timestamp.

    Returns:
        A DK-local naive time string, e.g. ``"2026-07-08T00:00"``.
    """
    return str(ts_utc.tz_convert(DK_TZ).strftime("%Y-%m-%dT%H:%M"))


def main() -> None:
    """Demonstrate get_dayahead_prices with a small recent live period."""
    # A recent 1-day window (yesterday -> today, DK-local) that should be
    # fully published for both DK1 and DK2.
    today_dk = pd.Timestamp.now(tz="UTC").tz_convert(DK_TZ).normalize()
    yesterday_dk = today_dk - pd.Timedelta(days=1)
    start = yesterday_dk.strftime("%Y-%m-%d")
    end = today_dk.strftime("%Y-%m-%d")

    print(f"Fetching day-ahead prices for {start}..{end} (DK1, DK2)...")
    df = get_dayahead_prices(start, end, ["DK1", "DK2"])
    print(df.head())
    print(f"shape={df.shape}")

    # A single-zone string also works (not just a list).
    df_single = get_dayahead_prices(start, end, "DK2")
    print(df_single.head())

    # Duplicate zones are de-duplicated, preserving first occurrence.
    df_dupe = get_dayahead_prices(start, end, ["DK2", "DK1", "DK2"])
    print(f"de-duplicated columns: {list(df_dupe.columns)}")

    try:
        get_dayahead_prices(start, end, "DK3")
    except UnknownAreaError as exc:
        print(f"Caught UnknownAreaError for unknown zone: {exc}")

    try:
        get_dayahead_prices(end, start, "DK2")
    except InvalidPeriodError as exc:
        print(f"Caught InvalidPeriodError for start >= end: {exc}")


if __name__ == "__main__":
    main()
