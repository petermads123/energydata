"""Day-ahead electricity prices for the Danish bidding zones."""

from collections.abc import Sequence
from datetime import datetime, timedelta

import pandas as pd

from energydata.utils.chunking import _gather_ordered
from energydata.utils.frames import (
    conform,
    expand_to_resolution,
    period_index,
    records_to_wide,
)
from energydata.utils.periods import TimeLike, resolve_period
from energydata.utils.zones import BIDDING_ZONES, BiddingZone, normalize_bidding_zones

from .client import EnergiDataServiceClient, Record

QUARTER_DATASET = "DayAheadPrices"
QUARTER_TIME = "TimeUTC"
QUARTER_VALUE = "DayAheadPriceEUR"
HOURLY_DATASET = "Elspotprices"
HOURLY_TIME = "HourUTC"
HOURLY_VALUE = "SpotPriceEUR"
AREA = "PriceArea"

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
    Hourly history is forward-filled to its four quarter-hours; where both
    datasets have a value for a slot, the 15-minute one wins. A slot with no
    published data, such as tomorrow before publication, is NaN.

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
    """
    zones = normalize_bidding_zones(bidding_zones)
    first, last = resolve_period(start, end, resolution=_QUARTER)
    index = period_index(first, last, _QUARTER)
    hour_start = first.tz_convert("UTC").floor("h")
    hour_end = last.tz_convert("UTC").ceil("h")

    owned = client is None
    active = EnergiDataServiceClient() if client is None else client
    try:
        quarter_records, hourly_records = active.run(
            lambda: _fetch_both(
                active,
                zones,
                (first.to_pydatetime(), last.to_pydatetime()),
                (hour_start.to_pydatetime(), hour_end.to_pydatetime()),
            )
        )
    finally:
        if owned:
            active.close()

    quarter = records_to_wide(
        quarter_records, time=QUARTER_TIME, column=AREA, value=QUARTER_VALUE
    )
    hourly = records_to_wide(
        hourly_records, time=HOURLY_TIME, column=AREA, value=HOURLY_VALUE
    )
    spread = expand_to_resolution(hourly, _HOUR, _QUARTER)
    return conform(quarter.combine_first(spread), index, zones)


async def _fetch_both(
    client: EnergiDataServiceClient,
    zones: Sequence[str],
    quarter_period: tuple[datetime, datetime],
    hourly_period: tuple[datetime, datetime],
) -> tuple[list[Record], list[Record]]:
    """Fetch both datasets concurrently; one failing cancels the other."""
    filters = {AREA: list(zones)}

    async def quarter() -> list[Record]:
        return await client.fetch_dataset(
            QUARTER_DATASET,
            *quarter_period,
            filters=filters,
            columns=[QUARTER_TIME, AREA, QUARTER_VALUE],
            sort_by=QUARTER_TIME,
        )

    async def hourly() -> list[Record]:
        return await client.fetch_dataset(
            HOURLY_DATASET,
            *hourly_period,
            filters=filters,
            columns=[HOURLY_TIME, AREA, HOURLY_VALUE],
            sort_by=HOURLY_TIME,
        )

    quarter_records, hourly_records = await _gather_ordered([quarter, hourly])
    return quarter_records, hourly_records


def main() -> None:
    """Showcase this module's functionality (calls the live API)."""
    start: TimeLike = "2026-01-15"  # a date, a datetime or an ISO 8601 string
    end = None  # exclusive; None means the whole day for a date
    bidding_zones: Sequence[BiddingZone] = ["DK1", "DK2"]  # "DK1", "DK2", or both

    prices = get_day_ahead_prices(start, end, bidding_zones)

    print(f"{len(prices)} quarter-hours, EUR/MWh")
    print(prices.head(8))

    # A single slot for one zone.
    start = "2026-01-15T12:00"
    bidding_zones = ["DK2"]

    prices = get_day_ahead_prices(start, end, bidding_zones)

    print(prices)


if __name__ == "__main__":
    main()
