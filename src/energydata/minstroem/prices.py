"""Electricity prices from Min Strøm: history, forecast and the two combined."""

from dataclasses import dataclass
from datetime import timedelta

import pandas as pd

from energydata.utils.frames import period_index
from energydata.utils.periods import DANISH_TZ, TimeLike, resolve_period

from ._series import (
    PRICE_RESOLUTIONS,
    PriceResolution,
    _build,
    _check_address_id,
    _client_scope,
    _empty_index,
    _Endpoint,
    _fetch_series,
    _fetch_windows,
    _Fetched,
    _Field,
    _parse,
    _resolve_address,
)
from .client import MinStroemClient, MinStroemError, _showcase_has_key

_HOUR = timedelta(hours=1)
_FIELDS = (
    _Field("total_price_per_kwh", "totalPricePerKWh", "float", nullable=True),
    _Field("variable_charges_per_kwh", "variableChargesPerKWh", "float", nullable=True),
)
_PRICES = _Endpoint(
    "prices", "prices", "prices", "resolution", PRICE_RESOLUTIONS, _FIELDS, True
)
_FORECAST = _Endpoint(
    "forecast-prices", "forecastPrices", "prices", "", (), _FIELDS, True
)
_PRICE_COLUMNS = ("total_price_per_kwh", "variable_charges_per_kwh")


@dataclass(frozen=True)
class _Forecast:
    """A checked forecast response on an hourly frame."""

    frame: pd.DataFrame
    hours: pd.DatetimeIndex
    currency: str | None
    vat_included: bool | None
    fetched_at: pd.Timestamp


def get_prices(
    start: TimeLike,
    end: TimeLike | None = None,
    *,
    resolution: PriceResolution = "hour",
    address_id: str | None = None,
    client: MinStroemClient | None = None,
) -> pd.DataFrame:
    """Get historical (confirmed) electricity prices for an address.

    | Property | Value |
    |---|---|
    | Endpoint | `GET /me/addresses/{id}/prices` |
    | Unit | currency/kWh incl. VAT, three decimals (`attrs["currency"]`: `DKK`, `SEK` or `NOK`) |
    | Intervals | `hour`, `fifteenMinutes` |
    | Columns | `total_price_per_kwh`, `variable_charges_per_kwh` (float; NaN where null) |
    | `attrs` | `address_id`, `resolution`, `currency`, `vat_included` |

    The index is tz-aware `Europe/Copenhagen`, named `"time"`, with one row per
    interval of `[start, end)` (23 or 25 hours on a DST day); an interval with
    no row is NaN, never zero. A period beyond the cap (366 days hourly, 90
    days quarter-hourly) is split into calendar-aligned requests.

    Args:
        start: First moment of the period. A date means local midnight; a
            naive value is read as Danish local time.
        end: First moment after the period (exclusive). `None` means the whole
            local day when `start` is a date, or one interval when it is a
            timestamp.
        resolution: `"hour"` or `"fifteenMinutes"`.
        address_id: The address (a UUID). `None` uses the account's only
            address.
        client: A client to fetch with. `None` creates one and closes it before
            returning; a passed client is left open.

    Returns:
        The frame described above.

    Raises:
        ValueError: If `resolution` is unknown, a bound is not on the
            interval, `start >= end`, `address_id` is not a UUID, or it is
            `None` and the account has no address or several.
        MissingApiKeyError: If there is no API key.
        MinStroemError: If the API refuses a request or returns an unexpected
            payload, or the address lacks the `prices` capability.
        RuntimeError: If a passed `client` is closed.
    """
    return _fetch_series(_PRICES, start, end, resolution, address_id, client)


def get_forecast_prices(
    *,
    address_id: str | None = None,
    client: MinStroemClient | None = None,
) -> pd.DataFrame:
    """Get Min Strøm's hourly price forecast for an address.

    | Property | Value |
    |---|---|
    | Endpoint | `GET /me/addresses/{id}/forecast-prices` |
    | Unit | currency/kWh incl. VAT, three decimals |
    | Resolution | one hour |
    | Columns | `total_price_per_kwh`, `variable_charges_per_kwh` (float; NaN where null) |
    | `attrs` | `address_id`, `currency`, `vat_included`, `fetched_at` |

    There is no period: the rows the API returns come out, indexed by hour
    start, tz-aware `Europe/Copenhagen`, named `"time"`, from the first to the
    last forecast hour with any gap inside NaN. `fetched_at` is the tz-aware
    time the response arrived, so a caller who wants to keep forecast vintages
    can store snapshots. An empty forecast is an empty frame with the same
    columns, an empty tz-aware index and the same `attrs`.

    Args:
        address_id: The address (a UUID). `None` uses the account's only
            address, and refuses one whose `forecastPrices` capability is
            false before requesting the forecast.
        client: A client to fetch with. `None` creates one and closes it before
            returning; a passed client is left open.

    Returns:
        The frame described above.

    Raises:
        ValueError: If `address_id` is not a UUID, or is `None` and the account
            has no address or several.
        MissingApiKeyError: If there is no API key.
        MinStroemError: If the API refuses the request (a 422
            `feature_unavailable` names the `forecastPrices` capability),
            returns an unexpected payload, or the address lacks the capability.
        RuntimeError: If a passed `client` is closed.
    """
    wanted = address_id if address_id is None else _check_address_id(address_id)

    async def work(active: MinStroemClient) -> tuple[str, _Forecast]:
        address = await _resolve_address(active, wanted, "forecastPrices")
        return address, await _fetch_forecast(active, address)

    with _client_scope(client) as active:
        address, forecast = active.run(lambda: work(active))
    frame = forecast.frame
    frame.attrs.update(
        address_id=address,
        currency=forecast.currency,
        vat_included=forecast.vat_included,
        fetched_at=forecast.fetched_at,
    )
    return frame


def get_prices_with_forecast(
    start: TimeLike,
    end: TimeLike | None = None,
    *,
    address_id: str | None = None,
    client: MinStroemClient | None = None,
) -> pd.DataFrame:
    """Get hourly prices: confirmed where they exist, forecast after them.

    | Property | Value |
    |---|---|
    | Endpoints | `GET .../prices` (hourly) and `GET .../forecast-prices` |
    | Unit | currency/kWh incl. VAT |
    | Columns | `total_price_per_kwh`, `variable_charges_per_kwh` (float), `is_forecast` (`boolean`) |
    | `attrs` | `address_id`, `currency`, `vat_included`, `fetched_at` |

    An hour is confirmed when its confirmed total price is not NaN. Confirmed
    prices win: forecast values are used only for hours after the last
    confirmed hour, and `is_forecast` is `True` there, `False` for confirmed
    hours and `<NA>` for an hour with neither (its prices NaN). With `end` the
    frame covers exactly `[start, end)` hourly; without it, it runs to the last
    forecast hour (to local midnight two days ahead when the forecast is
    empty). Confirmed prices are never requested beyond local midnight two days
    after today. A computed end at or before `start` gives an empty frame.

    Args:
        start: First moment of the period. A date means local midnight; a
            naive value is read as Danish local time.
        end: First moment after the period (exclusive), or `None` to run to the
            end of the forecast.
        address_id: The address (a UUID). `None` uses the account's only
            address, and refuses one without the `forecastPrices` capability.
        client: A client to fetch with. `None` creates one and closes it before
            returning; a passed client is left open.

    Returns:
        The frame described above, indexed like `get_prices`.

    Raises:
        ValueError: If a bound is not on the hour, `end <= start`, `address_id`
            is not a UUID, or it is `None` and the account has no address or
            several.
        MissingApiKeyError: If there is no API key.
        MinStroemError: If the API refuses a request, a payload is unexpected,
            the address lacks the forecast capability, or the two sources
            disagree on currency or VAT.
        RuntimeError: If a passed `client` is closed.
    """
    first, resolved_end = resolve_period(start, end, resolution=_HOUR)
    stop = None if end is None else resolved_end
    wanted = address_id if address_id is None else _check_address_id(address_id)
    cap = _local_midnight(2)

    async def work(
        active: MinStroemClient,
    ) -> tuple[str, _Forecast, pd.Timestamp, _Fetched | None]:
        address = await _resolve_address(active, wanted, "forecastPrices")
        forecast = await _fetch_forecast(active, address)
        if stop is not None:
            frame_end = stop
        elif len(forecast.hours):
            frame_end = forecast.hours.max() + pd.Timedelta(_HOUR)
        else:
            frame_end = cap
        confirmed_end = min(frame_end, cap)
        confirmed: _Fetched | None = None
        if frame_end > first and confirmed_end > first:
            confirmed = await _fetch_windows(
                active, _PRICES, address, "hour", first, confirmed_end
            )
        return address, forecast, frame_end, confirmed

    with _client_scope(client) as active:
        address, forecast, frame_end, confirmed = active.run(lambda: work(active))
    if confirmed is not None and (confirmed.currency, confirmed.vat_included) != (
        forecast.currency,
        forecast.vat_included,
    ):
        raise MinStroemError(
            "prices and forecast disagree on currency or VAT "
            f"({confirmed.currency}/{confirmed.vat_included} against "
            f"{forecast.currency}/{forecast.vat_included})"
        )
    frame = _stitch(first, frame_end, forecast, confirmed)
    frame.attrs.update(
        address_id=address,
        currency=forecast.currency,
        vat_included=forecast.vat_included,
        fetched_at=forecast.fetched_at,
    )
    return frame


def _local_midnight(days_ahead: int) -> pd.Timestamp:
    """Local midnight `days_ahead` days after today's Danish date."""
    today = pd.Timestamp.now(tz=DANISH_TZ).tz_localize(None).normalize()
    return (today + pd.DateOffset(days=days_ahead)).tz_localize(DANISH_TZ).as_unit("ns")


def _empty_prices(*, with_flag: bool) -> pd.DataFrame:
    """The documented empty frame: price columns, empty tz-aware index."""
    index = _empty_index()
    columns = {
        name: pd.Series([], index=index, dtype="float64") for name in _PRICE_COLUMNS
    }
    if with_flag:
        columns["is_forecast"] = pd.Series(pd.array([], dtype="boolean"), index=index)
    return pd.DataFrame(columns, index=index)


async def _fetch_forecast(client: MinStroemClient, address: str) -> _Forecast:
    """Request and check the forecast, and build its hourly frame."""
    path = f"/me/addresses/{address}/forecast-prices"
    payload = await client.fetch_json(path)
    fetched_at = pd.Timestamp.now(tz=DANISH_TZ).as_unit("ns")
    if payload.get("resolution") != "hour":
        raise MinStroemError(
            f"{path}: expected resolution 'hour', got {payload.get('resolution')!r}"
        )
    parsed = _parse(_FORECAST, payload, path)
    for position, row in enumerate(parsed.rows):
        utc = row.start.tz_convert("UTC")
        if utc != utc.floor("h") or row.end - row.start != pd.Timedelta(_HOUR):
            raise MinStroemError(
                f"{path}: row {position} is not one whole hour "
                f"({row.start.isoformat()} to {row.end.isoformat()})"
            )
    if not parsed.rows:
        return _Forecast(
            _empty_prices(with_flag=False),
            _empty_index(),
            parsed.currency,
            parsed.vat_included,
            fetched_at,
        )
    starts = pd.DatetimeIndex([row.start for row in parsed.rows]).as_unit("ns")
    index = period_index(starts.min(), starts.max() + pd.Timedelta(_HOUR), _HOUR)
    frame = _build(_FIELDS, parsed.rows, index)
    return _Forecast(
        frame, starts.sort_values(), parsed.currency, parsed.vat_included, fetched_at
    )


def _stitch(
    first: pd.Timestamp,
    frame_end: pd.Timestamp,
    forecast: _Forecast,
    confirmed: _Fetched | None,
) -> pd.DataFrame:
    """Combine confirmed and forecast hours over `[first, frame_end)`."""
    if frame_end <= first:
        return _empty_prices(with_flag=True)
    index = period_index(first, frame_end, _HOUR)
    forecast_values = forecast.frame.reindex(index)
    if confirmed is None:
        is_confirmed = pd.Series(False, index=index)
        confirmed_values = forecast_values.where(is_confirmed)
    else:
        confirmed_values = confirmed.frame.reindex(index)
        is_confirmed = confirmed_values["total_price_per_kwh"].notna()
    from_forecast = index.isin(forecast.hours)
    if is_confirmed.any():
        last_confirmed = index[is_confirmed.to_numpy()].max()
        from_forecast = from_forecast & (index > last_confirmed)
    columns: dict[str, pd.Series] = {}
    for name in _PRICE_COLUMNS:
        fallback = forecast_values[name].where(from_forecast)
        columns[name] = confirmed_values[name].where(is_confirmed, fallback)
    flags = [
        True if fc else (False if done else None)
        for fc, done in zip(from_forecast, is_confirmed, strict=True)
    ]
    columns["is_forecast"] = pd.Series(pd.array(flags, dtype="boolean"), index=index)
    return pd.DataFrame(columns, index=index)


def main() -> None:
    """Showcase this module's functionality (calls the live API)."""
    if not _showcase_has_key():
        return

    forecast = get_forecast_prices()

    print(f"forecast: {len(forecast)} hours, fetched {forecast.attrs['fetched_at']}")
    print(forecast.head())

    # Confirmed prices where they exist, forecast after the last one.
    start = pd.Timestamp.now(tz=DANISH_TZ).strftime("%Y-%m-%d")
    end = None  # exclusive; None runs to the last forecast hour

    combined = get_prices_with_forecast(start, end)

    print(
        f"combined: {len(combined)} hours, {int(combined['is_forecast'].sum())} forecast"
    )
    print(combined.head())

    # Quarter-hourly history for yesterday.
    start = (pd.Timestamp.now(tz=DANISH_TZ) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    resolution: PriceResolution = "fifteenMinutes"  # "hour", "fifteenMinutes"

    history = get_prices(start, resolution=resolution)

    print(f"history: {history.shape}")


if __name__ == "__main__":
    main()
