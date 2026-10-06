"""Day-ahead electricity prices for the Danish bidding zones."""

from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timedelta

import pandas as pd

from energydata.utils.chunking import _gather_ordered
from energydata.utils.frames import (
    conform,
    expand_to_resolution,
    period_index,
    records_to_wide,
)
from energydata.utils.periods import DANISH_TZ, TimeLike, resolve_period
from energydata.utils.zones import BIDDING_ZONES, BiddingZone, normalize_bidding_zones

from .client import EnergiDataServiceClient, Record

QUARTER_DATASET = "DayAheadPrices"
QUARTER_TIME = "TimeUTC"
QUARTER_VALUE = "DayAheadPriceEUR"
HOURLY_DATASET = "Elspotprices"
HOURLY_TIME = "HourUTC"
HOURLY_VALUE = "SpotPriceEUR"
AREA = "PriceArea"

SWITCH = pd.Timestamp("2025-10-01 00:00", tz=DANISH_TZ)
"""The first slot priced in 15 minutes; every earlier slot is an hourly price."""

_QUARTER = timedelta(minutes=15)
_HOUR = timedelta(hours=1)


def get_day_ahead_prices(
    start: TimeLike,
    end: TimeLike | None = None,
    bidding_zones: BiddingZone | Sequence[BiddingZone] = BIDDING_ZONES,
    *,
    client: EnergiDataServiceClient | None = None,
) -> pd.DataFrame:
    """Get day-ahead electricity prices per bidding zone.

    | Property | Value |
    |---|---|
    | Currency and unit | EUR/MWh, excl. VAT |
    | Resolution | 15 minutes |
    | Format | wide: one float column per zone |
    | Zones | `DK1`, `DK2` |
    | Source datasets | *DayAheadPrices* (`TimeUTC`, `PriceArea`, `DayAheadPriceEUR`), 15 minutes, current; *Elspotprices* (`HourUTC`, `PriceArea`, `SpotPriceEUR`), hourly, history |

    The index is tz-aware `Europe/Copenhagen` and covers exactly `[start, end)`.
    Prices switched from hourly to 15-minute at 2025-10-01 00:00 Danish time
    (`SWITCH`). Slots before it hold the *Elspotprices* hourly price repeated
    over its four quarter-hours; slots from it on hold only the
    *DayAheadPrices* value, and a null or missing one is NaN, never filled
    from hourly data. A dataset is requested only when the period overlaps its
    side of the switch. A slot with no published data, such as tomorrow before
    publication, is NaN.

    Args:
        start: First moment of the period. A date means local midnight; a
            naive value is read as Danish local time.
        end: First moment after the period (exclusive). `None` means the whole
            local day when `start` is a date, or one 15-minute slot when it is
            a timestamp.
        bidding_zones: One zone or a sequence of them. Both by default; the
            columns follow the order given.
        client: A client to fetch with. `None` creates one and closes it before
            returning; a passed client is left open.

    Returns:
        A frame indexed by every quarter-hour of the period, one column per
        zone.

    Raises:
        ValueError: If `start >= end`, a timestamp is not on a 15-minute
            boundary, a time is nonexistent or ambiguous, a string does not
            parse, or a zone is unknown, repeated or missing.
        EnergiDataServiceError: If the service returns an unexpected payload.
        httpx.HTTPStatusError: If the service refuses a request.
        RuntimeError: If a passed `client` is closed.
    """
    zones = normalize_bidding_zones(bidding_zones)
    first, last = resolve_period(start, end, resolution=_QUARTER)
    index = period_index(first, last, _QUARTER)
    # Each side's request covers only its own part of the period; the hourly one
    # is widened to whole hours so a mid-hour start still gets its hour's price.
    hourly_period = (
        (
            first.tz_convert("UTC").floor("h").to_pydatetime(),
            min(last, SWITCH).tz_convert("UTC").ceil("h").to_pydatetime(),
        )
        if first < SWITCH
        else None
    )
    quarter_period = (
        (max(first, SWITCH).to_pydatetime(), last.to_pydatetime())
        if last > SWITCH
        else None
    )

    owned = client is None
    active = EnergiDataServiceClient() if client is None else client
    try:
        hourly_records, quarter_records = active.run(
            lambda: _fetch_sides(active, zones, hourly_period, quarter_period)
        )
    finally:
        if owned:
            active.close()

    parts: list[pd.DataFrame] = []
    before = index[index < SWITCH]
    if len(before):
        hourly = records_to_wide(
            hourly_records, time=HOURLY_TIME, column=AREA, value=HOURLY_VALUE
        )
        parts.append(
            conform(expand_to_resolution(hourly, _HOUR, _QUARTER), before, zones)
        )
    after = index[index >= SWITCH]
    if len(after):
        quarter = records_to_wide(
            quarter_records, time=QUARTER_TIME, column=AREA, value=QUARTER_VALUE
        )
        parts.append(conform(quarter, after, zones))
    return parts[0] if len(parts) == 1 else pd.concat(parts)


async def _fetch_sides(
    client: EnergiDataServiceClient,
    zones: Sequence[str],
    hourly_period: tuple[datetime, datetime] | None,
    quarter_period: tuple[datetime, datetime] | None,
) -> tuple[list[Record], list[Record]]:
    """Fetch the datasets the period needs, concurrently; a failure cancels the rest.

    Returns the hourly and the 15-minute records; a side with no period is empty.
    """
    filters = {AREA: list(zones)}

    async def hourly(period: tuple[datetime, datetime]) -> list[Record]:
        return await client.fetch_dataset(
            HOURLY_DATASET,
            *period,
            filters=filters,
            columns=[HOURLY_TIME, AREA, HOURLY_VALUE],
            sort_by=HOURLY_TIME,
        )

    async def quarter(period: tuple[datetime, datetime]) -> list[Record]:
        return await client.fetch_dataset(
            QUARTER_DATASET,
            *period,
            filters=filters,
            columns=[QUARTER_TIME, AREA, QUARTER_VALUE],
            sort_by=QUARTER_TIME,
        )

    calls: list[Callable[[], Awaitable[list[Record]]]] = []
    if hourly_period is not None:
        calls.append(lambda: hourly(hourly_period))
    if quarter_period is not None:
        calls.append(lambda: quarter(quarter_period))
    results = iter(await _gather_ordered(calls))
    hourly_records = next(results) if hourly_period is not None else []
    quarter_records = next(results) if quarter_period is not None else []
    return hourly_records, quarter_records


def main() -> None:
    """Showcase this module's functionality (calls the live API)."""
    start: TimeLike = "2026-01-15"  # a date, a datetime or an ISO 8601 string
    end: TimeLike | None = None  # exclusive; None means the whole day for a date
    bidding_zones: Sequence[BiddingZone] = ["DK1", "DK2"]  # "DK1", "DK2", or both

    prices = get_day_ahead_prices(start, end, bidding_zones)

    print(f"{len(prices)} quarter-hours, EUR/MWh")
    print(prices.head(8))

    # A period spanning the switch from hourly to 15-minute prices (2025-10-01).
    start = "2025-09-30"
    end = "2025-10-02"

    prices = get_day_ahead_prices(start, end, bidding_zones)

    print(f"{len(prices)} quarter-hours across the switch")
    print(prices.iloc[92:100])

    # A single slot for one zone.
    start = "2026-01-15T12:00"
    end = None
    bidding_zones = ["DK2"]

    prices = get_day_ahead_prices(start, end, bidding_zones)

    print(prices)


if __name__ == "__main__":
    main()
