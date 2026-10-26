"""Metered consumption and solar export from Min Strøm."""

import pandas as pd

from energydata.utils.periods import TimeLike

from ._series import AGGREGATIONS, Aggregation, _Endpoint, _fetch_series, _Field
from .client import MinStroemClient, _showcase_has_key

_FIELDS = (
    _Field("energy_kwh", "energyKWh", "float"),
    _Field("is_estimate", "isEstimate", "bool"),
)
_CONSUMPTION = _Endpoint(
    "consumption", "consumption", "usage", "aggregation", AGGREGATIONS, _FIELDS, False
)
_SOLAR_EXPORT = _Endpoint(
    "solar-export", "solarExport", "usage", "aggregation", AGGREGATIONS, _FIELDS, False
)


def get_consumption(
    start: TimeLike,
    end: TimeLike | None = None,
    *,
    aggregation: Aggregation = "hour",
    address_id: str | None = None,
    client: MinStroemClient | None = None,
) -> pd.DataFrame:
    """Get metered electricity consumption.

    | Property | Value |
    |---|---|
    | Endpoint | `GET /me/addresses/{id}/consumption` |
    | Unit | kWh |
    | Intervals | `hour`, `day`, `month`, `year` (Danish calendar) |
    | Columns | `energy_kwh` (float), `is_estimate` (`boolean`) |
    | `attrs` | `address_id`, `aggregation` |

    The index is tz-aware `Europe/Copenhagen`, named `"time"`, with one row per
    interval of `[start, end)` (23 or 25 hours on a DST day). An interval the
    API returns no row for is NaN (`<NA>` for `is_estimate`), never zero. A
    period beyond the endpoint's cap is split into calendar-aligned requests.

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
            interval (for example a month interval not starting on the 1st),
            `start >= end`, `address_id` is not a UUID, or `address_id` is
            `None` and the account has no address or several.
        MissingApiKeyError: If there is no API key.
        MinStroemError: If the API refuses a request or returns an unexpected
            payload, or the address lacks the `consumption` capability.
        RetriesExhaustedError: If a network failure outlasted the retries.
        RuntimeError: If a passed `client` is closed.
    """
    return _fetch_series(_CONSUMPTION, start, end, aggregation, address_id, client)


def get_solar_export(
    start: TimeLike,
    end: TimeLike | None = None,
    *,
    aggregation: Aggregation = "hour",
    address_id: str | None = None,
    client: MinStroemClient | None = None,
) -> pd.DataFrame:
    """Get metered solar export.

    The same as `get_consumption`, from `GET /me/addresses/{id}/solar-export`
    and the `solarExport` capability: kWh exported, `energy_kwh` and
    `is_estimate`, one row per interval of `[start, end)`, missing is NaN.

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
        The frame described at `get_consumption`.

    Raises:
        ValueError: As for `get_consumption`.
        MissingApiKeyError: If there is no API key.
        MinStroemError: As for `get_consumption`, for `solarExport`.
        RetriesExhaustedError: If a network failure outlasted the retries.
        RuntimeError: If a passed `client` is closed.
    """
    return _fetch_series(_SOLAR_EXPORT, start, end, aggregation, address_id, client)


def main() -> None:
    """Showcase this module's functionality (calls the live API)."""
    if not _showcase_has_key():
        return
    start = "2026-10-01"
    end: TimeLike | None = "2026-10-08"
    aggregation: Aggregation = "day"  # "hour", "day", "month", "year"

    consumption = get_consumption(start, end, aggregation=aggregation)

    print(f"consumption per {aggregation}: {consumption.shape}")
    print(consumption.head())

    # Solar export, hourly, for one day.
    start = "2026-10-01"
    end = None
    aggregation = "hour"

    export = get_solar_export(start, end, aggregation=aggregation)

    print(f"solar export per {aggregation}: {export.shape}")


if __name__ == "__main__":
    main()
