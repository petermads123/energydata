"""Private shared path of the Min Strøm period endpoints.

Holds the interval literals, period alignment, Copenhagen-calendar windows under
each endpoint's cap, address resolution, payload checks and the frame builder
that `usage.py`, `costs.py` and `prices.py` are thin layers over.
"""

import math
import uuid
from collections.abc import Awaitable, Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

import pandas as pd

from energydata.utils.chunking import _gather_ordered
from energydata.utils.frames import period_index
from energydata.utils.periods import DANISH_TZ, TimeLike, resolve_period
from energydata.utils.readers import JsonValue

from .client import MinStroemClient, MinStroemError, _showcase_has_key

type Aggregation = Literal["hour", "day", "month", "year"]
AGGREGATIONS: tuple[Aggregation, ...] = ("hour", "day", "month", "year")
type PriceResolution = Literal["hour", "fifteenMinutes"]
PRICE_RESOLUTIONS: tuple[PriceResolution, ...] = ("hour", "fifteenMinutes")

type _Family = Literal["usage", "costs", "prices"]
type _Unit = Literal["day", "month", "year"]

_HOUR = timedelta(hours=1)
_QUARTER = timedelta(minutes=15)
_CURRENCIES = ("DKK", "SEK", "NOK")
_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_OFFSETS = {
    "day": pd.DateOffset(days=1),
    "month": pd.DateOffset(months=1),
    "year": pd.DateOffset(years=1),
}

# (family, interval) -> (calendar unit of the boundaries, how many units a window spans)
_WINDOWS: dict[tuple[_Family, str], tuple[_Unit, int]] = {
    ("usage", "hour"): ("day", 365),
    ("usage", "day"): ("year", 4),
    ("usage", "month"): ("year", 4),
    ("usage", "year"): ("year", 4),
    ("costs", "hour"): ("day", 365),
    ("costs", "day"): ("day", 365),
    ("costs", "month"): ("month", 12),
    ("costs", "year"): ("year", 1),
    ("prices", "hour"): ("day", 365),
    ("prices", "fifteenMinutes"): ("day", 89),
}


@dataclass(frozen=True)
class _Field:
    """One response field and the frame column it becomes."""

    column: str
    source: str
    kind: Literal["float", "bool"]
    nullable: bool = False


@dataclass(frozen=True)
class _Endpoint:
    """What a function needs to know about its endpoint."""

    suffix: str
    capability: str
    family: _Family
    param: str
    intervals: tuple[str, ...]
    fields: tuple[_Field, ...]
    monetary: bool


@dataclass(frozen=True)
class _Row:
    """One checked row of a response: interval start, end and column values."""

    start: pd.Timestamp
    end: pd.Timestamp
    values: dict[str, float | bool]


@dataclass(frozen=True)
class _Parsed:
    """A checked response: its rows and, for money endpoints, currency and VAT."""

    rows: list[_Row]
    currency: str | None
    vat_included: bool | None


@dataclass(frozen=True)
class _Fetched:
    """The frame of a whole period and the currency and VAT flag it came with."""

    frame: pd.DataFrame
    currency: str | None
    vat_included: bool | None


def _check_interval(value: str, allowed: Sequence[str], name: str) -> None:
    """Raise unless `value` is one of `allowed`."""
    if value not in allowed:
        known = ", ".join(repr(item) for item in allowed)
        raise ValueError(f"{name} must be one of {known}, got {value!r}")


def _check_address_id(value: str) -> str:
    """Return the canonical form of a UUID address id, or raise `ValueError`."""
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"address_id must be a UUID, got {value!r}") from exc


def _resolve_series_period(
    start: TimeLike, end: TimeLike | None, interval: str
) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Resolve `[start, end)` and require both bounds on the interval."""
    resolution = _QUARTER if interval == "fifteenMinutes" else _HOUR
    first, last = resolve_period(start, end, resolution=resolution)
    if interval in ("day", "month", "year"):
        for name, bound in (("start", first), ("end", last)):
            local = bound.tz_localize(None)
            aligned = local == local.normalize()
            if interval in ("month", "year"):
                aligned = aligned and local.day == 1
            if interval == "year":
                aligned = aligned and local.month == 1
            if not aligned:
                needs = {
                    "day": "local midnight",
                    "month": "the first of a month at local midnight",
                    "year": "1 January at local midnight",
                }[interval]
                raise ValueError(
                    f"{name} must be {needs} for interval {interval!r}, "
                    f"got {bound.isoformat()!r}"
                )
    return first, last


def _interval_index(
    first: pd.Timestamp, last: pd.Timestamp, interval: str
) -> pd.DatetimeIndex:
    """Every interval start of `[first, last)` on the Danish calendar."""
    if interval == "hour":
        return period_index(first, last, _HOUR)
    if interval == "fifteenMinutes":
        return period_index(first, last, _QUARTER)
    naive = pd.date_range(
        first.tz_localize(None),
        last.tz_localize(None),
        freq=_OFFSETS[interval],
        inclusive="left",
    )
    return naive.tz_localize(DANISH_TZ).as_unit("ns").rename("time")


def _boundary(lo: pd.Timestamp, unit: _Unit, count: int) -> pd.Timestamp:
    """The `count`-th local `unit` boundary strictly after `lo`."""
    local = lo.tz_localize(None)
    if unit == "day":
        stop = local.normalize() + pd.DateOffset(days=count)
    elif unit == "month":
        stop = pd.Timestamp(local.year, local.month, 1) + pd.DateOffset(months=count)
    else:
        stop = pd.Timestamp(local.year, 1, 1) + pd.DateOffset(years=count)
    return stop.tz_localize(DANISH_TZ).as_unit("ns")


def _windows(
    first: pd.Timestamp, last: pd.Timestamp, family: _Family, interval: str
) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """Split `[first, last)` into windows under the endpoint's cap.

    Windows end on Copenhagen calendar boundaries and abut, so no interval is
    requested twice.
    """
    unit, count = _WINDOWS[(family, interval)]
    windows: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    lo = first
    while lo < last:
        hi = min(_boundary(lo, unit, count), last)
        windows.append((lo, hi))
        lo = hi
    return windows


@contextmanager
def _client_scope(client: MinStroemClient | None) -> Iterator[MinStroemClient]:
    """Yield the passed client, or a new one that is closed afterwards."""
    if client is not None:
        yield client
        return
    created = MinStroemClient()
    try:
        yield created
    finally:
        created.close()


def _text(item: dict[str, JsonValue], key: str, label: str) -> str:
    """A required string field of an address."""
    value = item.get(key)
    if not isinstance(value, str):
        raise MinStroemError(f"{label}: '{key}' is missing or not text")
    return value


def _nullable_text(item: dict[str, JsonValue], key: str, label: str) -> str | None:
    """A required string-or-null field of an address."""
    if key not in item or not (item[key] is None or isinstance(item[key], str)):
        raise MinStroemError(f"{label}: '{key}' is missing or not text or null")
    value = item[key]
    return value if isinstance(value, str) else None


_CAPABILITY_COLUMNS = (
    ("consumption", "consumption"),
    ("solar_export", "solarExport"),
    ("consumption_costs", "consumptionCosts"),
    ("solar_export_revenue", "solarExportRevenue"),
    ("prices", "prices"),
    ("forecast_prices", "forecastPrices"),
)
_ADDRESS_COLUMNS = (
    "id",
    "label",
    "formatted_address",
    "region",
    "time_zone",
    "grid_operator_name",
    "supplier_name",
    *(column for column, _ in _CAPABILITY_COLUMNS),
)


def _address_frame(payload: dict[str, JsonValue]) -> pd.DataFrame:
    """Check an `/me/addresses` payload and build the address frame."""
    data = payload.get("data")
    if not isinstance(data, list):
        raise MinStroemError("/me/addresses: the response has no 'data' list")
    columns: dict[str, list[object]] = {name: [] for name in _ADDRESS_COLUMNS}
    for position, item in enumerate(data):
        label = f"/me/addresses: address {position}"
        if not isinstance(item, dict):
            raise MinStroemError(f"{label} is not a JSON object")
        caps = item.get("capabilities")
        if not isinstance(caps, dict):
            raise MinStroemError(f"{label}: 'capabilities' is missing or not an object")
        columns["id"].append(_text(item, "id", label))
        columns["label"].append(_text(item, "label", label))
        columns["formatted_address"].append(_text(item, "formattedAddress", label))
        columns["region"].append(_nullable_text(item, "region", label))
        columns["time_zone"].append(_text(item, "timeZone", label))
        columns["grid_operator_name"].append(
            _nullable_text(item, "gridOperatorName", label)
        )
        columns["supplier_name"].append(_nullable_text(item, "supplierName", label))
        for column, source in _CAPABILITY_COLUMNS:
            flag = caps.get(source)
            if not isinstance(flag, bool):
                raise MinStroemError(f"{label}: capability '{source}' is not a boolean")
            columns[column].append(flag)
    flags = {column for column, _ in _CAPABILITY_COLUMNS}
    return pd.DataFrame(
        {
            name: pd.Series(values, dtype=bool if name in flags else object)
            for name, values in columns.items()
        }
    )


async def _addresses(client: MinStroemClient) -> pd.DataFrame:
    """Fetch and check the account's addresses."""
    return _address_frame(await client.fetch_json("/me/addresses"))


async def _resolve_address(
    client: MinStroemClient, address_id: str | None, capability: str
) -> str:
    """The address to use: the given id, else the account's only address.

    With no id the addresses are looked up, and the address's `capability` flag
    is checked before the endpoint is requested.
    """
    if address_id is not None:
        return _check_address_id(address_id)
    frame = await _addresses(client)
    if frame.empty:
        raise ValueError("no addresses on this account")
    if len(frame) > 1:
        listing = ", ".join(
            f"{row.id} ({row.label})" for row in frame.itertuples(index=False)
        )
        raise ValueError(
            f"the account has {len(frame)} addresses, pass address_id= one of: "
            f"{listing}"
        )
    column = next(name for name, source in _CAPABILITY_COLUMNS if source == capability)
    if not bool(frame[column].iloc[0]):
        raise MinStroemError(f"the address has no {capability!r} capability")
    return str(frame["id"].iloc[0])


def _timestamp(value: object, what: str) -> pd.Timestamp:
    """Parse a response instant to Copenhagen time, or raise `MinStroemError`."""
    if not isinstance(value, str):
        raise MinStroemError(f"{what} is missing or not text")
    try:
        stamp = pd.Timestamp(value)
    except ValueError as exc:
        raise MinStroemError(f"{what} is not a timestamp: {value!r}") from exc
    if stamp.tzinfo is None:
        raise MinStroemError(f"{what} has no UTC offset: {value!r}")
    return stamp.tz_convert(DANISH_TZ).as_unit("ns")


def _number(value: object, field: _Field, what: str) -> float:
    """A response number; null is NaN where the field allows it."""
    if value is None and field.nullable:
        return math.nan
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise MinStroemError(f"{what}: '{field.source}' is not a number, got {value!r}")
    return float(value)


def _parse(endpoint: _Endpoint, payload: dict[str, JsonValue], label: str) -> _Parsed:
    """Check a series payload against what the API promises."""
    data = payload.get("data")
    if not isinstance(data, list):
        raise MinStroemError(f"{label}: the response has no 'data' list")
    currency: str | None = None
    vat_included: bool | None = None
    if endpoint.monetary:
        raw_currency, raw_vat = payload.get("currency"), payload.get("vatIncluded")
        if raw_currency not in _CURRENCIES or not isinstance(raw_currency, str):
            raise MinStroemError(f"{label}: unknown currency {raw_currency!r}")
        if not isinstance(raw_vat, bool):
            raise MinStroemError(f"{label}: 'vatIncluded' is not a boolean")
        currency, vat_included = raw_currency, raw_vat
    rows: list[_Row] = []
    for position, item in enumerate(data):
        what = f"{label}: row {position}"
        if not isinstance(item, dict):
            raise MinStroemError(f"{what} is not a JSON object")
        start = _timestamp(item.get("start"), f"{what} 'start'")
        end = _timestamp(item.get("end"), f"{what} 'end'")
        values: dict[str, float | bool] = {}
        for field in endpoint.fields:
            if field.source not in item:
                raise MinStroemError(f"{what}: '{field.source}' is missing")
            value = item[field.source]
            if field.kind == "bool":
                if not isinstance(value, bool):
                    raise MinStroemError(f"{what}: '{field.source}' is not a boolean")
                values[field.column] = value
            else:
                values[field.column] = _number(value, field, what)
        rows.append(_Row(start, end, values))
    return _Parsed(rows, currency, vat_included)


def _build(
    fields: Sequence[_Field], rows: Sequence[_Row], index: pd.DatetimeIndex
) -> pd.DataFrame:
    """Place `rows` on `index`; every other interval is NaN, never filled."""
    size = len(index)
    slots: list[int] = []
    if rows:
        starts = pd.DatetimeIndex([row.start for row in rows]).as_unit("ns")
        slots = [int(slot) for slot in index.get_indexer(starts)]
    seen: set[int] = set()
    for row, slot in zip(rows, slots, strict=True):
        if slot < 0:
            raise MinStroemError(
                f"a row starts at {row.start.isoformat()!r}, which is not an "
                "interval of the requested period"
            )
        if slot in seen:
            raise MinStroemError(f"two rows start at {row.start.isoformat()!r}")
        seen.add(slot)
    columns: dict[str, pd.Series] = {}
    for field in fields:
        if field.kind == "bool":
            flags: list[bool | None] = [None] * size
            for row, slot in zip(rows, slots, strict=True):
                flags[slot] = bool(row.values[field.column])
            columns[field.column] = pd.Series(
                pd.array(flags, dtype="boolean"), index=index
            )
        else:
            numbers = [math.nan] * size
            for row, slot in zip(rows, slots, strict=True):
                numbers[slot] = float(row.values[field.column])
            columns[field.column] = pd.Series(numbers, index=index, dtype="float64")
    return pd.DataFrame(columns, index=index)


def _utc_text(stamp: pd.Timestamp) -> str:
    """An instant as the API's RFC 3339 UTC text."""
    return stamp.tz_convert("UTC").strftime(_TIME_FORMAT)


def _empty_index() -> pd.DatetimeIndex:
    """An empty tz-aware Copenhagen index named `"time"`."""
    return pd.DatetimeIndex([], tz=DANISH_TZ, name="time").as_unit("ns")


async def _fetch_windows(
    client: MinStroemClient,
    endpoint: _Endpoint,
    address: str,
    interval: str,
    first: pd.Timestamp,
    last: pd.Timestamp,
) -> _Fetched:
    """Request `[first, last)` window by window, concurrently, and build the frame."""
    windows = _windows(first, last, endpoint.family, interval)
    path = f"/me/addresses/{address}/{endpoint.suffix}"

    def bind(lo: pd.Timestamp, hi: pd.Timestamp) -> Callable[[], Awaitable[_Parsed]]:
        async def call() -> _Parsed:
            params = {"from": _utc_text(lo), "to": _utc_text(hi)}
            if endpoint.param:
                params[endpoint.param] = interval
            payload = await client.fetch_json(path, params)
            return _parse(endpoint, payload, path)

        return call

    parsed = await _gather_ordered([bind(lo, hi) for lo, hi in windows])
    currency, vat_included = parsed[0].currency, parsed[0].vat_included
    for part in parsed[1:]:
        if (part.currency, part.vat_included) != (currency, vat_included):
            raise MinStroemError(
                f"{path}: windows disagree on currency or VAT "
                f"({currency}/{vat_included} against "
                f"{part.currency}/{part.vat_included})"
            )
    rows = [
        row
        for (lo, hi), part in zip(windows, parsed, strict=True)
        for row in part.rows
        if lo <= row.start < hi
    ]
    frame = _build(endpoint.fields, rows, _interval_index(first, last, interval))
    return _Fetched(frame, currency, vat_included)


def _fetch_series(
    endpoint: _Endpoint,
    start: TimeLike,
    end: TimeLike | None,
    interval: str,
    address_id: str | None,
    client: MinStroemClient | None,
) -> pd.DataFrame:
    """The shared body of every period endpoint function."""
    _check_interval(interval, endpoint.intervals, endpoint.param)
    first, last = _resolve_series_period(start, end, interval)
    wanted = address_id if address_id is None else _check_address_id(address_id)

    async def work(
        active: MinStroemClient,
    ) -> tuple[str, _Fetched]:
        address = await _resolve_address(active, wanted, endpoint.capability)
        return address, await _fetch_windows(
            active, endpoint, address, interval, first, last
        )

    with _client_scope(client) as active:
        address, fetched = active.run(lambda: work(active))
    frame = fetched.frame
    frame.attrs["address_id"] = address
    frame.attrs[endpoint.param] = interval
    if endpoint.monetary:
        frame.attrs["currency"] = fetched.currency
        frame.attrs["vat_included"] = fetched.vat_included
    return frame


def main() -> None:
    """Showcase alignment and calendar windows (offline)."""
    start = "2025-03-01"
    end = "2026-04-01"
    interval = "hour"  # "hour", "day", "month", "year"
    family: _Family = "costs"  # "usage", "costs", "prices"

    first, last = _resolve_series_period(start, end, interval)
    windows = _windows(first, last, family, interval)

    print(f"{family} per {interval}: {len(windows)} windows over {start} to {end}")
    for lo, hi in windows:
        print(f"  {lo.isoformat()} -> {hi.isoformat()}")

    # A month interval must start on the first of a month.
    start = "2026-03-15"
    end = "2026-05-01"
    interval = "month"

    try:
        _resolve_series_period(start, end, interval)
    except ValueError as exc:
        print(f"refused: {exc}")

    if _showcase_has_key():
        with MinStroemClient() as client:
            frame = client.run(lambda: _addresses(client))
        print(f"{len(frame)} address(es), regions {sorted(frame['region'].dropna())}")


if __name__ == "__main__":
    main()
