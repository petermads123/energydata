"""The shared path of the Min Strøm period endpoints, and the mock that backs it."""

import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal, NamedTuple

import httpx
import pandas as pd
import pytest
from conftest import (
    ADDRESS_ID,
    MinStroemService,
    minstroem_address,
    minstroem_request_id,
)

from energydata.minstroem import (
    API_KEY_ENV,
    BASE_URL,
    Aggregation,
    MinStroemClient,
    MinStroemError,
    MissingApiKeyError,
    _series,
    get_addresses,
    get_consumption,
    get_consumption_costs,
    get_forecast_prices,
    get_prices,
    get_prices_with_forecast,
    get_solar_export,
    get_solar_export_revenue,
    prices,
)
from energydata.minstroem._series import (
    _check_address_id,
    _interval_index,
    _resolve_series_period,
    _windows,
)
from energydata.utils import TimeLike

CPH = "Europe/Copenhagen"
OTHER = "2b4bf7a8-b5d0-45ab-8d42-3a5688bba167"
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

type Series = Callable[..., pd.DataFrame]
type Responder = Callable[[httpx.Request], httpx.Response]
type Family = Literal["usage", "costs", "prices"]


class SeriesCase(NamedTuple):
    """One period endpoint: its function, path suffix and interval parameter."""

    fn: Series
    suffix: str
    kw: str


SERIES = [
    pytest.param(
        SeriesCase(get_consumption, "consumption", "aggregation"), id="consumption"
    ),
    pytest.param(
        SeriesCase(get_solar_export, "solar-export", "aggregation"), id="solar-export"
    ),
    pytest.param(
        SeriesCase(get_consumption_costs, "consumption-costs", "aggregation"),
        id="costs",
    ),
    pytest.param(
        SeriesCase(get_solar_export_revenue, "solar-export-revenue", "aggregation"),
        id="revenue",
    ),
    pytest.param(SeriesCase(get_prices, "prices", "resolution"), id="prices"),
]


@pytest.fixture(autouse=True)
def _frozen_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin 'local midnight two days ahead' to 2026-11-04 for the combined series."""
    monkeypatch.setattr(
        prices,
        "_local_midnight",
        lambda days: (
            (pd.Timestamp("2026-11-02") + pd.DateOffset(days=days))
            .tz_localize(CPH)
            .as_unit("ns")
        ),
    )


def _utc(local: str) -> str:
    """The API text of a Copenhagen-local instant."""
    return pd.Timestamp(local, tz=CPH).tz_convert("UTC").strftime(TIME_FORMAT)


def _lacking(capability: str) -> MinStroemService:
    """A mock whose only address lacks `capability` (a camelCase API name)."""
    address = minstroem_address()
    address["capabilities"][capability] = False
    return MinStroemService([address])


def _stamp(text: str) -> datetime:
    return datetime.strptime(text, TIME_FORMAT).replace(tzinfo=UTC)


def _usage_row(
    start: str, energy: float | None = 1.5, *, estimate: bool = False, hours: int = 1
) -> dict[str, Any]:
    """A consumption row starting at the API text `start`."""
    begin = _stamp(start)
    return {
        "start": start,
        "end": (begin + timedelta(hours=hours)).strftime(TIME_FORMAT),
        "energyKWh": energy,
        "isEstimate": estimate,
    }


def _serve(service: MinStroemService, suffix: str, body: object) -> None:
    """Make the mock answer `suffix` with a 200 and `body`."""
    service.overrides[suffix] = lambda _request: httpx.Response(200, json=body)


def _serve_raw(service: MinStroemService, suffix: str, content: bytes) -> None:
    service.overrides[suffix] = lambda _request: httpx.Response(
        200, content=content, headers={"Content-Type": "application/json"}
    )


def _expected_index(start: str, end: str, interval: str) -> pd.DatetimeIndex:
    """Every interval start of `[start, end)`, built without the package."""
    if interval in ("hour", "fifteenMinutes"):
        lo = pd.Timestamp(start, tz=CPH).tz_convert("UTC")
        hi = pd.Timestamp(end, tz=CPH).tz_convert("UTC")
        freq = "h" if interval == "hour" else "15min"
        index = pd.date_range(lo, hi, freq=freq, inclusive="left").tz_convert(CPH)
    else:
        freq = {"day": "D", "month": "MS", "year": "YS"}[interval]
        index = pd.date_range(start, end, freq=freq, inclusive="left").tz_localize(CPH)
    return index.as_unit("ns").rename("time")


def _assert_same_index(actual: pd.Index, expected: pd.DatetimeIndex) -> None:
    """Same instants, dtype and name; the `freq` attribute is not part of it."""
    assert actual.equals(expected)
    assert actual.dtype == expected.dtype
    assert actual.name == expected.name


def _energy(index: pd.DatetimeIndex) -> pd.Series:
    """What the mock generates for `energy_kwh` at each interval start."""
    return pd.Series(
        [(t.timestamp() / 3600) % 1000 for t in index], index=index, dtype="float64"
    )


# --- the five series functions: intervals and periods ---------------------


@pytest.mark.parametrize("case", SERIES)
@pytest.mark.parametrize("bad", ["Hour", "", "week", "fifteen_minutes", None, ["hour"]])
def test_an_unknown_interval_is_refused_before_a_key_or_a_request(
    monkeypatch: pytest.MonkeyPatch, case: SeriesCase, bad: object
) -> None:
    fn = case.fn
    kw = case.kw
    monkeypatch.delenv(API_KEY_ENV, raising=False)

    with pytest.raises(ValueError, match=f"{kw} must be one of") as caught:
        fn("2026-11-02", **{kw: bad})

    assert not isinstance(caught.value, MissingApiKeyError)


@pytest.mark.parametrize(
    ("fn", "kw", "bad"),
    [
        (get_consumption, "aggregation", "fifteenMinutes"),
        (get_consumption_costs, "aggregation", "fifteenMinutes"),
        (get_prices, "resolution", "day"),
        (get_prices, "resolution", "FIFTEENMINUTES"),
    ],
)
def test_the_error_for_an_unknown_interval_lists_the_allowed_values(
    fn: Series, kw: str, bad: str
) -> None:
    allowed = (
        "'hour', 'day', 'month', 'year'"
        if kw == "aggregation"
        else ("'hour', 'fifteenMinutes'")
    )

    with pytest.raises(ValueError, match=allowed) as caught:
        fn("2026-11-02", **{kw: bad})

    assert repr(bad) in str(caught.value)


@pytest.mark.parametrize("case", SERIES)
def test_start_not_before_end_is_a_value_error_without_a_request(
    minstroem_service: MinStroemService, case: SeriesCase
) -> None:
    fn = case.fn
    with minstroem_service.client() as c:
        for start, end in [("2026-03-02", "2026-03-01"), ("2026-03-02", "2026-03-02")]:
            with pytest.raises(ValueError, match="end"):
                fn(start, end, client=c)

    assert minstroem_service.requests == []


@pytest.mark.parametrize(
    ("start", "end", "interval", "bound", "got"),
    [
        ("2026-03-15", "2026-05-01", "month", "start", "2026-03-15T00:00:00+01:00"),
        ("2026-03-01", "2026-04-15", "month", "end", "2026-04-15T00:00:00+02:00"),
        ("2026-02-01", "2027-01-01", "year", "start", "2026-02-01T00:00:00+01:00"),
        ("2026-01-01", "2027-02-01", "year", "end", "2027-02-01T00:00:00+01:00"),
        ("2026-03-01T06:00", "2026-03-02", "day", "start", "2026-03-01T06:00:00+01:00"),
        ("2026-03-01", "2026-03-02T06:00", "day", "end", "2026-03-02T06:00:00+01:00"),
        # A fixed offset: 00:00+01:00 on 30 March is 01:00 local, after the change.
        (
            "2026-03-29T00:00+01:00",
            "2026-03-30T00:00+01:00",
            "day",
            "end",
            "2026-03-30T01:00:00+02:00",
        ),
        # A UTC midnight is 01:00 in Copenhagen.
        (
            "2026-03-01T00:00Z",
            "2026-03-02",
            "day",
            "start",
            "2026-03-01T01:00:00+01:00",
        ),
        # A lone date with a month interval has a derived end one day later.
        ("2026-03-01", None, "month", "end", "2026-03-02T00:00:00+01:00"),
        ("2026-01-01", None, "year", "end", "2026-01-02T00:00:00+01:00"),
        # A lone timestamp ends one hour later, which no day interval is.
        ("2026-03-01T00:00", None, "day", "end", "2026-03-01T01:00:00+01:00"),
        (
            datetime(2026, 3, 29),  # noqa: DTZ001 - a naive datetime is the input
            None,
            "day",
            "end",
            "2026-03-29T01:00:00+01:00",
        ),
    ],
)
def test_a_bound_off_its_calendar_interval_is_a_value_error_naming_it(
    minstroem_service: MinStroemService,
    start: TimeLike,
    end: TimeLike | None,
    interval: Aggregation,
    bound: str,
    got: str,
) -> None:
    with minstroem_service.client() as c:
        with pytest.raises(ValueError) as caught:
            get_consumption(start, end, aggregation=interval, client=c)
        with pytest.raises(ValueError) as costs:
            get_consumption_costs(start, end, aggregation=interval, client=c)

    for error in (caught.value, costs.value):
        assert str(error).startswith(f"{bound} must be ")
        assert f"interval {interval!r}" in str(error)
        assert repr(got) in str(error)
    assert minstroem_service.requests == []


def test_a_lone_date_is_exactly_one_day_interval(
    minstroem_service: MinStroemService,
) -> None:
    with minstroem_service.client() as c:
        frame = get_consumption(date(2026, 3, 29), aggregation="day", client=c)

    assert list(frame.index) == [pd.Timestamp("2026-03-29", tz=CPH)]


@pytest.mark.parametrize("start", ["2026-03-01T10:30", "2026-03-01T10:00:30"])
def test_an_hourly_bound_off_the_hour_is_refused(
    minstroem_service: MinStroemService, start: str
) -> None:
    with minstroem_service.client() as c, pytest.raises(ValueError):
        get_consumption(start, client=c)

    assert minstroem_service.requests == []


@pytest.mark.parametrize("start", ["2026-03-29T02:00", "2026-10-25T02:00"])
def test_a_nonexistent_or_ambiguous_local_time_is_refused(
    minstroem_service: MinStroemService, start: str
) -> None:
    with minstroem_service.client() as c, pytest.raises(ValueError):
        get_consumption(start, client=c)

    assert minstroem_service.requests == []


def test_an_aware_instant_in_the_repeated_hour_is_one_row_at_its_own_offset(
    minstroem_service: MinStroemService,
) -> None:
    with minstroem_service.client() as c:
        frame = get_consumption("2026-10-25T02:00+01:00", client=c)

    assert len(frame) == 1
    assert frame.index[0] == pd.Timestamp("2026-10-25T01:00:00Z")
    assert frame.index[0].utcoffset() == timedelta(hours=1)
    assert minstroem_service.calls("/consumption")[0]["from"] == "2026-10-25T01:00:00Z"


@pytest.mark.parametrize("quarter", ["2026-11-02T10:15", "2026-11-02T10:45"])
def test_a_quarter_hour_bound_is_fine_for_quarters_and_refused_for_hours(
    minstroem_service: MinStroemService, quarter: str
) -> None:
    with minstroem_service.client() as c:
        frame = get_prices(quarter, resolution="fifteenMinutes", client=c)
        with pytest.raises(ValueError):
            get_prices(quarter, resolution="hour", client=c)

    assert len(frame) == 1


def test_resolve_series_period_returns_both_bounds_in_local_time() -> None:
    first, last = _resolve_series_period("2026-01-01", "2027-01-01", "year")

    assert first == pd.Timestamp("2026-01-01", tz=CPH)
    assert last == pd.Timestamp("2027-01-01", tz=CPH)
    assert str(first.tz) == CPH


# --- the index ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end", "interval", "length"),
    [
        ("2026-03-29", "2026-03-30", "hour", 23),
        ("2026-10-25", "2026-10-26", "hour", 25),
        ("2026-11-02", "2026-11-03", "hour", 24),
        ("2026-03-29", "2026-03-30", "fifteenMinutes", 92),
        ("2026-10-25", "2026-10-26", "fifteenMinutes", 100),
        ("2026-11-02", "2026-11-03", "fifteenMinutes", 96),
        ("2026-03-28", "2026-03-31", "day", 3),
        ("2026-01-01", "2027-01-01", "month", 12),
        ("2024-01-01", "2027-01-01", "year", 3),
    ],
)
def test_the_index_has_one_row_per_interval_on_the_danish_calendar(
    start: str, end: str, interval: str, length: int
) -> None:
    first, last = _resolve_series_period(start, end, interval)

    index = _interval_index(first, last, interval)

    assert len(index) == length
    assert index.name == "time"
    assert str(index.dtype) == f"datetime64[ns, {CPH}]"
    _assert_same_index(index, _expected_index(start, end, interval))


@pytest.mark.parametrize(
    ("start", "end", "interval", "offsets"),
    [
        ("2026-03-28", "2026-03-31", "day", [1, 1, 2]),
        ("2026-10-24", "2026-10-27", "day", [2, 2, 1]),
        ("2026-03-01", "2026-06-01", "month", [1, 2, 2]),
        ("2026-10-01", "2027-01-01", "month", [2, 1, 1]),
    ],
)
def test_calendar_intervals_stay_at_local_midnight_across_a_dst_change(
    start: str, end: str, interval: str, offsets: list[int]
) -> None:
    first, last = _resolve_series_period(start, end, interval)

    index = _interval_index(first, last, interval)

    assert list(index.hour) == [0] * len(index)
    assert list(index.minute) == [0] * len(index)
    assert [_offset_hours(t) for t in index] == offsets


def test_the_hour_after_the_autumn_change_is_repeated_not_skipped() -> None:
    first, last = _resolve_series_period("2026-10-25", "2026-10-26", "hour")

    index = _interval_index(first, last, "hour")

    assert index[2] == pd.Timestamp("2026-10-25 02:00:00+02:00")
    assert index[3] == pd.Timestamp("2026-10-25 02:00:00+01:00")
    assert index.is_unique


def test_the_spring_gap_hour_does_not_exist() -> None:
    first, last = _resolve_series_period("2026-03-29", "2026-03-30", "hour")

    index = _interval_index(first, last, "hour")

    assert 2 not in list(index.hour)


# --- windows -----------------------------------------------------------------


def _offset_hours(stamp: pd.Timestamp) -> int:
    offset = stamp.utcoffset()
    assert offset is not None
    return offset // timedelta(hours=1)


def _local(text: str) -> pd.Timestamp:
    return pd.Timestamp(text, tz=CPH).as_unit("ns")


@pytest.mark.parametrize(
    ("family", "interval", "start", "end", "expected"),
    [
        ("usage", "hour", "2025-01-01", "2026-01-01", ["2026-01-01"]),
        ("usage", "hour", "2025-01-01", "2026-01-01T01:00", ["2026-01-01", None]),
        ("usage", "day", "2020-01-01", "2024-01-02", ["2024-01-01", None]),
        ("usage", "month", "2020-01-01", "2024-02-01", ["2024-01-01", None]),
        ("usage", "year", "2021-01-01", "2026-01-01", ["2025-01-01", None]),
        ("costs", "hour", "2024-01-01", "2025-01-01", ["2024-12-31", None]),
        ("costs", "day", "2025-01-01", "2026-03-01", ["2026-01-01", None]),
        ("costs", "month", "2025-01-01", "2026-02-01", ["2026-01-01", None]),
        ("costs", "month", "2023-03-01", "2024-04-01", ["2024-03-01", None]),
        (
            "costs",
            "year",
            "2023-01-01",
            "2026-01-01",
            ["2024-01-01", "2025-01-01", None],
        ),
        ("prices", "hour", "2025-01-01", "2026-03-01", ["2026-01-01", None]),
        ("prices", "fifteenMinutes", "2026-01-01", "2026-04-02", ["2026-03-31", None]),
        ("prices", "fifteenMinutes", "2026-01-01", "2026-03-31", ["2026-03-31"]),
    ],
)
def test_windows_end_on_calendar_boundaries_and_abut(
    family: Family, interval: str, start: str, end: str, expected: list[str | None]
) -> None:
    first, last = _local(start), _local(end)

    windows = _windows(first, last, family, interval)

    # Each window but the last ends on the listed boundary; the last ends at `end`.
    ends = [_local(text) if text else last for text in expected]
    assert [hi for _, hi in windows] == ends
    assert windows[0][0] == first
    assert all(
        a_hi == b_lo for (_, a_hi), (b_lo, _) in zip(windows, windows[1:], strict=False)
    )
    assert all(lo < hi for lo, hi in windows)


@pytest.mark.parametrize(
    ("family", "interval", "limit_days"),
    [
        ("usage", "hour", 366),
        ("costs", "hour", 366),
        ("costs", "day", 366),
        ("costs", "month", 366),
        ("costs", "year", 366),
        ("prices", "hour", 366),
        ("prices", "fifteenMinutes", 90),
    ],
)
@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2020-01-01", "2023-06-01"),
        ("2024-01-01", "2026-12-01"),
        ("2023-03-01", "2025-10-01"),
    ],
)
def test_every_window_touches_no_more_local_days_than_the_cap(
    family: Family, interval: str, limit_days: int, start: str, end: str
) -> None:
    windows = _windows(_local(start), _local(end), family, interval)

    for lo, hi in windows:
        last_day = (hi - pd.Timedelta(microseconds=1)).date()
        assert (last_day - lo.date()).days + 1 <= limit_days
    assert windows[0][0] == _local(start)
    assert windows[-1][1] == _local(end)


@pytest.mark.parametrize("interval", ["day", "month", "year"])
def test_usage_windows_never_touch_more_than_five_calendar_years(
    interval: str,
) -> None:
    windows = _windows(_local("2000-01-01"), _local("2026-01-01"), "usage", interval)

    for lo, hi in windows:
        last_year = (hi - pd.Timedelta(microseconds=1)).year
        assert last_year - lo.year + 1 <= 5
    assert len(windows) == 7


def test_window_boundaries_are_local_midnights_not_elapsed_time() -> None:
    # 365 elapsed days from 2026-01-01 would land at 23:00 on 2026-12-31 after
    # the March and October changes; the calendar boundary is local midnight.
    windows = _windows(_local("2026-01-01"), _local("2028-01-01"), "costs", "hour")

    assert [hi for _, hi in windows][0] == _local("2027-01-01")
    assert all(hi.hour == 0 for _, hi in windows)


def test_a_period_inside_one_window_is_one_window() -> None:
    first, last = _local("2026-03-29"), _local("2026-03-30")

    assert _windows(first, last, "prices", "fifteenMinutes") == [(first, last)]


# --- end to end: windows, caps and the frame (T6) ----------------------------

WINDOW_CASES = [
    # fn, suffix, kw, interval, start, end, windows, rows, first window ends (UTC)
    pytest.param(
        get_consumption, "consumption", "aggregation", "hour",
        "2025-01-01", "2026-01-01T01:00", 2, 8761, "2025-12-31T23:00:00Z",
        id="usage-hour-over",
    ),  # fmt: skip
    pytest.param(
        get_consumption, "consumption", "aggregation", "hour",
        "2025-01-01", "2026-01-01", 1, 8760, "2025-12-31T23:00:00Z",
        id="usage-hour-at-cap",
    ),  # fmt: skip
    pytest.param(
        get_solar_export, "solar-export", "aggregation", "hour",
        "2024-01-01", "2025-01-02", 2, 8808, "2024-12-30T23:00:00Z",
        id="solar-hour-leap-year",
    ),  # fmt: skip
    pytest.param(
        get_consumption, "consumption", "aggregation", "day",
        "2020-01-01", "2024-01-02", 2, 1462, "2023-12-31T23:00:00Z",
        id="usage-day",
    ),  # fmt: skip
    pytest.param(
        get_solar_export, "solar-export", "aggregation", "month",
        "2020-01-01", "2024-02-01", 2, 49, "2023-12-31T23:00:00Z",
        id="usage-month",
    ),  # fmt: skip
    pytest.param(
        get_consumption, "consumption", "aggregation", "year",
        "2021-01-01", "2026-01-01", 2, 5, "2024-12-31T23:00:00Z",
        id="usage-year",
    ),  # fmt: skip
    pytest.param(
        get_consumption_costs, "consumption-costs", "aggregation", "hour",
        "2024-01-01", "2025-01-01", 2, 8784, "2024-12-30T23:00:00Z",
        id="costs-hour-leap-year",
    ),  # fmt: skip
    pytest.param(
        get_solar_export_revenue, "solar-export-revenue", "aggregation", "day",
        "2025-01-01", "2026-03-01", 2, 424, "2025-12-31T23:00:00Z",
        id="revenue-day",
    ),  # fmt: skip
    pytest.param(
        get_consumption_costs, "consumption-costs", "aggregation", "month",
        "2025-01-01", "2026-02-01", 2, 13, "2025-12-31T23:00:00Z",
        id="costs-month",
    ),  # fmt: skip
    pytest.param(
        get_solar_export_revenue, "solar-export-revenue", "aggregation", "month",
        "2023-03-01", "2024-04-01", 2, 13, "2024-02-29T23:00:00Z",
        id="revenue-month-with-29-february",
    ),  # fmt: skip
    pytest.param(
        get_consumption_costs, "consumption-costs", "aggregation", "year",
        "2023-01-01", "2026-01-01", 3, 3, "2023-12-31T23:00:00Z",
        id="costs-year",
    ),  # fmt: skip
    pytest.param(
        get_prices, "prices", "resolution", "hour",
        "2025-01-01", "2026-03-01", 2, 10176, "2025-12-31T23:00:00Z",
        id="prices-hour",
    ),  # fmt: skip
    pytest.param(
        get_prices, "prices", "resolution", "fifteenMinutes",
        "2026-01-01", "2026-04-02", 2, 8732, "2026-03-30T22:00:00Z",
        id="prices-quarter-across-dst",
    ),  # fmt: skip
]  # fmt: skip


@pytest.mark.parametrize(
    ("fn", "suffix", "kw", "interval", "start", "end", "windows", "rows", "first_to"),
    WINDOW_CASES,
)
def test_a_period_over_the_cap_is_split_aligned_and_equals_one_unbounded_call(
    minstroem_service: MinStroemService,
    fn: Series,
    suffix: str,
    kw: str,
    interval: str,
    start: str,
    end: str,
    windows: int,
    rows: int,
    first_to: str,
) -> None:
    with minstroem_service.client() as c:
        frame = fn(start, end, **{kw: interval}, address_id=ADDRESS_ID, client=c)

    calls = sorted(minstroem_service.calls("/" + suffix), key=lambda q: q["from"])
    assert len(calls) == windows
    assert calls[0]["from"] == _utc(start)
    assert calls[-1]["to"] == _utc(end)
    assert calls[0]["to"] == first_to
    assert all(a["to"] == b["from"] for a, b in zip(calls, calls[1:], strict=False))
    assert all(q[kw] == interval for q in calls)
    # The mock refuses any window over its cap, so each one passed that check.
    assert len(frame) == rows
    expected = _expected_index(start, end, interval)
    _assert_same_index(frame.index, expected)
    column = "total_price_per_kwh" if suffix == "prices" else "energy_kwh"
    values = _energy(expected) / (1000 if suffix == "prices" else 1)
    pd.testing.assert_series_equal(
        frame[column], values.rename(column), check_freq=False
    )


def test_the_mock_refuses_the_whole_over_cap_period_in_one_request(
    minstroem_service: MinStroemService,
) -> None:
    # The control for the test above: unbounded, the mock answers 400.
    with minstroem_service.client() as c, pytest.raises(MinStroemError) as caught:
        c.get_json(
            f"/me/addresses/{ADDRESS_ID}/consumption",
            {
                "from": _utc("2025-01-01"),
                "to": _utc("2026-01-03"),
                "aggregation": "hour",
            },
        )

    assert caught.value.error_code == "range_too_large"


def test_windows_are_requested_one_at_a_time_in_order_with_one_in_flight(
    minstroem_service: MinStroemService,
) -> None:
    with minstroem_service.client(max_concurrency=1) as c:
        frame = get_consumption(
            "2022-01-01", "2024-07-01", address_id=ADDRESS_ID, client=c
        )

    sent = [q["from"] for q in minstroem_service.calls("/consumption")]
    assert len(sent) == 3
    assert sent == sorted(sent)
    assert frame["energy_kwh"].notna().all()


# --- the frame ----------------------------------------------------------------


@pytest.mark.parametrize("case", SERIES)
def test_a_period_with_no_rows_is_all_missing_with_the_right_dtypes(
    minstroem_service: MinStroemService, case: SeriesCase
) -> None:
    fn = case.fn
    minstroem_service.generate = False

    with minstroem_service.client() as c:
        frame = fn("2026-11-02", address_id=ADDRESS_ID, client=c)

    assert len(frame) == 24
    assert frame.index.name == "time"
    assert frame.drop(columns="is_estimate", errors="ignore").isna().all().all()
    assert (
        frame.drop(columns="is_estimate", errors="ignore").dtypes == "float64"
    ).all()
    if "is_estimate" in frame:
        assert str(frame["is_estimate"].dtype) == "boolean"
        assert frame["is_estimate"].isna().all()


def test_a_zero_reading_stays_zero_and_a_missing_row_is_nan(
    minstroem_service: MinStroemService,
) -> None:
    minstroem_service.rows["consumption"] = [
        _usage_row("2025-12-31T23:00:00Z", 0.0),
        _usage_row("2026-01-01T01:00:00Z", 1.25, estimate=True),
    ]

    with minstroem_service.client() as c:
        frame = get_consumption("2026-01-01T00:00", "2026-01-01T03:00", client=c)

    assert frame["energy_kwh"].tolist()[0] == 0.0
    assert pd.isna(frame["energy_kwh"].iloc[1])
    assert frame["energy_kwh"].iloc[2] == 1.25
    assert frame["is_estimate"].tolist() == [False, pd.NA, True]
    assert str(frame["is_estimate"].dtype) == "boolean"
    assert str(frame["energy_kwh"].dtype) == "float64"


def test_a_gap_in_the_middle_is_one_nan_row(
    minstroem_service: MinStroemService,
) -> None:
    minstroem_service.gaps = {"2026-11-02T04:00:00Z"}

    with minstroem_service.client() as c:
        frame = get_consumption("2026-11-02", client=c)

    assert frame["energy_kwh"].isna().sum() == 1
    assert pd.isna(frame.loc[pd.Timestamp("2026-11-02 05:00", tz=CPH), "energy_kwh"])


def test_rows_arriving_out_of_order_land_on_their_own_interval(
    minstroem_service: MinStroemService,
) -> None:
    rows = [
        _usage_row("2026-11-02T02:00:00Z", 3.0),
        _usage_row("2026-11-02T00:00:00Z", 1.0),
        _usage_row("2026-11-02T01:00:00Z", 2.0),
    ]
    minstroem_service.rows["consumption"] = rows

    with minstroem_service.client() as c:
        frame = get_consumption("2026-11-02T01:00", "2026-11-02T05:00", client=c)

    assert frame["energy_kwh"].tolist()[:3] == [1.0, 2.0, 3.0]


def test_rows_outside_their_window_are_dropped_not_duplicated(
    minstroem_service: MinStroemService,
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        lo, hi = _stamp(request.url.params["from"]), _stamp(request.url.params["to"])
        marker = 1.0 if request.url.params["from"] == "2024-12-31T23:00:00Z" else 2.0
        starts = [lo - timedelta(hours=1)]
        while starts[-1] < hi:
            starts.append(starts[-1] + timedelta(hours=1))
        rows = [_usage_row(s.strftime(TIME_FORMAT), marker) for s in starts]
        return httpx.Response(200, json={"data": rows})

    minstroem_service.overrides["consumption"] = respond

    with minstroem_service.client() as c:
        frame = get_consumption(
            "2025-01-01", "2026-01-01T01:00", address_id=ADDRESS_ID, client=c
        )

    assert len(frame) == 8761
    # Window 1 also served the row at its `to`; window 2 owns that hour.
    assert frame.loc[pd.Timestamp("2026-01-01 00:00", tz=CPH), "energy_kwh"] == 2.0
    # Window 2 also served the hour before its `from`; window 1 owns that hour.
    assert frame.loc[pd.Timestamp("2025-12-31 23:00", tz=CPH), "energy_kwh"] == 1.0
    assert frame["energy_kwh"].notna().all()


@pytest.mark.parametrize(
    "start",
    [
        "2026-11-01T23:30:00Z",  # not on the hour
        "2026-11-03T23:00:00Z",  # inside no window but in range of the data
    ],
)
def test_a_row_inside_its_window_but_off_the_interval_grid_is_an_error(
    minstroem_service: MinStroemService, start: str
) -> None:
    _serve(minstroem_service, "consumption", {"data": [_usage_row(start)]})

    with minstroem_service.client() as c:
        if start.endswith("23:30:00Z"):
            with pytest.raises(
                MinStroemError, match="not an interval of the requested"
            ):
                get_consumption("2026-11-02", address_id=ADDRESS_ID, client=c)
        else:
            # Outside the window it is dropped, so the frame is simply empty.
            frame = get_consumption("2026-11-02", address_id=ADDRESS_ID, client=c)
            assert frame["energy_kwh"].isna().all()


def test_utc_midnight_mistaken_for_local_midnight_is_an_error_for_day_rows(
    minstroem_service: MinStroemService,
) -> None:
    _serve(
        minstroem_service,
        "consumption",
        {"data": [_usage_row("2026-03-02T00:00:00Z", hours=24)]},
    )

    with minstroem_service.client() as c, pytest.raises(MinStroemError) as caught:
        get_consumption(
            "2026-03-02",
            "2026-03-03",
            aggregation="day",
            address_id=ADDRESS_ID,
            client=c,
        )

    assert "not an interval of the requested period" in str(caught.value)


def test_hourly_rows_for_a_quarter_hour_request_are_an_error_the_other_way_round(
    minstroem_service: MinStroemService,
) -> None:
    # A server answering 15-minute rows to an hourly request.
    rows = [
        _usage_row("2026-11-01T23:15:00Z", 1.0),
        _usage_row("2026-11-01T23:30:00Z", 1.0),
    ]
    _serve(minstroem_service, "consumption", {"data": rows})

    with minstroem_service.client() as c, pytest.raises(MinStroemError):
        get_consumption("2026-11-02", address_id=ADDRESS_ID, client=c)


def test_two_rows_with_the_same_start_are_an_error(
    minstroem_service: MinStroemService,
) -> None:
    row = _usage_row("2026-11-01T23:00:00Z")
    _serve(minstroem_service, "consumption", {"data": [row, dict(row)]})

    with minstroem_service.client() as c, pytest.raises(MinStroemError) as caught:
        get_consumption("2026-11-02", address_id=ADDRESS_ID, client=c)

    assert "two rows start at" in str(caught.value)
    assert caught.value.status is None


GOOD_ROW = _usage_row("2026-11-01T23:00:00Z", 1.5)


def _without(key: str) -> dict[str, Any]:
    return {k: v for k, v in GOOD_ROW.items() if k != key}


@pytest.mark.parametrize(
    "row",
    [
        {**GOOD_ROW, "energyKWh": "1.5"},
        {**GOOD_ROW, "energyKWh": True},
        {**GOOD_ROW, "energyKWh": None},
        {**GOOD_ROW, "energyKWh": [1.5]},
        {**GOOD_ROW, "isEstimate": None},
        {**GOOD_ROW, "isEstimate": 0},
        {**GOOD_ROW, "isEstimate": "false"},
        {**GOOD_ROW, "start": 1767225600},
        {**GOOD_ROW, "start": None},
        {**GOOD_ROW, "start": "2026-11-01T23:00:00"},
        {**GOOD_ROW, "start": "garbage"},
        {**GOOD_ROW, "start": ""},
        {**GOOD_ROW, "start": "NaT"},
        {**GOOD_ROW, "start": "2300-01-01T00:00:00Z"},
        {**GOOD_ROW, "end": "garbage"},
        {**GOOD_ROW, "end": None},
        {**GOOD_ROW, "end": "2300-01-01T00:00:00Z"},
        _without("start"),
        _without("end"),
        _without("energyKWh"),
        _without("isEstimate"),
        [],
        "row",
        None,
        7,
    ],
    ids=repr,
)
def test_a_malformed_row_is_a_minstroem_error_without_status(
    minstroem_service: MinStroemService, row: object
) -> None:
    _serve(minstroem_service, "consumption", {"data": [row]})

    with minstroem_service.client() as c, pytest.raises(MinStroemError) as caught:
        get_consumption("2026-11-02", address_id=ADDRESS_ID, client=c)

    error = caught.value
    assert (error.status, error.error_code, error.reason, error.request_id) == (
        None,
        None,
        None,
        None,
    )
    assert "/consumption" in str(error)


@pytest.mark.parametrize("body", [{}, {"data": None}, {"data": {}}, {"data": "x"}])
def test_a_payload_without_a_data_list_is_a_minstroem_error(
    minstroem_service: MinStroemService, body: dict[str, Any]
) -> None:
    _serve(minstroem_service, "consumption", body)

    with minstroem_service.client() as c, pytest.raises(MinStroemError) as caught:
        get_consumption("2026-11-02", address_id=ADDRESS_ID, client=c)

    assert "no 'data' list" in str(caught.value)


@pytest.mark.parametrize(
    "literal", ["NaN", "Infinity", "-Infinity", "9" * 400, "1e999"]
)
def test_a_number_that_is_not_finite_is_an_error_not_a_silent_nan(
    minstroem_service: MinStroemService, literal: str
) -> None:
    row = (
        '{"start": "2026-11-01T23:00:00Z", "end": "2026-11-02T00:00:00Z",'
        f' "energyKWh": {literal}, "isEstimate": false}}'
    )
    _serve_raw(minstroem_service, "consumption", f'{{"data": [{row}]}}'.encode())

    with minstroem_service.client() as c, pytest.raises(MinStroemError) as caught:
        get_consumption("2026-11-02", address_id=ADDRESS_ID, client=c)

    assert "energyKWh" in str(caught.value)
    assert caught.value.status is None


def test_a_negative_number_is_data_not_an_error(
    minstroem_service: MinStroemService,
) -> None:
    # Negative prices and net-export readings are legitimate in Denmark.
    _serve(
        minstroem_service,
        "consumption",
        {"data": [_usage_row("2026-11-01T23:00:00Z", -0.4)]},
    )

    with minstroem_service.client() as c:
        frame = get_consumption("2026-11-02", address_id=ADDRESS_ID, client=c)

    assert frame["energy_kwh"].iloc[0] == -0.4


def test_an_integer_reading_is_a_float() -> None:
    service = MinStroemService()
    _serve(service, "consumption", {"data": [_usage_row("2026-11-01T23:00:00Z", 3)]})

    with service.client() as c:
        frame = get_consumption("2026-11-02", address_id=ADDRESS_ID, client=c)

    assert frame["energy_kwh"].iloc[0] == 3.0
    assert str(frame["energy_kwh"].dtype) == "float64"


def test_a_row_without_an_explicit_offset_would_be_refused_but_z_and_offsets_agree(
    minstroem_service: MinStroemService,
) -> None:
    rows = [
        {
            **_usage_row("2026-11-01T23:00:00Z", 1.0),
            "start": "2026-11-02T00:00:00+01:00",
        },
    ]
    _serve(minstroem_service, "consumption", {"data": rows})

    with minstroem_service.client() as c:
        frame = get_consumption("2026-11-02", address_id=ADDRESS_ID, client=c)

    assert frame["energy_kwh"].iloc[0] == 1.0


# --- the request ----------------------------------------------------------------


@pytest.mark.parametrize("case", SERIES)
def test_the_request_carries_utc_from_to_and_the_interval_parameter(
    minstroem_service: MinStroemService, case: SeriesCase
) -> None:
    fn = case.fn
    suffix = case.suffix
    kw = case.kw
    with minstroem_service.client() as c:
        fn("2026-11-02", address_id=ADDRESS_ID, client=c)

    assert minstroem_service.calls("/" + suffix) == [
        {"from": "2026-11-01T23:00:00Z", "to": "2026-11-02T23:00:00Z", kw: "hour"}
    ]
    path = minstroem_service.requests[0].url.path
    assert path == f"/consumer/v1/me/addresses/{ADDRESS_ID}/{suffix}"


@pytest.mark.parametrize(
    ("day", "length", "sent_from", "sent_to"),
    [
        ("2026-03-29", 23, "2026-03-28T23:00:00Z", "2026-03-29T22:00:00Z"),
        ("2026-10-25", 25, "2026-10-24T22:00:00Z", "2026-10-25T23:00:00Z"),
    ],
)
@pytest.mark.parametrize("case", SERIES)
def test_a_dst_day_has_23_or_25_hourly_rows_and_the_right_utc_window(
    minstroem_service: MinStroemService,
    case: SeriesCase,
    day: str,
    length: int,
    sent_from: str,
    sent_to: str,
) -> None:
    fn = case.fn
    suffix = case.suffix
    with minstroem_service.client() as c:
        frame = fn(day, address_id=ADDRESS_ID, client=c)

    assert len(frame) == length
    assert frame.index.is_unique
    assert minstroem_service.calls("/" + suffix)[0]["from"] == sent_from
    assert minstroem_service.calls("/" + suffix)[0]["to"] == sent_to
    assert frame.iloc[:, 0].notna().all()


def test_the_series_is_not_changed_by_a_second_identical_call(
    minstroem_service: MinStroemService,
) -> None:
    with minstroem_service.client() as c:
        first = get_consumption("2026-11-02", client=c)
        second = get_consumption("2026-11-02", client=c)

    pd.testing.assert_frame_equal(first, second)
    assert first.attrs == second.attrs


# --- the error from a window ---------------------------------------------------


def test_a_failure_in_the_second_window_is_one_minstroem_error_not_a_group(
    minstroem_service: MinStroemService,
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.params["from"] == "2025-12-31T23:00:00Z":
            body = {"error": True, "reason": "too big", "errorCode": "range_too_large"}
            return httpx.Response(400, json=body)
        return httpx.Response(200, json={"data": []})

    minstroem_service.overrides["consumption"] = respond

    with minstroem_service.client() as c, pytest.raises(MinStroemError) as caught:
        get_consumption(
            "2025-01-01", "2026-01-01T01:00", address_id=ADDRESS_ID, client=c
        )

    error = caught.value
    assert not isinstance(error, BaseExceptionGroup)
    assert (error.status, error.error_code, error.reason) == (
        400,
        "range_too_large",
        "too big",
    )
    assert error.request_id is not None


def test_a_missing_key_over_two_windows_is_one_missing_key_error_and_no_request(
    monkeypatch: pytest.MonkeyPatch, minstroem_service: MinStroemService
) -> None:
    monkeypatch.delenv(API_KEY_ENV, raising=False)

    with minstroem_service.client(None) as c, pytest.raises(MissingApiKeyError):
        get_consumption(
            "2025-01-01", "2026-01-01T01:00", address_id=ADDRESS_ID, client=c
        )

    assert minstroem_service.requests == []


def test_a_windowed_failure_still_carries_the_http_error_of_a_single_request() -> None:
    # Known limit, recorded in the plan's test log: on a windowed path the
    # chained cause is the task group's ExceptionGroup (utils' re-raise rule),
    # so the underlying httpx error is not reachable; the four attributes are.
    service = MinStroemService()

    with service.client() as c, pytest.raises(MinStroemError) as caught:
        get_consumption("2026-11-02", address_id=OTHER, client=c)

    error = caught.value
    assert (error.status, error.error_code, error.request_id) == (
        404,
        "resource_not_found",
        minstroem_request_id(1),
    )
    assert isinstance(error.__cause__, BaseExceptionGroup)
    assert error in error.__cause__.exceptions


# --- the address (A4, T4) -----------------------------------------------------

type Call = Callable[..., pd.DataFrame]


class AddressCase(NamedTuple):
    """One address-scoped function: how to call it, its path suffix, its capability."""

    call: Call
    suffix: str
    capability: str


ADDRESS_CASES = {
    "consumption": AddressCase(
        lambda **kw: get_consumption("2026-11-02", **kw), "/consumption", "consumption"
    ),
    "solar-export": AddressCase(
        lambda **kw: get_solar_export("2026-11-02", **kw),
        "/solar-export",
        "solarExport",
    ),
    "costs": AddressCase(
        lambda **kw: get_consumption_costs("2026-11-02", **kw),
        "/consumption-costs",
        "consumptionCosts",
    ),
    "revenue": AddressCase(
        lambda **kw: get_solar_export_revenue("2026-11-02", **kw),
        "/solar-export-revenue",
        "solarExportRevenue",
    ),
    "prices": AddressCase(
        lambda **kw: get_prices("2026-11-02", **kw), "/prices", "prices"
    ),
    "forecast": AddressCase(
        lambda **kw: get_forecast_prices(**kw), "/forecast-prices", "forecastPrices"
    ),
    "with-forecast": AddressCase(
        lambda **kw: get_prices_with_forecast("2026-11-02", "2026-11-03", **kw),
        "/forecast-prices",
        "forecastPrices",
    ),
}
ADDRESS_SCOPED = [pytest.param(case, id=name) for name, case in ADDRESS_CASES.items()]


@pytest.mark.parametrize("case", ADDRESS_SCOPED)
def test_the_only_address_is_looked_up_once_and_used(
    minstroem_service: MinStroemService, case: AddressCase
) -> None:
    call = case.call
    with minstroem_service.client() as c:
        frame = call(client=c)

    requests = minstroem_service.requests
    assert requests[0].url.path == "/consumer/v1/me/addresses"
    assert len(minstroem_service.calls("/me/addresses")) == 1
    assert all(f"/me/addresses/{ADDRESS_ID}/" in r.url.path for r in requests[1:])
    assert len(requests) >= 2
    assert frame.attrs["address_id"] == ADDRESS_ID


@pytest.mark.parametrize("case", ADDRESS_SCOPED)
def test_an_explicit_address_id_skips_the_lookup(
    minstroem_service: MinStroemService, case: AddressCase
) -> None:
    call = case.call
    with minstroem_service.client() as c:
        frame = call(address_id=ADDRESS_ID, client=c)

    assert minstroem_service.calls("/me/addresses") == []
    assert frame.attrs["address_id"] == ADDRESS_ID


@pytest.mark.parametrize("case", ADDRESS_SCOPED)
def test_a_missing_capability_is_refused_after_the_lookup_and_before_the_endpoint(
    case: AddressCase,
) -> None:
    call = case.call
    suffix = case.suffix
    capability = case.capability
    service = _lacking(capability)

    with service.client() as c, pytest.raises(MinStroemError) as caught:
        call(client=c)

    error = caught.value
    assert f"'{capability}'" in str(error)
    assert error.status is None
    assert service.calls(suffix) == []
    assert [r.url.path for r in service.requests] == ["/consumer/v1/me/addresses"]


@pytest.mark.parametrize("case", ADDRESS_SCOPED)
def test_a_missing_capability_with_an_explicit_id_is_the_servers_422_naming_it(
    case: AddressCase,
) -> None:
    call = case.call
    capability = case.capability
    service = _lacking(capability)

    with service.client() as c, pytest.raises(MinStroemError) as caught:
        call(address_id=ADDRESS_ID, client=c)

    error = caught.value
    assert error.status == 422
    assert error.error_code == "feature_unavailable"
    assert str(error).endswith(f"; the address lacks the '{capability}' capability")
    assert error.request_id is not None
    assert service.calls("/me/addresses") == []


@pytest.mark.parametrize("case", ADDRESS_SCOPED)
def test_no_addresses_is_a_value_error(case: AddressCase) -> None:
    call = case.call
    service = MinStroemService(addresses=[])

    with (
        service.client() as c,
        pytest.raises(ValueError, match="no addresses on this account"),
    ):
        call(client=c)

    assert len(service.requests) == 1


@pytest.mark.parametrize("case", ADDRESS_SCOPED)
def test_several_addresses_are_a_value_error_listing_each_id_and_label(
    case: AddressCase,
) -> None:
    call = case.call
    service = MinStroemService(
        [
            minstroem_address(ADDRESS_ID, "Home"),
            minstroem_address(OTHER, "Sommerhus æøå"),
        ]
    )

    with service.client() as c:
        with pytest.raises(ValueError) as caught:
            call(client=c)
        message = str(caught.value)
        assert ADDRESS_ID in message
        assert OTHER in message
        assert "Home" in message
        assert "Sommerhus æøå" in message
        assert "address_id" in message
        assert len(service.requests) == 1
        # Choosing one of them works, with no lookup.
        service.requests.clear()
        call(address_id=OTHER, client=c)

    assert service.calls("/me/addresses") == []
    assert all(OTHER in r.url.path for r in service.requests)


@pytest.mark.parametrize("case", ADDRESS_SCOPED)
@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not-a-uuid",
        "../../me/addresses",
        "x/y",
        ADDRESS_ID[:-1],
        ADDRESS_ID + "0",
        ADDRESS_ID + "\n",
        ADDRESS_ID + "/consumption",
        ADDRESS_ID + "?x=1",
        123,
        uuid.UUID(ADDRESS_ID),
    ],
    ids=repr,
)
def test_an_address_id_that_is_not_a_uuid_string_is_refused_before_a_key_or_request(
    monkeypatch: pytest.MonkeyPatch,
    minstroem_service: MinStroemService,
    case: AddressCase,
    bad: object,
) -> None:
    call = case.call
    monkeypatch.delenv(API_KEY_ENV, raising=False)

    with (
        minstroem_service.client(None) as c,
        pytest.raises(ValueError, match="address_id must be a UUID") as caught,
    ):
        call(address_id=bad, client=c)

    assert not isinstance(caught.value, MissingApiKeyError)
    assert minstroem_service.requests == []


@pytest.mark.parametrize(
    "form",
    [
        ADDRESS_ID.upper(),
        "{" + ADDRESS_ID + "}",
        "urn:uuid:" + ADDRESS_ID,
        ADDRESS_ID.replace("-", ""),
    ],
)
def test_an_explicit_uuid_in_any_form_is_sent_in_its_canonical_lowercase_form(
    minstroem_service: MinStroemService, form: str
) -> None:
    with minstroem_service.client() as c:
        frame = get_consumption("2026-11-02", address_id=form, client=c)

    assert minstroem_service.requests[0].url.path.endswith(f"/{ADDRESS_ID}/consumption")
    assert frame.attrs["address_id"] == ADDRESS_ID


def test_check_address_id_returns_the_canonical_form() -> None:
    assert _check_address_id(ADDRESS_ID.upper()) == ADDRESS_ID
    with pytest.raises(ValueError, match="'oops'"):
        _check_address_id("oops")


@pytest.mark.parametrize("bad", ["not-a-uuid", "a/b", "../x", ADDRESS_ID.upper() + "x"])
def test_an_id_the_server_sends_that_is_not_a_uuid_is_never_put_in_a_path(
    bad: str,
) -> None:
    service = MinStroemService([minstroem_address(address_id=bad)])

    with service.client() as c, pytest.raises(MinStroemError) as caught:
        get_consumption("2026-11-02", client=c)

    assert "not a UUID" in str(caught.value)
    assert [r.url.path for r in service.requests] == ["/consumer/v1/me/addresses"]


def test_an_id_the_server_sends_is_used_as_sent() -> None:
    upper = ADDRESS_ID.upper()
    service = MinStroemService([minstroem_address(address_id=upper)])
    # The mock keys addresses by their exact id, so the series path must match.
    with service.client() as c:
        frame = get_consumption("2026-11-02", client=c)

    assert frame.attrs["address_id"] == upper
    assert service.requests[1].url.path.endswith(f"/{upper}/consumption")


# --- the client lifecycle (T9) --------------------------------------------------


def _spy(
    monkeypatch: pytest.MonkeyPatch, service: MinStroemService
) -> list[MinStroemClient]:
    """Make the functions build their client from `service`, and remember each."""
    created: list[MinStroemClient] = []

    def make() -> MinStroemClient:
        client = service.client()
        created.append(client)
        return client

    monkeypatch.setattr(_series, "MinStroemClient", make)
    return created


ALL_FUNCTIONS = [
    *(pytest.param(case.call, id=name) for name, case in ADDRESS_CASES.items()),
    pytest.param(lambda **kw: get_addresses(**kw), id="addresses"),
]


@pytest.mark.parametrize("call", ALL_FUNCTIONS)
def test_a_client_a_function_created_is_closed_after_success(
    monkeypatch: pytest.MonkeyPatch, minstroem_service: MinStroemService, call: Call
) -> None:
    created = _spy(monkeypatch, minstroem_service)

    call()

    assert len(created) == 1
    assert created[0].closed is True


@pytest.mark.parametrize("call", ALL_FUNCTIONS)
def test_a_client_a_function_created_is_closed_after_an_error(
    monkeypatch: pytest.MonkeyPatch, call: Call
) -> None:
    service = MinStroemService(addresses=[])
    service.overrides["addresses"] = lambda _r: httpx.Response(
        403, json={"error": True, "reason": "no", "errorCode": "plus_required"}
    )
    created = _spy(monkeypatch, service)

    with pytest.raises(MinStroemError):
        call()

    assert created[0].closed is True


@pytest.mark.parametrize("call", ALL_FUNCTIONS)
def test_a_client_a_function_created_is_closed_when_the_key_is_missing(
    monkeypatch: pytest.MonkeyPatch, call: Call
) -> None:
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    made: list[MinStroemClient] = []
    real = MinStroemClient

    def make() -> MinStroemClient:
        client = real()
        made.append(client)
        return client

    monkeypatch.setattr(_series, "MinStroemClient", make)

    with pytest.raises(MissingApiKeyError):
        call()

    assert len(made) == 1
    assert made[0].closed is True


@pytest.mark.parametrize("call", ALL_FUNCTIONS)
def test_a_passed_client_is_left_open_and_reusable(
    minstroem_service: MinStroemService, call: Call
) -> None:
    with minstroem_service.client() as c:
        first = call(client=c)
        assert c.closed is False
        second = call(client=c)
        assert c.closed is False

    assert first.shape == second.shape
    assert list(first.columns) == list(second.columns)


@pytest.mark.parametrize("call", ALL_FUNCTIONS)
def test_a_closed_passed_client_is_a_runtime_error(
    minstroem_service: MinStroemService, call: Call
) -> None:
    c = minstroem_service.client()
    c.close()

    with pytest.raises(RuntimeError):
        call(client=c)


# --- the mock itself (T13) ---------------------------------------------------------


def _ask(
    service: MinStroemService,
    suffix: str,
    key: str | None = "test-key",
    address: str = ADDRESS_ID,
    **params: str,
) -> httpx.Response:
    """Send one request straight to the mock."""
    headers = {} if key is None else {"Authorization": f"Bearer {key}"}
    url = (
        f"{BASE_URL}/me/addresses"
        if suffix == ""
        else f"{BASE_URL}/me/addresses/{address}/{suffix}"
    )
    return service(httpx.Request("GET", url, params=params, headers=headers))


def _error_code(response: httpx.Response) -> str:
    body = response.json()
    assert body["error"] is True
    code: str = body["errorCode"]
    return code


def test_the_mock_checks_the_bearer_key() -> None:
    service = MinStroemService()

    assert _ask(service, "").status_code == 200
    for key in (None, "wrong", ""):
        response = _ask(service, "", key=key)
        assert response.status_code == 401
        assert _error_code(response) == "invalid_token"
    assert _ask(MinStroemService(api_key="other"), "", key="other").status_code == 200


def test_the_mock_stamps_a_running_request_id_on_every_response() -> None:
    service = MinStroemService()

    ids = [
        _ask(service, "").headers["X-Request-ID"],
        _ask(service, "", key=None).headers["X-Request-ID"],
        _ask(service, "nope", address=OTHER).headers["X-Request-ID"],
    ]

    assert ids == [minstroem_request_id(n) for n in (1, 2, 3)]
    assert len(service.requests) == 3


GOOD = {
    "from": "2026-11-01T23:00:00Z",
    "to": "2026-11-02T23:00:00Z",
    "aggregation": "hour",
}


@pytest.mark.parametrize(
    ("params", "status", "code"),
    [
        (
            {k: v for k, v in GOOD.items() if k != "aggregation"},
            400,
            "invalid_parameter",
        ),
        ({**GOOD, "aggregation": "week"}, 400, "invalid_parameter"),
        ({**GOOD, "aggregation": "fifteenMinutes"}, 400, "invalid_parameter"),
        ({k: v for k, v in GOOD.items() if k != "from"}, 400, "invalid_parameter"),
        ({k: v for k, v in GOOD.items() if k != "to"}, 400, "invalid_parameter"),
        ({**GOOD, "from": "yesterday"}, 400, "invalid_parameter"),
        ({**GOOD, "to": "2026-11-02T23:00:00+01:00"}, 400, "invalid_parameter"),
        ({**GOOD, "to": GOOD["from"]}, 400, "invalid_range"),
        ({**GOOD, "to": "2026-11-01T22:00:00Z"}, 400, "invalid_range"),
    ],
)
def test_the_mock_validates_parameters(
    params: dict[str, str], status: int, code: str
) -> None:
    response = _ask(MinStroemService(), "consumption", **params)

    assert response.status_code == status
    assert _error_code(response) == code


def test_the_mock_wants_resolution_for_prices_and_aggregation_for_the_rest() -> None:
    service = MinStroemService()
    prices_params = {k: v for k, v in GOOD.items() if k != "aggregation"}

    assert (
        _ask(service, "prices", **prices_params, resolution="hour").status_code == 200
    )
    assert (
        _ask(service, "prices", **prices_params, aggregation="hour").status_code == 400
    )
    assert _ask(service, "prices", **prices_params, resolution="day").status_code == 400
    assert _ask(service, "consumption-costs", **GOOD).status_code == 200


def test_the_mock_expands_the_period_outward_to_whole_intervals() -> None:
    service = MinStroemService()
    params = {
        "from": "2026-11-01T23:30:00Z",
        "to": "2026-11-02T01:10:00Z",
        "aggregation": "hour",
    }

    body = _ask(service, "consumption", **params).json()

    assert body["from"] == "2026-11-01T23:00:00Z"
    assert body["to"] == "2026-11-02T02:00:00Z"
    assert [r["start"] for r in body["data"]] == [
        "2026-11-01T23:00:00Z",
        "2026-11-02T00:00:00Z",
        "2026-11-02T01:00:00Z",
    ]


def test_the_mock_expands_day_month_and_year_to_the_copenhagen_calendar() -> None:
    service = MinStroemService()

    day = _ask(
        service,
        "consumption",
        **{
            "from": "2026-03-28T23:30:00Z",
            "to": "2026-03-29T00:30:00Z",
            "aggregation": "day",
        },
    ).json()
    month = _ask(
        service,
        "consumption",
        **{
            "from": "2026-03-15T12:00:00Z",
            "to": "2026-03-16T12:00:00Z",
            "aggregation": "month",
        },
    ).json()
    year = _ask(
        service,
        "consumption",
        **{
            "from": "2026-06-01T00:00:00Z",
            "to": "2026-06-02T00:00:00Z",
            "aggregation": "year",
        },
    ).json()

    assert (day["from"], day["to"]) == ("2026-03-28T23:00:00Z", "2026-03-29T22:00:00Z")
    assert [r["start"] for r in day["data"]] == ["2026-03-28T23:00:00Z"]
    assert day["data"][0]["end"] == "2026-03-29T22:00:00Z"
    assert (month["from"], month["to"]) == (
        "2026-02-28T23:00:00Z",
        "2026-03-31T22:00:00Z",
    )
    assert (year["from"], year["to"]) == (
        "2025-12-31T23:00:00Z",
        "2026-12-31T23:00:00Z",
    )


def _capped(
    service: MinStroemService, suffix: str, lo: str, hi: str, **interval: str
) -> int:
    return _ask(
        service, suffix, **{"from": _utc(lo), "to": _utc(hi)}, **interval
    ).status_code


@pytest.mark.parametrize(
    ("suffix", "interval", "lo", "ok_hi", "refused_hi"),
    [
        # 366 local days touched, a partial day counting, for hourly and daily data.
        (
            "consumption",
            {"aggregation": "hour"},
            "2026-01-01",
            "2027-01-02",
            "2027-01-02T01:00",
        ),
        (
            "consumption-costs",
            {"aggregation": "day"},
            "2026-01-01",
            "2027-01-02",
            "2027-01-03",
        ),
        (
            "solar-export-revenue",
            {"aggregation": "hour"},
            "2026-01-01",
            "2027-01-02",
            "2027-01-02T01:00",
        ),
        (
            "prices",
            {"resolution": "hour"},
            "2026-01-01",
            "2027-01-02",
            "2027-01-02T01:00",
        ),
        # 90 days for quarter-hours.
        (
            "prices",
            {"resolution": "fifteenMinutes"},
            "2026-01-01",
            "2026-04-01",
            "2026-04-01T00:15",
        ),
        # Five calendar years touched for usage at day and above.
        (
            "consumption",
            {"aggregation": "day"},
            "2021-01-01",
            "2026-01-01",
            "2026-01-02",
        ),
        (
            "solar-export",
            {"aggregation": "month"},
            "2021-01-01",
            "2026-01-01",
            "2026-02-01",
        ),
        (
            "consumption",
            {"aggregation": "year"},
            "2021-01-01",
            "2026-01-01",
            "2027-01-01",
        ),
        # Costs and revenue at month and year are held to 366 days.
        (
            "consumption-costs",
            {"aggregation": "month"},
            "2024-01-01",
            "2025-01-01",
            "2025-02-01",
        ),
        (
            "solar-export-revenue",
            {"aggregation": "year"},
            "2024-01-01",
            "2025-01-01",
            "2026-01-01",
        ),
    ],
)
def test_the_mock_enforces_its_caps_after_expansion(
    suffix: str, interval: dict[str, str], lo: str, ok_hi: str, refused_hi: str
) -> None:
    service = MinStroemService()

    assert _capped(service, suffix, lo, ok_hi, **interval) == 200
    refused = _ask(
        service, suffix, **{"from": _utc(lo), "to": _utc(refused_hi)}, **interval
    )
    assert refused.status_code == 400
    assert _error_code(refused) == "range_too_large"


def test_a_partial_day_counts_as_a_day_for_the_cap() -> None:
    service = MinStroemService()
    # 366 days exactly, then half an hour into a 367th day.
    ok = {"from": _utc("2026-01-01"), "to": _utc("2027-01-02"), "aggregation": "hour"}
    over = {
        "from": _utc("2026-01-01"),
        "to": _utc("2027-01-02T00:30"),
        "aggregation": "hour",
    }
    # A start late in the previous day counts that whole day too.
    late = {
        "from": _utc("2025-12-31T23:30"),
        "to": _utc("2027-01-02"),
        "aggregation": "hour",
    }

    assert _ask(service, "consumption", **ok).status_code == 200
    assert _ask(service, "consumption", **over).status_code == 400
    assert _ask(service, "consumption", **late).status_code == 400


def test_a_yearly_cost_request_crossing_new_year_is_refused() -> None:
    service = MinStroemService()
    params = {
        "from": _utc("2025-01-01"),
        "to": _utc("2027-01-01"),
        "aggregation": "year",
    }

    assert _ask(service, "consumption-costs", **params).status_code == 400


def test_the_mock_answers_404_for_an_unknown_address_or_endpoint() -> None:
    service = MinStroemService()

    unknown = _ask(service, "consumption", address=OTHER, **GOOD)
    endpoint = _ask(service, "nope", **GOOD)

    assert (unknown.status_code, _error_code(unknown)) == (404, "resource_not_found")
    assert (endpoint.status_code, _error_code(endpoint)) == (404, "resource_not_found")


def test_the_mock_answers_422_for_a_capability_the_address_lacks() -> None:
    service = MinStroemService(
        [minstroem_address(solarExport=False, forecastPrices=False)]
    )

    solar = _ask(service, "solar-export", **GOOD)
    forecast = _ask(service, "forecast-prices")
    consumption = _ask(service, "consumption", **GOOD)

    assert (solar.status_code, _error_code(solar)) == (422, "feature_unavailable")
    assert (forecast.status_code, _error_code(forecast)) == (422, "feature_unavailable")
    assert consumption.status_code == 200


def test_the_mock_serves_sparse_rows_it_was_given_filtered_to_the_period() -> None:
    service = MinStroemService()
    service.rows["consumption"] = [
        _usage_row("2026-11-01T22:00:00Z", 9.0),
        _usage_row("2026-11-01T23:00:00Z", 1.0),
        _usage_row("2026-11-02T05:00:00Z", None),
        _usage_row("2026-11-02T23:00:00Z", 9.0),
    ]

    data = _ask(service, "consumption", **GOOD).json()["data"]

    assert [r["start"] for r in data] == [
        "2026-11-01T23:00:00Z",
        "2026-11-02T05:00:00Z",
    ]
    assert data[1]["energyKWh"] is None


def test_the_mock_generates_one_row_per_interval_minus_gaps_unless_told_not_to() -> (
    None
):
    service = MinStroemService()

    full = _ask(service, "consumption", **GOOD).json()["data"]
    service.gaps = {"2026-11-02T00:00:00Z"}
    gapped = _ask(service, "consumption", **GOOD).json()["data"]
    service.generate = False
    none = _ask(service, "consumption", **GOOD).json()["data"]

    assert len(full) == 24
    assert len(gapped) == 23
    assert "2026-11-02T00:00:00Z" not in [r["start"] for r in gapped]
    assert none == []


def test_the_mock_generates_rows_in_the_shape_of_each_endpoint() -> None:
    service = MinStroemService()
    keys = {
        "consumption": {"start", "end", "energyKWh", "isEstimate"},
        "solar-export": {"start", "end", "energyKWh", "isEstimate"},
        "consumption-costs": {
            "start",
            "end",
            "energyKWh",
            "totalPricePerKWh",
            "fixedFeesForInterval",
            "totalCost",
            "isEstimate",
        },
        "solar-export-revenue": {
            "start",
            "end",
            "energyKWh",
            "totalPricePerKWh",
            "fixedFeesForInterval",
            "netRevenue",
            "isEstimate",
        },
    }

    for suffix, expected in keys.items():
        row = _ask(service, suffix, **GOOD).json()["data"][0]
        assert set(row) == expected
    price = _ask(
        service,
        "prices",
        **{"from": GOOD["from"], "to": GOOD["to"]},
        resolution="hour",
    ).json()["data"][0]
    assert set(price) == {"start", "end", "totalPricePerKWh", "variableChargesPerKWh"}


def test_the_mock_reports_currency_and_vat_for_the_money_endpoints_only() -> None:
    service = MinStroemService(currency="SEK")

    costs = _ask(service, "consumption-costs", **GOOD).json()
    revenue = _ask(service, "solar-export-revenue", **GOOD).json()
    usage = _ask(service, "consumption", **GOOD).json()
    forecast = _ask(service, "forecast-prices").json()

    assert (costs["currency"], costs["vatIncluded"]) == ("SEK", True)
    assert (revenue["currency"], revenue["vatIncluded"]) == ("SEK", False)
    assert "currency" not in usage
    assert (forecast["currency"], forecast["vatIncluded"]) == ("SEK", True)
    assert forecast["resolution"] == "hour"
    assert forecast["isEstimate"] is True


def test_the_mock_serves_the_forecast_rows_it_was_given() -> None:
    service = MinStroemService()
    service.forecast = [
        {"start": "2026-11-02T10:00:00Z", "end": "2026-11-02T11:00:00Z",
         "totalPricePerKWh": 1.0, "variableChargesPerKWh": None},
    ]  # fmt: skip

    assert _ask(service, "forecast-prices").json()["data"] == service.forecast


def test_an_override_answers_in_place_of_the_mock_and_skips_the_key_check() -> None:
    service = MinStroemService()
    service.overrides["consumption"] = lambda _r: httpx.Response(418, text="tea")

    response = _ask(service, "consumption", key=None, **GOOD)

    assert response.status_code == 418
    assert response.headers["X-Request-ID"] == minstroem_request_id(1)


def test_the_mock_records_every_request_and_calls_reads_back_by_suffix() -> None:
    service = MinStroemService()

    _ask(service, "")
    _ask(service, "consumption", **GOOD)
    _ask(
        service, "prices", **{"from": GOOD["from"], "to": GOOD["to"]}, resolution="hour"
    )
    _ask(service, "forecast-prices")

    assert len(service.requests) == 4
    assert service.calls("/consumption") == [GOOD]
    assert len(service.calls("/prices")) == 1
    # A bare "prices" would also match "forecast-prices"; the slash is the guard.
    assert len(service.calls("prices")) == 2
    assert service.calls("/me/addresses") == [{}]


def test_the_mock_builds_a_real_client_with_instant_retries() -> None:
    service = MinStroemService()
    service.overrides["addresses"] = lambda _r: httpx.Response(503, json={})

    with service.client() as c, pytest.raises(MinStroemError):
        c.get_json("/me/addresses")

    assert len(service.requests) == 5


def test_the_default_mock_rows_are_deterministic() -> None:
    first = _ask(MinStroemService(), "consumption", **GOOD).json()
    second = _ask(MinStroemService(), "consumption", **GOOD).json()

    assert first == second


def test_the_minstroem_service_fixture_has_one_address_with_every_capability(
    minstroem_service: MinStroemService,
) -> None:
    (address,) = minstroem_service.addresses

    assert address["id"] == ADDRESS_ID
    assert all(address["capabilities"].values())
    assert len(address["capabilities"]) == 6


# --- showcase ------------------------------------------------------------------


def test_the_series_showcase_runs_offline_and_shows_windows_and_a_refusal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(API_KEY_ENV, raising=False)

    _series.main()

    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith(
        "costs per hour: 2 windows over 2025-03-01 to 2026-04-01"
    )
    assert any(
        line.startswith("refused: start must be the first of a month") for line in lines
    )
    assert API_KEY_ENV in lines[-1]


def test_the_series_showcase_adds_one_address_lookup_with_a_key(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    minstroem_service: MinStroemService,
) -> None:
    monkeypatch.setenv(API_KEY_ENV, "test-key")
    monkeypatch.setattr(_series, "MinStroemClient", minstroem_service.client)

    _series.main()

    out = capsys.readouterr().out
    assert "1 address(es), regions ['DK1']" in out
    assert len(minstroem_service.calls("/me/addresses")) == 1
