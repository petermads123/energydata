"""Reserve capacity prices: mFRR, aFRR, FCR-N, FCR-D, FCR DK1 and FFR."""

from collections.abc import Sequence
from datetime import timedelta

import pandas as pd

from energydata.utils.periods import TimeLike
from energydata.utils.zones import BIDDING_ZONES, BiddingZone

from ._markets import _get, _Market
from .client import EnergiDataServiceClient

_HOUR = timedelta(hours=1)
_CAPACITY_VOLUMES = {
    "up_demand": "UpDemandMW",
    "up_procured": "UpProcuredMW",
    "down_demand": "DownDemandMW",
    "down_procured": "DownProcuredMW",
}
_CAPACITY_FIELDS = {"up": "UpPriceEUR", "down": "DownPriceEUR"}
_FCR_VOLUMES = {
    "purchased_local": "PurchasedVolumeLocal",
    "purchased_total": "PurchasedVolumeTotal",
}
_TOTAL = {"AuctionType": ["Total"]}

_MFRR_CAPACITY = _Market(
    dataset="MfrrCapacityMarket",
    time_field="TimeUTC",
    fields=_CAPACITY_FIELDS,
    resolution=_HOUR,
    zoned=True,
    volume_fields=_CAPACITY_VOLUMES,
)
_AFRR_CAPACITY = _Market(
    dataset="AfrrReservesNordic",
    time_field="TimeUTC",
    fields=_CAPACITY_FIELDS,
    resolution=_HOUR,
    zoned=True,
    volume_fields=_CAPACITY_VOLUMES,
)
_FCR_N = _Market(
    dataset="FcrNdDK2",
    time_field="HourUTC",
    fields={"price": "PriceTotalEUR"},
    resolution=_HOUR,
    area="DK2",
    extra_filters={**_TOTAL, "ProductName": ["FCR-N"]},
    volume_fields=_FCR_VOLUMES,
)
_FCR_D_UP = _Market(
    dataset="FcrNdDK2",
    time_field="HourUTC",
    fields={"price": "PriceTotalEUR"},
    resolution=_HOUR,
    area="DK2",
    extra_filters={**_TOTAL, "ProductName": ["FCR-D upp"]},
    volume_fields=_FCR_VOLUMES,
)
_FCR_D_DOWN = _Market(
    dataset="FcrNdDK2",
    time_field="HourUTC",
    fields={"price": "PriceTotalEUR"},
    resolution=_HOUR,
    area="DK2",
    extra_filters={**_TOTAL, "ProductName": ["FCR-D ned"]},
    volume_fields=_FCR_VOLUMES,
)
_FCR_DK1 = _Market(
    dataset="FcrDK1",
    time_field="HourUTC",
    fields={"cross_border": "FCRcross_EUR", "danish": "FCRdk_EUR"},
    resolution=_HOUR,
    volume_fields={"domestic": "FCRdomestic_MW", "abroad": "FCRabroad_MW"},
    block_hours=4,
)
_FFR = _Market(
    dataset="FfrDK2",
    time_field="HourUTC",
    fields={"price": "FFR_PriceEUR"},
    resolution=_HOUR,
    volume_fields={"demand": "FFR_DemandMW", "purchased": "FFR_PurchasedMW"},
)


def get_mfrr_capacity_prices(
    start: TimeLike,
    end: TimeLike | None = None,
    bidding_zones: BiddingZone | Sequence[BiddingZone] = BIDDING_ZONES,
    *,
    include_volumes: bool = False,
    client: EnergiDataServiceClient | None = None,
) -> pd.DataFrame:
    """Get mFRR capacity prices per bidding zone.

    | Property | Value |
    |---|---|
    | Currency and unit | EUR/MW/h |
    | Resolution | 1 hour |
    | Format | wide: MultiIndex columns `(zone, "up" | "down")`, float |
    | Zones | `DK1`, `DK2` |
    | Source dataset | *MfrrCapacityMarket* (`TimeUTC`, `PriceArea`, `UpPriceEUR`, `DownPriceEUR`) |
    | Data from | 2023-06-21 (earlier slots are NaN) |

    The index is tz-aware `Europe/Copenhagen` and covers exactly `[start, end)`.
    `up` is the upward capacity price (`UpPriceEUR`) and `down` the downward
    one (`DownPriceEUR`), in EUR/MW/h. The volumes are the demand and the
    procured volume per direction (`UpDemandMW`, `UpProcuredMW`,
    `DownDemandMW`, `DownProcuredMW`).
    A slot with no published value is NaN, never filled from a neighbour.

    Args:
        start: First moment of the period. A date means local midnight; a
            naive value is read as Danish local time.
        end: First moment after the period (exclusive). `None` means the whole
            local day when `start` is a date, or one slot when it is a
            timestamp.
        bidding_zones: One zone or a sequence of them. Both by default; the
            columns follow the order given.
        include_volumes: Whether to add the volume columns, in MW:
            `up_demand`, `up_procured`, `down_demand` and `down_procured`.
        client: A client to fetch with. `None` creates one and closes it before
            returning; a passed client is left open.

    Returns:
        A frame with columns `(zone, field)`, zone-major: for each zone `up` and
            `down`, then each volume when `include_volumes` is true.

    Raises:
        ValueError: If `start >= end`, a timestamp is not on a slot boundary, a
            time is nonexistent or ambiguous, a string does not parse, or a zone is unknown, repeated or missing.
        EnergiDataServiceError: If the service returns an unexpected payload.
        httpx.HTTPStatusError: If the service refuses a request.
        RuntimeError: If a passed `client` is closed.
    """
    return _get(_MFRR_CAPACITY, start, end, bidding_zones, include_volumes, client)


def get_afrr_capacity_prices(
    start: TimeLike,
    end: TimeLike | None = None,
    bidding_zones: BiddingZone | Sequence[BiddingZone] = BIDDING_ZONES,
    *,
    include_volumes: bool = False,
    client: EnergiDataServiceClient | None = None,
) -> pd.DataFrame:
    """Get aFRR capacity prices per bidding zone.

    | Property | Value |
    |---|---|
    | Currency and unit | EUR/MW/h |
    | Resolution | 1 hour |
    | Format | wide: MultiIndex columns `(zone, "up" | "down")`, float |
    | Zones | `DK1`, `DK2` |
    | Source dataset | *AfrrReservesNordic* (`TimeUTC`, `PriceArea`, `UpPriceEUR`, `DownPriceEUR`) |
    | Data from | 2022-12-08 (earlier slots are NaN) |

    The index is tz-aware `Europe/Copenhagen` and covers exactly `[start, end)`.
    Same fields and columns as `get_mfrr_capacity_prices`. The request filters
    to the selected Danish zones, so the Nordic zones the dataset also holds
    are never returned.
    A slot with no published value is NaN, never filled from a neighbour.

    Args:
        start: First moment of the period. A date means local midnight; a
            naive value is read as Danish local time.
        end: First moment after the period (exclusive). `None` means the whole
            local day when `start` is a date, or one slot when it is a
            timestamp.
        bidding_zones: One zone or a sequence of them. Both by default; the
            columns follow the order given.
        include_volumes: Whether to add the volume columns, in MW:
            `up_demand`, `up_procured`, `down_demand` and `down_procured`.
        client: A client to fetch with. `None` creates one and closes it before
            returning; a passed client is left open.

    Returns:
        A frame with columns `(zone, field)`, zone-major: for each zone `up` and
            `down`, then each volume when `include_volumes` is true.

    Raises:
        ValueError: If `start >= end`, a timestamp is not on a slot boundary, a
            time is nonexistent or ambiguous, a string does not parse, or a zone is unknown, repeated or missing.
        EnergiDataServiceError: If the service returns an unexpected payload.
        httpx.HTTPStatusError: If the service refuses a request.
        RuntimeError: If a passed `client` is closed.
    """
    return _get(_AFRR_CAPACITY, start, end, bidding_zones, include_volumes, client)


def get_fcr_n_prices(
    start: TimeLike,
    end: TimeLike | None = None,
    *,
    include_volumes: bool = False,
    client: EnergiDataServiceClient | None = None,
) -> pd.DataFrame:
    """Get FCR-N (normal operation) prices for DK2.

    | Property | Value |
    |---|---|
    | Currency and unit | EUR/MW/h |
    | Resolution | 1 hour |
    | Format | wide: one `price` column, float |
    | Zones | `DK2` only; no zone argument |
    | Source dataset | *FcrNdDK2* (`HourUTC`, `PriceArea`, `ProductName`, `AuctionType`, `PriceTotalEUR`, `PurchasedVolumeLocal`, `PurchasedVolumeTotal`) |
    | Data from | 2021-11-10 (earlier slots are NaN) |

    The index is tz-aware `Europe/Copenhagen` and covers exactly `[start, end)`.
    `price` is the volume-weighted `Total` of the auctions (`PriceTotalEUR`, `ProductName = "FCR-N"`, `AuctionType = "Total"`, `PriceArea = "DK2"`). The volumes
    are `purchased_local` (`PurchasedVolumeLocal`) and `purchased_total`
    (`PurchasedVolumeTotal`).
    A slot with no published value is NaN, never filled from a neighbour.

    Args:
        start: First moment of the period. A date means local midnight; a
            naive value is read as Danish local time.
        end: First moment after the period (exclusive). `None` means the whole
            local day when `start` is a date, or one slot when it is a
            timestamp.
        include_volumes: Whether to add the volume columns, in MW:
            `purchased_local` and `purchased_total`.
        client: A client to fetch with. `None` creates one and closes it before
            returning; a passed client is left open.

    Returns:
        A frame with one `price` column, then `purchased_local` and
        `purchased_total` when `include_volumes` is true.

    Raises:
        ValueError: If `start >= end`, a timestamp is not on a slot boundary, a
            time is nonexistent or ambiguous, a string does not parse.
        EnergiDataServiceError: If the service returns an unexpected payload.
        httpx.HTTPStatusError: If the service refuses a request.
        RuntimeError: If a passed `client` is closed.
    """
    return _get(_FCR_N, start, end, None, include_volumes, client)


def get_fcr_d_up_prices(
    start: TimeLike,
    end: TimeLike | None = None,
    *,
    include_volumes: bool = False,
    client: EnergiDataServiceClient | None = None,
) -> pd.DataFrame:
    """Get FCR-D upward (disturbance) prices for DK2.

    | Property | Value |
    |---|---|
    | Currency and unit | EUR/MW/h |
    | Resolution | 1 hour |
    | Format | wide: one `price` column, float |
    | Zones | `DK2` only; no zone argument |
    | Source dataset | *FcrNdDK2* (`HourUTC`, `PriceArea`, `ProductName`, `AuctionType`, `PriceTotalEUR`, `PurchasedVolumeLocal`, `PurchasedVolumeTotal`) |
    | Data from | 2021-11-10 (earlier slots are NaN) |

    The index is tz-aware `Europe/Copenhagen` and covers exactly `[start, end)`.
    `price` is the volume-weighted `Total` of the auctions (`PriceTotalEUR`, `ProductName = "FCR-D upp"`, `AuctionType = "Total"`, `PriceArea = "DK2"`). The volumes
    are `purchased_local` (`PurchasedVolumeLocal`) and `purchased_total`
    (`PurchasedVolumeTotal`).
    A slot with no published value is NaN, never filled from a neighbour.

    Args:
        start: First moment of the period. A date means local midnight; a
            naive value is read as Danish local time.
        end: First moment after the period (exclusive). `None` means the whole
            local day when `start` is a date, or one slot when it is a
            timestamp.
        include_volumes: Whether to add the volume columns, in MW:
            `purchased_local` and `purchased_total`.
        client: A client to fetch with. `None` creates one and closes it before
            returning; a passed client is left open.

    Returns:
        A frame with one `price` column, then `purchased_local` and
        `purchased_total` when `include_volumes` is true.

    Raises:
        ValueError: If `start >= end`, a timestamp is not on a slot boundary, a
            time is nonexistent or ambiguous, a string does not parse.
        EnergiDataServiceError: If the service returns an unexpected payload.
        httpx.HTTPStatusError: If the service refuses a request.
        RuntimeError: If a passed `client` is closed.
    """
    return _get(_FCR_D_UP, start, end, None, include_volumes, client)


def get_fcr_d_down_prices(
    start: TimeLike,
    end: TimeLike | None = None,
    *,
    include_volumes: bool = False,
    client: EnergiDataServiceClient | None = None,
) -> pd.DataFrame:
    """Get FCR-D downward (disturbance) prices for DK2.

    | Property | Value |
    |---|---|
    | Currency and unit | EUR/MW/h |
    | Resolution | 1 hour |
    | Format | wide: one `price` column, float |
    | Zones | `DK2` only; no zone argument |
    | Source dataset | *FcrNdDK2* (`HourUTC`, `PriceArea`, `ProductName`, `AuctionType`, `PriceTotalEUR`, `PurchasedVolumeLocal`, `PurchasedVolumeTotal`) |
    | Data from | 2021-11-10 (earlier slots are NaN) |

    The index is tz-aware `Europe/Copenhagen` and covers exactly `[start, end)`.
    `price` is the volume-weighted `Total` of the auctions (`PriceTotalEUR`, `ProductName = "FCR-D ned"`, `AuctionType = "Total"`, `PriceArea = "DK2"`). The volumes
    are `purchased_local` (`PurchasedVolumeLocal`) and `purchased_total`
    (`PurchasedVolumeTotal`).
    A slot with no published value is NaN, never filled from a neighbour.

    Args:
        start: First moment of the period. A date means local midnight; a
            naive value is read as Danish local time.
        end: First moment after the period (exclusive). `None` means the whole
            local day when `start` is a date, or one slot when it is a
            timestamp.
        include_volumes: Whether to add the volume columns, in MW:
            `purchased_local` and `purchased_total`.
        client: A client to fetch with. `None` creates one and closes it before
            returning; a passed client is left open.

    Returns:
        A frame with one `price` column, then `purchased_local` and
        `purchased_total` when `include_volumes` is true.

    Raises:
        ValueError: If `start >= end`, a timestamp is not on a slot boundary, a
            time is nonexistent or ambiguous, a string does not parse.
        EnergiDataServiceError: If the service returns an unexpected payload.
        httpx.HTTPStatusError: If the service refuses a request.
        RuntimeError: If a passed `client` is closed.
    """
    return _get(_FCR_D_DOWN, start, end, None, include_volumes, client)


def get_fcr_dk1_prices(
    start: TimeLike,
    end: TimeLike | None = None,
    *,
    include_volumes: bool = False,
    client: EnergiDataServiceClient | None = None,
) -> pd.DataFrame:
    """Get FCR prices for DK1, per 4-hour block.

    | Property | Value |
    |---|---|
    | Currency and unit | EUR/MW/h |
    | Resolution | 4-hour blocks (published hourly) |
    | Format | wide: columns `cross_border` and `danish`, float |
    | Zones | `DK1` only; no zone argument |
    | Source dataset | *FcrDK1* (`HourUTC`, `FCRcross_EUR`, `FCRdk_EUR`, `FCRdomestic_MW`, `FCRabroad_MW`) |
    | Data from | 2021-01-19 (earlier slots are NaN) |

    The index is tz-aware `Europe/Copenhagen` and covers exactly `[start, end)`.
    The dataset is published hourly but constant over each 4-hour block, so the
    index is the block starts (00, 04, ..., 20 Danish local time; on a DST day
    the block holding the change is shorter or longer) and each row takes its
    block's first hour. `cross_border` is `FCRcross_EUR` and `danish` is
    `FCRdk_EUR`; the volumes are `domestic` (`FCRdomestic_MW`) and `abroad`
    (`FCRabroad_MW`). A lone date gives that day's blocks; a lone timestamp
    gives one block and must be a block start.
    A slot with no published value is NaN, never filled from a neighbour.

    Args:
        start: First moment of the period. A date means local midnight; a
            naive value is read as Danish local time.
        end: First moment after the period (exclusive). `None` means the whole
            local day when `start` is a date, or one slot when it is a
            timestamp.
        include_volumes: Whether to add the volume columns, in MW:
            `domestic` and `abroad`.
        client: A client to fetch with. `None` creates one and closes it before
            returning; a passed client is left open.

    Returns:
        A frame indexed by block start, with columns `cross_border` and `danish`,
        then `domestic` and `abroad` when `include_volumes` is true.

    Raises:
        ValueError: If `start >= end`, a timestamp is not on a slot boundary, a
            time is nonexistent or ambiguous, a string does not parse.
        EnergiDataServiceError: If the service returns an unexpected payload.
        httpx.HTTPStatusError: If the service refuses a request.
        RuntimeError: If a passed `client` is closed.
    """
    return _get(_FCR_DK1, start, end, None, include_volumes, client)


def get_ffr_prices(
    start: TimeLike,
    end: TimeLike | None = None,
    *,
    include_volumes: bool = False,
    client: EnergiDataServiceClient | None = None,
) -> pd.DataFrame:
    """Get FFR (fast frequency reserve) prices for DK2.

    | Property | Value |
    |---|---|
    | Currency and unit | EUR/MW/h |
    | Resolution | 1 hour |
    | Format | wide: one `price` column, float |
    | Zones | `DK2` only; no zone argument |
    | Source dataset | *FfrDK2* (`HourUTC`, `FFR_PriceEUR`, `FFR_DemandMW`, `FFR_PurchasedMW`) |
    | Data from | 2021-04-26 (earlier slots are NaN) |

    The index is tz-aware `Europe/Copenhagen` and covers exactly `[start, end)`.
    `price` is the FFR price (`FFR_PriceEUR`). The volumes are `demand`
    (`FFR_DemandMW`) and `purchased` (`FFR_PurchasedMW`).
    A slot with no published value is NaN, never filled from a neighbour.

    Args:
        start: First moment of the period. A date means local midnight; a
            naive value is read as Danish local time.
        end: First moment after the period (exclusive). `None` means the whole
            local day when `start` is a date, or one slot when it is a
            timestamp.
        include_volumes: Whether to add the volume columns, in MW:
            `demand` and `purchased`.
        client: A client to fetch with. `None` creates one and closes it before
            returning; a passed client is left open.

    Returns:
        A frame with one `price` column, then `demand` and `purchased` when
        `include_volumes` is true.

    Raises:
        ValueError: If `start >= end`, a timestamp is not on a slot boundary, a
            time is nonexistent or ambiguous, a string does not parse.
        EnergiDataServiceError: If the service returns an unexpected payload.
        httpx.HTTPStatusError: If the service refuses a request.
        RuntimeError: If a passed `client` is closed.
    """
    return _get(_FFR, start, end, None, include_volumes, client)


def main() -> None:
    """Showcase this module's functionality (calls the live API)."""
    start: TimeLike = "2026-09-15"  # a date, a datetime or an ISO 8601 string
    end: TimeLike | None = None  # exclusive; None means the whole day for a date
    bidding_zones: Sequence[BiddingZone] = ["DK1", "DK2"]  # "DK1", "DK2", or both
    include_volumes = False  # True adds demand and procured volumes in MW

    prices = get_mfrr_capacity_prices(
        start, end, bidding_zones, include_volumes=include_volumes
    )

    print(f"{len(prices)} hours of mFRR capacity prices, EUR/MW/h")
    print(prices.tail(3))

    # FCR-D downward with volumes; DK2 only, so there is no zone argument.
    include_volumes = True

    prices = get_fcr_d_down_prices(start, end, include_volumes=include_volumes)

    print("FCR-D down, with volumes")
    print(prices.tail(3))

    # FCR DK1 is indexed by 4-hour blocks: six rows for the day.
    prices = get_fcr_dk1_prices(start, end)

    print("FCR DK1, per 4-hour block")
    print(prices)


if __name__ == "__main__":
    main()
