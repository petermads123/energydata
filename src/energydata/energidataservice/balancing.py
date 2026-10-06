"""Imbalance and balancing-energy prices for the Danish bidding zones."""

from collections.abc import Sequence
from datetime import timedelta

import pandas as pd

from energydata.utils.periods import TimeLike
from energydata.utils.zones import BIDDING_ZONES, BiddingZone

from ._markets import _get, _Market
from .client import EnergiDataServiceClient

_QUARTER = timedelta(minutes=15)

_IMBALANCE = _Market(
    dataset="ImbalancePrice",
    time_field="TimeUTC",
    fields={"up": "ImbalancePriceEUR", "down": "ImbalancePriceEUR"},
    resolution=_QUARTER,
    zoned=True,
)
_AFRR_ENERGY = _Market(
    dataset="ImbalancePrice",
    time_field="TimeUTC",
    fields={"up": "aFRRVWAUpEUR", "down": "aFRRVWADownEUR"},
    resolution=_QUARTER,
    zoned=True,
)
_MFRR_ENERGY = _Market(
    dataset="MfrrEnergyActivationMarket",
    time_field="TimeUTC",
    fields={"up": "mFRRSAUpEUR", "down": "mFRRSADownEUR"},
    resolution=_QUARTER,
    zoned=True,
)


def get_imbalance_prices(
    start: TimeLike,
    end: TimeLike | None = None,
    bidding_zones: BiddingZone | Sequence[BiddingZone] = BIDDING_ZONES,
    *,
    client: EnergiDataServiceClient | None = None,
) -> pd.DataFrame:
    """Get imbalance prices per bidding zone.

    | Property | Value |
    |---|---|
    | Currency and unit | EUR/MWh |
    | Resolution | 15 minutes |
    | Format | wide: MultiIndex columns `(zone, "up" | "down")`, float |
    | Zones | `DK1`, `DK2` |
    | Source dataset | *ImbalancePrice* (`TimeUTC`, `PriceArea`, `ImbalancePriceEUR`) |
    | Data from | 2025-03-04 (first record 12:15 UTC) (earlier slots are NaN) |

    The index is tz-aware `Europe/Copenhagen` and covers exactly `[start, end)`.
    Denmark has a single imbalance price, so `up` and `down` hold the same value
    (`ImbalancePriceEUR`).
    A slot with no published value is NaN, never filled from a neighbour.

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
        A frame indexed by every quarter-hour of the period, with columns
        `(zone, "up")` and `(zone, "down")` for each zone.

    Raises:
        ValueError: If `start >= end`, a timestamp is not on a 15-minute
            boundary, a time is nonexistent or ambiguous, a string does not
            parse, or a zone is unknown, repeated or missing.
        EnergiDataServiceError: If the service returns an unexpected payload.
        httpx.HTTPStatusError: If the service refuses a request.
        RuntimeError: If a passed `client` is closed.
    """
    return _get(_IMBALANCE, start, end, bidding_zones, False, client)


def get_afrr_energy_prices(
    start: TimeLike,
    end: TimeLike | None = None,
    bidding_zones: BiddingZone | Sequence[BiddingZone] = BIDDING_ZONES,
    *,
    client: EnergiDataServiceClient | None = None,
) -> pd.DataFrame:
    """Get aFRR activation-energy prices per bidding zone.

    | Property | Value |
    |---|---|
    | Currency and unit | EUR/MWh |
    | Resolution | 15 minutes |
    | Format | wide: MultiIndex columns `(zone, "up" | "down")`, float |
    | Zones | `DK1`, `DK2` |
    | Source dataset | *ImbalancePrice* (`TimeUTC`, `PriceArea`, `aFRRVWAUpEUR`, `aFRRVWADownEUR`) |
    | Data from | 2025-03-04 (first record 12:15 UTC) (earlier slots are NaN) |

    The index is tz-aware `Europe/Copenhagen` and covers exactly `[start, end)`.
    `up` is the volume-weighted average price of upward aFRR activation
    (`aFRRVWAUpEUR`) and `down` that of downward activation
    (`aFRRVWADownEUR`); a direction not activated in a slot is NaN.
    A slot with no published value is NaN, never filled from a neighbour.

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
        A frame indexed by every quarter-hour of the period, with columns
        `(zone, "up")` and `(zone, "down")` for each zone.

    Raises:
        ValueError: If `start >= end`, a timestamp is not on a 15-minute
            boundary, a time is nonexistent or ambiguous, a string does not
            parse, or a zone is unknown, repeated or missing.
        EnergiDataServiceError: If the service returns an unexpected payload.
        httpx.HTTPStatusError: If the service refuses a request.
        RuntimeError: If a passed `client` is closed.
    """
    return _get(_AFRR_ENERGY, start, end, bidding_zones, False, client)


def get_mfrr_energy_prices(
    start: TimeLike,
    end: TimeLike | None = None,
    bidding_zones: BiddingZone | Sequence[BiddingZone] = BIDDING_ZONES,
    *,
    client: EnergiDataServiceClient | None = None,
) -> pd.DataFrame:
    """Get mFRR scheduled-activation energy prices per bidding zone.

    | Property | Value |
    |---|---|
    | Currency and unit | EUR/MWh |
    | Resolution | 15 minutes |
    | Format | wide: MultiIndex columns `(zone, "up" | "down")`, float |
    | Zones | `DK1`, `DK2` |
    | Source dataset | *MfrrEnergyActivationMarket* (`TimeUTC`, `PriceArea`, `mFRRSAUpEUR`, `mFRRSADownEUR`) |
    | Data from | 2025-03-04 (earlier slots are NaN) |

    The index is tz-aware `Europe/Copenhagen` and covers exactly `[start, end)`.
    `up` is the scheduled-activation marginal price upward (`mFRRSAUpEUR`) and
    `down` the one downward (`mFRRSADownEUR`); a direction not activated in a
    slot is NaN. Direct-activation prices are not included.
    A slot with no published value is NaN, never filled from a neighbour.

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
        A frame indexed by every quarter-hour of the period, with columns
        `(zone, "up")` and `(zone, "down")` for each zone.

    Raises:
        ValueError: If `start >= end`, a timestamp is not on a 15-minute
            boundary, a time is nonexistent or ambiguous, a string does not
            parse, or a zone is unknown, repeated or missing.
        EnergiDataServiceError: If the service returns an unexpected payload.
        httpx.HTTPStatusError: If the service refuses a request.
        RuntimeError: If a passed `client` is closed.
    """
    return _get(_MFRR_ENERGY, start, end, bidding_zones, False, client)


def main() -> None:
    """Showcase this module's functionality (calls the live API)."""
    start: TimeLike = "2026-09-15"  # a date, a datetime or an ISO 8601 string
    end: TimeLike | None = None  # exclusive; None means the whole day for a date
    bidding_zones: Sequence[BiddingZone] = ["DK1", "DK2"]  # "DK1", "DK2", or both

    prices = get_imbalance_prices(start, end, bidding_zones)

    print(f"{len(prices)} quarter-hours of imbalance prices, EUR/MWh")
    print(prices.tail(4))

    # Up and down activation prices differ; a zone with no activation is NaN.
    bidding_zones = ["DK1"]

    prices = get_afrr_energy_prices(start, end, bidding_zones)

    print("aFRR energy, DK1")
    print(prices.tail(4))

    # A single slot of scheduled-activation mFRR energy prices.
    start = "2026-09-15T23:45"
    bidding_zones = ["DK2", "DK1"]  # the columns follow this order

    prices = get_mfrr_energy_prices(start, end, bidding_zones)

    print("mFRR energy, one slot")
    print(prices)


if __name__ == "__main__":
    main()
