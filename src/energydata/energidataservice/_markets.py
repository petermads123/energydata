"""Private: the shared fetch-and-shape path behind the market price functions."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta

import pandas as pd

from energydata.utils.frames import (
    block_index,
    combine_levels,
    conform,
    period_index,
    records_to_wide,
)
from energydata.utils.periods import TimeLike, resolve_period
from energydata.utils.zones import BIDDING_ZONES, BiddingZone, normalize_bidding_zones

from .client import EnergiDataServiceClient

AREA = "PriceArea"
_KEY = "_key"
_HOUR = timedelta(hours=1)


@dataclass(frozen=True)
class _Market:
    """The facts that tell one market's dataset apart from another's.

    Attributes:
        dataset: Dataset name.
        time_field: The dataset's UTC time field.
        fields: Output column name to source field, in output order.
        resolution: The dataset's slot length.
        zoned: Whether the result has a zone level above the field names.
        area: For an unzoned dataset that has a `PriceArea` field, the one area
            to read; `None` for a dataset with no such field.
        extra_filters: Further request filters, such as the FCR product name.
        volume_fields: Output column name to source field for the volumes
            added by `include_volumes`.
        block_hours: Set for a market indexed by wall-clock blocks of this many
            hours, whose records are hourly; `None` otherwise.
    """

    dataset: str
    time_field: str
    fields: Mapping[str, str]
    resolution: timedelta
    zoned: bool = False
    area: str | None = None
    extra_filters: Mapping[str, Sequence[str]] = field(default_factory=dict)
    volume_fields: Mapping[str, str] = field(default_factory=dict)
    block_hours: int | None = None


def _get(
    market: _Market,
    start: TimeLike,
    end: TimeLike | None,
    bidding_zones: BiddingZone | Sequence[BiddingZone] | None,
    include_volumes: bool,
    client: EnergiDataServiceClient | None,
) -> pd.DataFrame:
    """Fetch one market's records and shape them into its frame.

    Args:
        market: The market to read.
        start: First moment of the period, as for `resolve_period`.
        end: First moment after the period, or `None`.
        bidding_zones: The zones, for a zoned market; ignored otherwise.
        include_volumes: Whether to add the market's volume columns.
        client: A client to fetch with; `None` creates one and closes it.

    Returns:
        The frame, indexed by every slot (or block start) of the period.
    """
    zones: tuple[BiddingZone, ...] = ()
    if market.zoned:
        zones = normalize_bidding_zones(
            BIDDING_ZONES if bidding_zones is None else bidding_zones
        )
    index, first, last = _period(market, start, end)

    columns = dict(market.fields)
    if include_volumes:
        columns.update(market.volume_fields)
    sources = list(dict.fromkeys(columns.values()))
    filters: dict[str, Sequence[str]] = dict(market.extra_filters)
    requested = [market.time_field, *sources]
    if market.zoned:
        filters[AREA] = list(zones)
        requested.insert(1, AREA)
    elif market.area is not None:
        filters[AREA] = [market.area]
        requested.insert(1, AREA)

    owned = client is None
    active = EnergiDataServiceClient() if client is None else client
    try:
        records = active.run(
            lambda: active.fetch_dataset(
                market.dataset,
                first.to_pydatetime(),
                last.to_pydatetime(),
                filters=filters,
                columns=requested,
                sort_by=market.time_field,
            )
        )
    finally:
        if owned:
            active.close()

    if market.zoned:
        wide = {
            source: records_to_wide(
                records, time=market.time_field, column=AREA, value=source
            )
            for source in sources
        }
        return combine_levels(
            {name: wide[source] for name, source in columns.items()}, index, zones
        )

    key = market.area if market.area is not None else _KEY
    keyed: Sequence[Mapping[str, object]] = (
        records
        if market.area is not None
        else [{**record, _KEY: _KEY} for record in records]
    )
    column = AREA if market.area is not None else _KEY
    singles = []
    for name, source in columns.items():
        wide_one = records_to_wide(
            keyed, time=market.time_field, column=column, value=source
        )
        single = conform(wide_one, index, [key])
        singles.append(single.rename(columns={key: name}))
    return pd.concat(singles, axis=1)


def _period(
    market: _Market, start: TimeLike, end: TimeLike | None
) -> tuple[pd.DatetimeIndex, pd.Timestamp, pd.Timestamp]:
    """Resolve the period into the output index and the span to fetch."""
    if market.block_hours is None:
        first, last = resolve_period(start, end, resolution=market.resolution)
        return period_index(first, last, market.resolution), first, last
    first, last = resolve_period(start, end, resolution=_HOUR)
    if end is None and last - first == pd.Timedelta(_HOUR):
        # A lone timestamp names one block: end at the next block start in
        # wall-clock time (a block start is never inside a DST hour).
        wall = first.tz_localize(None) + pd.Timedelta(hours=market.block_hours)
        last = wall.tz_localize(first.tz)
    return block_index(first, last, market.block_hours), first, last


def main() -> None:
    """Showcase this module's private period handling (offline)."""
    market = _Market(
        dataset="FcrDK1",
        time_field="HourUTC",
        fields={"cross_border": "FCRcross_EUR", "danish": "FCRdk_EUR"},
        resolution=_HOUR,
        block_hours=4,
    )
    start: TimeLike = "2026-10-25T04:00"  # a lone timestamp: one 4-hour block
    end: TimeLike | None = None

    index, first, last = _period(market, start, end)

    print(f"index {list(index)}, fetch [{first}, {last})")


if __name__ == "__main__":
    main()
