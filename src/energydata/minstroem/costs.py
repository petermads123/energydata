"""Calculated consumption costs and solar-export revenue from Min Strøm."""

import pandas as pd

from energydata.utils.periods import TimeLike

from ._series import AGGREGATIONS, Aggregation, _Endpoint, _fetch_series, _Field
from .client import MinStroemClient, _showcase_has_key

_CONSUMPTION_COSTS = _Endpoint(
    "consumption-costs",
    "consumptionCosts",
    "costs",
    "aggregation",
    AGGREGATIONS,
    (
        _Field("energy_kwh", "energyKWh", "float"),
        _Field("total_price_per_kwh", "totalPricePerKWh", "float", nullable=True),
        _Field("fixed_fees", "fixedFeesForInterval", "float"),
        _Field("total_cost", "totalCost", "float"),
        _Field("is_estimate", "isEstimate", "bool"),
    ),
    True,
)
_SOLAR_EXPORT_REVENUE = _Endpoint(
    "solar-export-revenue",
    "solarExportRevenue",
    "costs",
    "aggregation",
    AGGREGATIONS,
    (
        _Field("energy_kwh", "energyKWh", "float"),
        _Field("total_price_per_kwh", "totalPricePerKWh", "float", nullable=True),
        _Field("fixed_fees", "fixedFeesForInterval", "float"),
        _Field("net_revenue", "netRevenue", "float"),
        _Field("is_estimate", "isEstimate", "bool"),
    ),
    True,
)


def get_consumption_costs(
    start: TimeLike,
    end: TimeLike | None = None,
    *,
    aggregation: Aggregation = "hour",
    address_id: str | None = None,
    client: MinStroemClient | None = None,
) -> pd.DataFrame:
    """Get calculated consumption costs.

    | Property | Value |
    |---|---|
    | Endpoint | `GET /me/addresses/{id}/consumption-costs` |
    | Currency | `attrs["currency"]` (`DKK`, `SEK` or `NOK`), incl. VAT (`attrs["vat_included"]` is `True`) |
    | Intervals | `hour`, `day`, `month`, `year` (Danish calendar) |
    | Columns | `energy_kwh` (kWh), `total_price_per_kwh` (effective rate; NaN where the API sends null, as it does at zero energy), `fixed_fees`, `total_cost` (currency), `is_estimate` (`boolean`) |
    | `attrs` | `address_id`, `aggregation`, `currency`, `vat_included` |

    The index is tz-aware `Europe/Copenhagen`, named `"time"`, with one row per
    interval of `[start, end)`; an interval with no row is NaN, never zero.
    A period beyond the endpoint's cap is split into calendar-aligned requests.

    Args:
        start: First moment of the period. A date means local midnight; a
            naive value is read as Danish local time.
        end: First moment after the period (exclusive). `None` means the whole
            local day when `start` is a date, or one hour when it is a
            timestamp, so with `aggregation` `"day"`, `"month"` or `"year"` a
            lone timestamp is refused (its derived `end` is not on the
            interval), and a lone date is one `"day"` but never a `"month"` or
            `"year"`.
        aggregation: The interval: `"hour"`, `"day"`, `"month"` or `"year"`.
        address_id: The address (a UUID). `None` uses the account's only
            address.
        client: A client to fetch with. `None` creates one and closes it before
            returning; a passed client is left open.

    Returns:
        The frame described above.

    Raises:
        ValueError: If `aggregation` is unknown, a bound is not on the
            interval, `start >= end`, `address_id` is not a UUID, or it is
            `None` and the account has no address or several.
        MissingApiKeyError: If there is no API key.
        MinStroemError: If the API refuses a request, returns an unexpected
            payload or windows disagree on currency, or the address lacks the
            `consumptionCosts` capability.
        RetriesExhaustedError: If a network failure outlasted the retries.
        RuntimeError: If a passed `client` is closed.
    """
    return _fetch_series(
        _CONSUMPTION_COSTS, start, end, aggregation, address_id, client
    )


def get_solar_export_revenue(
    start: TimeLike,
    end: TimeLike | None = None,
    *,
    aggregation: Aggregation = "hour",
    address_id: str | None = None,
    client: MinStroemClient | None = None,
) -> pd.DataFrame:
    """Get calculated solar-export revenue.

    The same as `get_consumption_costs`, from
    `GET /me/addresses/{id}/solar-export-revenue` and the `solarExportRevenue`
    capability, with `net_revenue` in place of `total_cost`, and excl. VAT
    (`attrs["vat_included"]` is `False`).

    Args:
        start: First moment of the period. A date means local midnight; a
            naive value is read as Danish local time.
        end: First moment after the period (exclusive). `None` means the whole
            local day when `start` is a date, or one hour when it is a
            timestamp, so with `aggregation` `"day"`, `"month"` or `"year"` a
            lone timestamp is refused (its derived `end` is not on the
            interval), and a lone date is one `"day"` but never a `"month"` or
            `"year"`.
        aggregation: The interval: `"hour"`, `"day"`, `"month"` or `"year"`.
        address_id: The address (a UUID). `None` uses the account's only
            address.
        client: A client to fetch with. `None` creates one and closes it before
            returning; a passed client is left open.

    Returns:
        Columns `energy_kwh`, `total_price_per_kwh`, `fixed_fees`,
        `net_revenue` and `is_estimate`, indexed as `get_consumption_costs`.

    Raises:
        ValueError: As for `get_consumption_costs`.
        MissingApiKeyError: If there is no API key.
        MinStroemError: As for `get_consumption_costs`, for
            `solarExportRevenue`.
        RetriesExhaustedError: If a network failure outlasted the retries.
        RuntimeError: If a passed `client` is closed.
    """
    return _fetch_series(
        _SOLAR_EXPORT_REVENUE, start, end, aggregation, address_id, client
    )


def main() -> None:
    """Showcase this module's functionality (calls the live API)."""
    if not _showcase_has_key():
        return
    start = "2026-09-01"
    end = "2026-10-01"
    aggregation: Aggregation = "month"  # "hour", "day", "month", "year"

    costs = get_consumption_costs(start, end, aggregation=aggregation)

    print(f"costs per {aggregation} in {costs.attrs['currency']}: {costs.shape}")
    print(costs.head())

    # Revenue per day over a week.
    start = "2026-10-01"
    end = "2026-10-08"
    aggregation = "day"

    revenue = get_solar_export_revenue(start, end, aggregation=aggregation)

    print(f"revenue per {aggregation}: {revenue.shape}")


if __name__ == "__main__":
    main()
