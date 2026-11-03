"""Measured consumption and production per metering point from Eloverblik."""

import math
from collections.abc import Awaitable, Callable, Sequence
from datetime import date, timedelta
from typing import Literal

import pandas as pd

from energydata.utils.chunking import gather_ordered
from energydata.utils.frames import period_index
from energydata.utils.periods import DANISH_TZ, TimeLike, resolve_period
from energydata.utils.readers import JsonValue

from ._common import (
    _carrying,
    _client_scope,
    _post_points,
    _resolve_ids,
    _text,
    _unpack,
)
from .client import EloverblikClient, EloverblikError, _showcase_has_token

type Aggregation = Literal["quarter", "hour", "day", "month", "year"]
AGGREGATIONS: tuple[Aggregation, ...] = ("quarter", "hour", "day", "month", "year")

_API_NAME = {
    "quarter": "Quarter",
    "hour": "Hour",
    "day": "Day",
    "month": "Month",
    "year": "Year",
}
_RESOLUTION = {
    "quarter": "PT15M",
    "hour": "PT1H",
    "day": "PT1D",  # the label the real API sends; ISO 8601 `P1D` is accepted too
    "month": "P1M",
    "year": "P1Y",
}
_ALSO_ACCEPTED = {"day": frozenset({"P1D"})}
_OFFSETS = {
    "day": pd.DateOffset(days=1),
    "month": pd.DateOffset(months=1),
    "year": pd.DateOffset(years=1),
}
_QUARTER = timedelta(minutes=15)
_HOUR = timedelta(hours=1)
_MAX_DAYS = 730
_HISTORY_YEARS = 5
# 30015 no data, 30018 period outside the point's data period, 30010 period not
# covered by the authorization, 30016 relation expired: NaN for that window.
_TOLERATED = frozenset({30015, 30018, 30010, 30016})

type _Value = tuple[float, str | None]
type _Collected = dict[str, dict[pd.Timestamp, _Value]]


def _check_aggregation(aggregation: str) -> None:
    """Raise unless `aggregation` is one of `AGGREGATIONS`."""
    if aggregation not in AGGREGATIONS:
        known = ", ".join(repr(item) for item in AGGREGATIONS)
        raise ValueError(f"aggregation must be one of {known}, got {aggregation!r}")


def _require_aligned(name: str, bound: pd.Timestamp, aggregation: str) -> None:
    """Raise unless `bound` is on the start of an `aggregation` interval."""
    if aggregation in ("quarter", "hour"):
        return  # resolve_period has checked the clock alignment
    local = bound.tz_localize(None)
    aligned = local == local.normalize()
    if aggregation in ("month", "year"):
        aligned = aligned and local.day == 1
    if aggregation == "year":
        aligned = aligned and local.month == 1
    if not aligned:
        needs = {
            "day": "local midnight",
            "month": "the first of a month at local midnight",
            "year": "1 January at local midnight",
        }[aggregation]
        raise ValueError(
            f"{name} must be {needs} for aggregation {aggregation!r}, "
            f"got {bound.isoformat()!r}"
        )


def _resolve_bounds(
    start: TimeLike, end: TimeLike | None, aggregation: str
) -> tuple[pd.Timestamp, pd.Timestamp | None]:
    """Resolve `[start, end)` and require both bounds on the aggregation.

    Without `end` the period is open: `start` only is checked, and the one-slot
    `end` that `resolve_period` derives is discarded.
    """
    resolution = _QUARTER if aggregation == "quarter" else _HOUR
    first, last = resolve_period(start, end, resolution=resolution)
    _require_aligned("start", first, aggregation)
    if end is None:
        return first, None
    _require_aligned("end", last, aggregation)
    return first, last


def _request_dates(
    first: pd.Timestamp, last: pd.Timestamp | None, today: date
) -> tuple[date, date] | None:
    """The Danish dates `[dateFrom, dateTo)` to request, or `None` for none.

    `dateFrom` is clamped to 1 January five years back and `dateTo` to
    tomorrow; a period wholly outside that range needs no request.
    """
    tomorrow = today + timedelta(days=1)
    horizon = date(today.year - _HISTORY_YEARS, 1, 1)
    date_from = first.date()
    if last is None:
        date_to = tomorrow
    else:
        date_to = last.date() + (
            timedelta(0) if last == last.normalize() else timedelta(days=1)
        )
    low, high = max(date_from, horizon), min(date_to, tomorrow)
    return (low, high) if low < high else None


def _next_boundary(day: date, aggregation: str) -> date:
    """The end of the largest request window that starts at `day`."""
    if aggregation == "month":
        index = day.month - 1 + 12
        return date(day.year + index // 12, index % 12 + 1, 1)
    if aggregation == "year":
        return date(day.year + 1, 1, 1)
    return day + timedelta(days=_MAX_DAYS)


def _windows(low: date, high: date, aggregation: str) -> list[tuple[date, date]]:
    """Split `[low, high)` into abutting request windows on Danish dates.

    At most 730 days for quarter, hour and day data, 12 months for month data and
    one year for year data; no date is requested twice.
    """
    windows: list[tuple[date, date]] = []
    current = low
    while current < high:
        stop = min(_next_boundary(current, aggregation), high)
        windows.append((current, stop))
        current = stop
    return windows


def _interval_start(begin: pd.Timestamp, aggregation: str, offset: int) -> pd.Timestamp:
    """The start of the `offset`th interval after a period starting at `begin`."""
    if aggregation == "quarter":
        return (begin + pd.Timedelta(minutes=15 * offset)).tz_convert(DANISH_TZ)
    if aggregation == "hour":
        return (begin + pd.Timedelta(hours=offset)).tz_convert(DANISH_TZ)
    local = begin.tz_convert(DANISH_TZ).tz_localize(None)
    moved = local + _OFFSETS[aggregation] * offset
    return moved.tz_localize(DANISH_TZ).as_unit("ns")


def _interval_index(
    first: pd.Timestamp, last: pd.Timestamp, aggregation: str
) -> pd.DatetimeIndex:
    """Every interval start of `[first, last)` on the Danish calendar."""
    if aggregation == "quarter":
        return period_index(first, last, _QUARTER)
    if aggregation == "hour":
        return period_index(first, last, _HOUR)
    naive = pd.date_range(
        first.tz_localize(None),
        last.tz_localize(None),
        freq=_OFFSETS[aggregation],
        inclusive="left",
    )
    return naive.tz_localize(DANISH_TZ).as_unit("ns").rename("time")


def _empty_index() -> pd.DatetimeIndex:
    """An empty tz-aware Copenhagen index named `"time"`."""
    return pd.DatetimeIndex([], tz=DANISH_TZ, name="time").as_unit("ns")


def _instant(value: object, what: str) -> pd.Timestamp:
    """Parse a UTC instant from the response, or raise `EloverblikError`."""
    if not isinstance(value, str):
        raise EloverblikError(f"{what} is missing or not text")
    try:
        stamp = pd.Timestamp(value)
    except (ValueError, OverflowError) as exc:
        raise EloverblikError(f"{what} is not a usable timestamp: {value!r}") from exc
    if pd.isna(stamp):
        raise EloverblikError(f"{what} is not a usable timestamp: {value!r}")
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    try:
        return stamp.tz_convert("UTC").as_unit("ns")
    except (ValueError, OverflowError) as exc:
        raise EloverblikError(f"{what} is out of range: {value!r}") from exc


def _quantity(value: JsonValue, what: str) -> float:
    """A point's quantity; null or absent is NaN."""
    if value is None:
        return math.nan
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        raise EloverblikError(f"{what}: quantity is not a number, got {value!r}")
    try:
        number = float(value)
    except (ValueError, OverflowError) as exc:
        raise EloverblikError(
            f"{what}: quantity is not a number, got {value!r}"
        ) from exc
    if not math.isfinite(number):
        raise EloverblikError(f"{what}: quantity is not finite, got {value!r}")
    return number


def _same(a: _Value, b: _Value) -> bool:
    """Whether two values are equal, NaN equalling NaN."""
    return a[1] == b[1] and (a[0] == b[0] or (math.isnan(a[0]) and math.isnan(b[0])))


def _collect(
    result: dict[str, JsonValue],
    path: str,
    aggregation: str,
    requested: set[str],
    into: _Collected,
) -> None:
    """Check one point's result and add its rows to `into`.

    Raises:
        EloverblikError: For a failure that is not tolerated, a series for a point
            that was not requested, a unit other than kWh, a resolution other than
            the one asked, a malformed position or quantity, or two different
            values for the same interval.
    """
    if not _unpack(result, path, _TOLERATED):
        return
    document = result.get("MyEnergyData_MarketDocument")
    if document is None:
        return
    if not isinstance(document, dict):
        raise EloverblikError(f"{path}: the market document is not a JSON object")
    series_list = document.get("TimeSeries")
    if series_list is None:
        return
    if not isinstance(series_list, list):
        raise EloverblikError(f"{path}: 'TimeSeries' is not a list")
    expected = _RESOLUTION[aggregation]
    accepted = _ALSO_ACCEPTED.get(aggregation, frozenset())
    for series in series_list:
        if not isinstance(series, dict):
            raise EloverblikError(f"{path}: a series is not a JSON object")
        point_id = _text(series, "mRID", f"{path}: series")
        if point_id not in requested:
            raise EloverblikError(
                f"{path}: a series for metering point {point_id}, which was not "
                "requested",
                metering_point_id=point_id,
            )
        if point_id != result.get("id"):
            raise EloverblikError(
                f"{path}: the result for metering point {result.get('id')!r} holds "
                f"a series for metering point {point_id}",
                metering_point_id=point_id,
            )
        try:
            label = f"{path}: point {point_id}"
            unit = series.get("measurement_Unit.name")
            if not isinstance(unit, str) or unit.strip().upper() != "KWH":
                raise EloverblikError(
                    f"{label} reports unit {unit!r}, expected KWH",
                    metering_point_id=point_id,
                )
            periods = series.get("Period")
            if periods is None:
                continue
            if not isinstance(periods, list):
                raise EloverblikError(f"{label}: 'Period' is not a list")
            rows = into.setdefault(point_id, {})
            for period in periods:
                if not isinstance(period, dict):
                    raise EloverblikError(f"{label}: a period is not a JSON object")
                got = period.get("resolution")
                if got != expected and got not in accepted:
                    raise EloverblikError(
                        f"{label} returned resolution {got!r} for aggregation "
                        f"{aggregation!r}, expected {expected!r}",
                        metering_point_id=point_id,
                    )
                interval = period.get("timeInterval")
                if not isinstance(interval, dict):
                    raise EloverblikError(f"{label}: a period has no 'timeInterval'")
                begin = _instant(
                    interval.get("start"), f"{label}: timeInterval 'start'"
                )
                points = period.get("Point")
                if points is None:
                    continue
                if not isinstance(points, list):
                    raise EloverblikError(f"{label}: 'Point' is not a list")
                for point in points:
                    if not isinstance(point, dict):
                        raise EloverblikError(f"{label}: a point is not a JSON object")
                    position = _position(point.get("position"), label)
                    quality = point.get("out_Quantity.quality")
                    if quality is not None and not isinstance(quality, str):
                        raise EloverblikError(f"{label}: quality is not text")
                    value = (
                        _quantity(
                            point.get("out_Quantity.quantity"),
                            f"{label} point {position}",
                        ),
                        quality,
                    )
                    try:
                        start = _interval_start(begin, aggregation, position - 1)
                    except (ValueError, OverflowError) as exc:
                        raise EloverblikError(
                            f"{label}: position {position} is out of range"
                        ) from exc
                    known = rows.get(start)
                    if known is not None and not _same(known, value):
                        raise EloverblikError(
                            f"{label} has two different values for the interval "
                            f"starting {start.isoformat()}",
                            metering_point_id=point_id,
                        )
                    rows[start] = value
        except EloverblikError as exc:
            if exc.metering_point_id is None:
                exc.metering_point_id = point_id
            raise


def _position(value: JsonValue, label: str) -> int:
    """A point's 1-based position."""
    try:
        if isinstance(value, bool) or not isinstance(value, str | int):
            raise ValueError(value)
        position = int(value)
    except ValueError as exc:
        raise EloverblikError(
            f"{label}: position is not an integer, got {value!r}"
        ) from exc
    if position < 1:
        raise EloverblikError(f"{label}: position must be at least 1, got {position}")
    return position


def _frame(
    ids: Sequence[str],
    collected: _Collected,
    first: pd.Timestamp,
    last: pd.Timestamp | None,
    aggregation: str,
) -> pd.DataFrame:
    """Place the collected rows on the calendar index of `[first, last)`."""
    kept: _Collected = {}
    for point_id in ids:
        rows = collected.get(point_id, {})
        kept[point_id] = {
            start: value
            for start, value in rows.items()
            if start >= first and (last is None or start < last)
        }
    if last is None:
        starts = [start for rows in kept.values() for start in rows]
        if starts:
            stop = _interval_start(max(starts), aggregation, 1)
            index = _interval_index(first, stop, aggregation)
        else:
            index = _empty_index()
    else:
        index = _interval_index(first, last, aggregation)
    columns: dict[tuple[str, str], pd.Series] = {}
    for point_id in ids:
        rows = kept[point_id]
        energy = [math.nan] * len(index)
        quality: list[object] = [math.nan] * len(index)
        if rows:
            stamps = pd.DatetimeIndex(list(rows)).as_unit("ns")
            slots = index.get_indexer(stamps)
            for (start, (value, code)), slot in zip(rows.items(), slots, strict=True):
                if slot < 0:
                    raise EloverblikError(
                        f"point {point_id} has a value at {start.isoformat()}, which "
                        f"is not a {aggregation} interval of the requested period",
                        metering_point_id=point_id,
                    )
                energy[int(slot)] = value
                if code is not None:
                    quality[int(slot)] = code
        columns[(point_id, "energy_kwh")] = pd.Series(
            energy, index=index, dtype="float64"
        )
        columns[(point_id, "quality")] = pd.Series(quality, index=index, dtype=object)
    frame = pd.DataFrame(columns, index=index)
    frame.columns.names = ["metering_point_id", "field"]
    frame.attrs["aggregation"] = aggregation
    return frame


def get_meter_data(
    start: TimeLike,
    end: TimeLike | None = None,
    *,
    aggregation: Aggregation = "hour",
    metering_point_ids: str | Sequence[str] | None = None,
    client: EloverblikClient | None = None,
) -> pd.DataFrame:
    """Get measured energy per metering point.

    | Property | Value |
    |---|---|
    | Endpoint | `POST /meterdata/gettimeseries/{dateFrom}/{dateTo}/{aggregation}` |
    | Unit | kWh |
    | Aggregations | `quarter`, `hour`, `day`, `month`, `year` (Danish calendar) |
    | Index | `"time"`, `datetime64[ns, Europe/Copenhagen]`, interval starts |
    | Columns | two levels `(metering_point_id, field)`; fields `energy_kwh` (`float64`) and `quality` (the raw code, such as `A04`) |
    | `attrs` | `aggregation` |

    With `end` there is one row per interval of `[start, end)` on the Danish
    calendar (23 or 25 hours on a DST day); an interval the API returned nothing
    for is NaN in both fields, never zero. Without `end` the index runs from
    `start` to the end of the last interval any point returned (an empty frame
    when none did). Only the previous five years and the current one are
    available, so earlier intervals are NaN, and nothing after tomorrow is
    requested. The interval holding today may be incomplete: its value is a
    partial sum. Periods over 730 days are split into windows on Danish dates and
    points are requested at most 10 at a time; the result is the same as one
    unbounded call. A point with no data (`30015`), outside its data period
    (`30018`), not covered by the authorization (`30010`) or whose relation has
    expired (`30016`) is NaN for the window concerned.

    Args:
        start: First moment of the period. A date means local midnight; a
            naive value is read as Danish local time. It must lie on an interval
            of `aggregation`.
        end: First moment after the period (exclusive), also on an interval.
            `None` leaves the period open, to the last interval returned.
        aggregation: The interval: `"quarter"`, `"hour"`, `"day"`, `"month"` or
            `"year"`.
        metering_point_ids: One 18-digit id or a sequence of them. `None` means
            every linked point.
        client: A client to fetch with. `None` creates one and closes it before
            returning; a passed client is left open.

    Returns:
        The frame described above.

    Raises:
        ValueError: If `aggregation` is unknown, a bound is not on the
            aggregation, `start >= end`, an id is malformed or repeated, the id
            sequence is empty, or `metering_point_ids` is `None` and no point is
            linked.
        MissingTokenError: If there is no refresh token and a request is made.
        EloverblikError: If the API refuses a request, reports another failure
            for a point, returns a resolution or unit other than the one asked,
            or returns an unexpected payload.
        RetriesExhaustedError: If a network failure outlasted the retries.
        RuntimeError: If a passed `client` is closed.
    """
    _check_aggregation(aggregation)
    first, last = _resolve_bounds(start, end, aggregation)
    today = pd.Timestamp.now(DANISH_TZ).date()
    span = _request_dates(first, last, today)
    windows = [] if span is None else _windows(span[0], span[1], aggregation)
    name = _API_NAME[aggregation]

    async def work(
        active: EloverblikClient,
    ) -> tuple[list[str], list[list[dict[str, JsonValue]]]]:
        ids = await _resolve_ids(active, metering_point_ids)

        def bind(
            low: date, high: date
        ) -> Callable[[], Awaitable[list[dict[str, JsonValue]]]]:
            path = (
                f"/meterdata/gettimeseries/{low.isoformat()}/{high.isoformat()}/{name}"
            )
            return lambda: _post_points(active, path, ids)

        return ids, await gather_ordered([bind(low, high) for low, high in windows])

    with _client_scope(client) as active:
        ids, parts = active.run(lambda: work(active))
    collected: _Collected = {}
    requested = set(ids)
    for (low, high), results in zip(windows, parts, strict=True):
        path = f"/meterdata/gettimeseries/{low.isoformat()}/{high.isoformat()}/{name}"
        for result in results:
            with _carrying(result):
                _collect(result, path, aggregation, requested, collected)
    return _frame(ids, collected, first, last, aggregation)


def main() -> None:
    """Showcase period handling offline, and the live call with a token."""
    start = "2026-03-01"
    end = "2026-04-01"
    aggregation: Aggregation = "hour"  # "quarter", "hour", "day", "month", "year"

    first, last = _resolve_bounds(start, end, aggregation)
    span = _request_dates(first, last, date(2026, 11, 2))
    windows = [] if span is None else _windows(span[0], span[1], aggregation)

    print(f"{aggregation} data {start} to {end}: {len(windows)} request window(s)")

    # A month aggregation must start on the first of a month.
    start = "2026-03-15"
    end = "2026-05-01"
    aggregation = "month"

    try:
        _resolve_bounds(start, end, aggregation)
    except ValueError as exc:
        print(f"refused: {exc}")

    # Several years of hourly data are split into windows of 730 days.
    start = "2022-01-01"
    end = "2026-01-01"
    aggregation = "hour"

    first, last = _resolve_bounds(start, end, aggregation)
    span = _request_dates(first, last, date(2026, 11, 2))
    windows = [] if span is None else _windows(span[0], span[1], aggregation)

    print(f"{start} to {end}: {[(str(a), str(b)) for a, b in windows]}")

    if _showcase_has_token():
        start = "2026-10-25"
        end = "2026-10-26"
        aggregation = "hour"
        metering_point_ids = None  # None for every linked point

        frame = get_meter_data(
            start, end, aggregation=aggregation, metering_point_ids=metering_point_ids
        )

        print(frame.shape)
        print(frame.head())


if __name__ == "__main__":
    main()
