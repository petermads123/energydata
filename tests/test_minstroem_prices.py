"""Prices: history, the forecast (the primary goal) and the two combined."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple

import httpx
import pandas as pd
import pytest
from conftest import (
    ADDRESS_ID,
    MinStroemService,
    minstroem_address,
    minstroem_price_row,
)

from energydata.minstroem import (
    API_KEY_ENV,
    PRICE_RESOLUTIONS,
    MinStroemClient,
    MinStroemError,
    MissingApiKeyError,
    _series,
    get_forecast_prices,
    get_prices,
    get_prices_with_forecast,
    prices,
)
from energydata.utils import TimeLike

CPH = "Europe/Copenhagen"
FORMAT = "%Y-%m-%dT%H:%M:%SZ"
TOTAL = "total_price_per_kwh"
VARIABLE = "variable_charges_per_kwh"
FLAG = "is_forecast"
NaN = float("nan")

type Row = dict[str, object]


def _local_midnight_on(today: str) -> Callable[[int], pd.Timestamp]:
    def midnight(days: int) -> pd.Timestamp:
        day = pd.Timestamp(today) + pd.DateOffset(days=days)
        return day.tz_localize(CPH).as_unit("ns")

    return midnight


@pytest.fixture(autouse=True)
def _today(monkeypatch: pytest.MonkeyPatch) -> None:
    """Today is 2026-11-02, so confirmed prices stop at local midnight on the 4th."""
    monkeypatch.setattr(prices, "_local_midnight", _local_midnight_on("2026-11-02"))


def _t(text: str) -> pd.Timestamp:
    return pd.Timestamp(text, tz=CPH)


def _hours(start: str, stop: str) -> list[pd.Timestamp]:
    """Every hour of the local period `[start, stop)`, in elapsed time."""
    return list(pd.date_range(_t(start), _t(stop), freq="h", inclusive="left"))


def _value(hour: pd.Timestamp, base: float) -> float:
    """A price that differs for every hour, so a mixed-up hour shows."""
    return round(base + (hour - _t("2026-01-01")).total_seconds() / 3600 / 10_000, 6)


def _confirmed(hours: list[pd.Timestamp]) -> list[Row]:
    """Confirmed rows, with totals near 1."""
    return [_row_of(h, 1.0) for h in hours]


def _row_of(hour: pd.Timestamp, base: float) -> Row:
    return minstroem_price_row(
        hour.to_pydatetime(), _value(hour, base), _value(hour, base) / 10
    )


def _forecast(hours: list[pd.Timestamp]) -> list[Row]:
    """Forecast rows (totals near 9)."""
    return [_row_of(h, 9.0) for h in hours]


def _expected_index(start: str, end: str) -> pd.DatetimeIndex:
    index = pd.date_range(_t(start), _t(end), freq="h", inclusive="left")
    return index.as_unit("ns").rename("time")


def _frame(
    index: pd.DatetimeIndex, rule: Callable[[pd.Timestamp], tuple[float | None, ...]]
) -> pd.DataFrame:
    """The frame the rule describes: `(total, variable, flag)` for each hour."""
    cells = [rule(hour) for hour in index]
    return pd.DataFrame(
        {
            TOTAL: pd.Series([c[0] for c in cells], index=index, dtype="float64"),
            VARIABLE: pd.Series([c[1] for c in cells], index=index, dtype="float64"),
            FLAG: pd.Series(
                pd.array([c[2] for c in cells], dtype="boolean"), index=index
            ),
        }
    )


def _cell(frame: pd.DataFrame, hour: pd.Timestamp) -> dict[str, Any]:
    """The three values of one hour, by column name."""
    return {name: frame.at[hour, name] for name in frame.columns}


def _same(frame: pd.DataFrame, expected: pd.DataFrame) -> None:
    pd.testing.assert_frame_equal(frame, expected, check_freq=False)


def _never_confirmed_after_forecast(frame: pd.DataFrame) -> None:
    seen = False
    for flag in frame[FLAG].tolist():
        if flag is True:
            seen = True
        assert not (seen and flag is False)


def _set_prices(service: MinStroemService, rows: list[Row]) -> None:
    service.rows["prices"] = rows


def _forecast_body(rows: list[Row], **fields: object) -> dict[str, object]:
    body: dict[str, object] = {
        "addressId": ADDRESS_ID,
        "resolution": "hour",
        "isEstimate": True,
        "timeZone": CPH,
        "currency": "DKK",
        "vatIncluded": True,
        "data": rows,
    }
    body.update(fields)
    return body


def _serve_forecast(
    service: MinStroemService, rows: list[Row], **fields: object
) -> None:
    body = _forecast_body(rows, **fields)
    service.overrides["forecast-prices"] = lambda _r: httpx.Response(200, json=body)


def _row_at(
    start: str, end: str, total: float | None = 1.0, var: float | None = 0.1
) -> Row:
    return {
        "start": start,
        "end": end,
        "totalPricePerKWh": total,
        "variableChargesPerKWh": var,
    }


# === get_prices ================================================================


def test_prices_default_request_columns_dtypes_and_attrs(
    minstroem_service: MinStroemService,
) -> None:
    with minstroem_service.client() as c:
        frame = get_prices("2026-11-02", address_id=ADDRESS_ID, client=c)

    assert minstroem_service.calls("/prices") == [
        {
            "from": "2026-11-01T23:00:00Z",
            "to": "2026-11-02T23:00:00Z",
            "resolution": "hour",
        }
    ]
    assert list(frame.columns) == [TOTAL, VARIABLE]
    assert (frame.dtypes == "float64").all()
    assert str(frame.index.dtype) == f"datetime64[ns, {CPH}]"
    assert frame.index.name == "time"
    assert frame.attrs == {
        "address_id": ADDRESS_ID,
        "resolution": "hour",
        "currency": "DKK",
        "vat_included": True,
    }
    assert len(frame) == 24
    assert frame.notna().all().all()
    assert minstroem_service.calls("/forecast-prices") == []


@pytest.mark.parametrize("currency", ["DKK", "SEK", "NOK"])
def test_prices_carry_the_responses_currency(currency: str) -> None:
    service = MinStroemService(currency=currency)

    with service.client() as c:
        frame = get_prices("2026-11-02", address_id=ADDRESS_ID, client=c)

    assert frame.attrs["currency"] == currency


def test_prices_total_and_variable_land_in_their_own_columns(
    minstroem_service: MinStroemService,
) -> None:
    rows = [
        minstroem_price_row(_t("2026-11-02 00:00").to_pydatetime(), 1.234, 0.456),
        minstroem_price_row(_t("2026-11-02 01:00").to_pydatetime(), -0.5, 0.0),
    ]
    _set_prices(minstroem_service, rows)

    with minstroem_service.client() as c:
        frame = get_prices("2026-11-02", "2026-11-02T03:00", client=c)

    assert frame[TOTAL].tolist()[:2] == [1.234, -0.5]
    assert frame[VARIABLE].tolist()[:2] == [0.456, 0.0]
    assert frame[TOTAL].isna().tolist() == [False, False, True]


def test_null_prices_are_nan_not_an_error(minstroem_service: MinStroemService) -> None:
    rows = [
        minstroem_price_row(_t("2026-11-02 00:00").to_pydatetime(), None, None),
        minstroem_price_row(_t("2026-11-02 01:00").to_pydatetime(), 1.234, None),
        minstroem_price_row(_t("2026-11-02 02:00").to_pydatetime(), None, 0.2),
    ]
    _set_prices(minstroem_service, rows)

    with minstroem_service.client() as c:
        frame = get_prices("2026-11-02", "2026-11-02T04:00", client=c)

    assert pd.isna(frame[TOTAL].iloc[0]) and pd.isna(frame[VARIABLE].iloc[0])
    assert frame[TOTAL].iloc[1] == 1.234 and pd.isna(frame[VARIABLE].iloc[1])
    assert pd.isna(frame[TOTAL].iloc[2]) and frame[VARIABLE].iloc[2] == 0.2
    assert frame.iloc[3].isna().all()


def test_prices_with_no_rows_are_all_nan_never_zero(
    minstroem_service: MinStroemService,
) -> None:
    _set_prices(minstroem_service, [])

    with minstroem_service.client() as c:
        frame = get_prices("2026-11-02", client=c)

    assert len(frame) == 24
    assert frame.isna().all().all()
    assert (frame.dtypes == "float64").all()


@pytest.mark.parametrize(
    ("day", "rows"), [("2026-03-29", 92), ("2026-10-25", 100), ("2026-11-02", 96)]
)
def test_quarter_hours_follow_the_danish_calendar(
    minstroem_service: MinStroemService, day: str, rows: int
) -> None:
    with minstroem_service.client() as c:
        frame = get_prices(
            day, resolution="fifteenMinutes", address_id=ADDRESS_ID, client=c
        )

    assert len(frame) == rows
    steps = {b - a for a, b in zip(frame.index, frame.index[1:], strict=False)}
    assert steps == {pd.Timedelta(minutes=15)}
    assert frame.attrs["resolution"] == "fifteenMinutes"
    assert minstroem_service.calls("/prices")[0]["resolution"] == "fifteenMinutes"
    assert frame.notna().all().all()


@pytest.mark.parametrize(
    ("day", "rows"), [("2026-03-29", 23), ("2026-10-25", 25), ("2026-11-02", 24)]
)
def test_hours_follow_the_danish_calendar(
    minstroem_service: MinStroemService, day: str, rows: int
) -> None:
    with minstroem_service.client() as c:
        frame = get_prices(day, address_id=ADDRESS_ID, client=c)

    assert len(frame) == rows
    assert frame.index.is_unique


def test_a_lone_quarter_hour_timestamp_is_one_row_for_quarters_and_refused_for_hours(
    minstroem_service: MinStroemService,
) -> None:
    with minstroem_service.client() as c:
        frame = get_prices("2026-11-02T10:15", resolution="fifteenMinutes", client=c)
        with pytest.raises(ValueError):
            get_prices("2026-11-02T10:15", client=c)

    assert len(frame) == 1


def test_the_resolution_defaults_to_hour_and_is_keyword_only(
    minstroem_service: MinStroemService,
) -> None:
    with minstroem_service.client() as c:
        with pytest.raises(TypeError):
            get_prices("2026-11-02", "2026-11-03", "hour", client=c)  # type: ignore[misc]  # keyword-only
        frame = get_prices("2026-11-02", client=c)

    assert frame.attrs["resolution"] == "hour"
    assert PRICE_RESOLUTIONS == ("hour", "fifteenMinutes")


@pytest.mark.parametrize(
    ("start", "end", "windows"),
    [
        ("2026-01-01", "2026-03-31", 1),  # 89 days: one window
        ("2026-01-01", "2026-04-01", 2),  # 90 days: the 89-day window and one more
        ("2026-01-01", "2026-06-01", 2),
        ("2026-01-01", "2026-07-01", 3),
    ],
)
def test_quarter_hours_are_requested_in_windows_of_at_most_89_days(
    minstroem_service: MinStroemService, start: str, end: str, windows: int
) -> None:
    with minstroem_service.client() as c:
        frame = get_prices(
            start, end, resolution="fifteenMinutes", address_id=ADDRESS_ID, client=c
        )

    calls = sorted(minstroem_service.calls("/prices"), key=lambda q: q["from"])
    assert len(calls) == windows
    assert frame.index.is_unique
    assert frame[TOTAL].notna().all()


def test_an_unknown_currency_in_a_prices_response_is_a_minstroem_error(
    minstroem_service: MinStroemService,
) -> None:
    body = {"data": [], "currency": "EUR", "vatIncluded": True}
    minstroem_service.overrides["prices"] = lambda _r: httpx.Response(200, json=body)

    with minstroem_service.client() as c, pytest.raises(MinStroemError):
        get_prices("2026-11-02", address_id=ADDRESS_ID, client=c)


# === get_forecast_prices (A1) ===================================================


def _utc_row(start: str, total: float | None = 1.0, var: float | None = 0.1) -> Row:
    """A forecast row from the API text `start`, one hour long."""
    begin = datetime.strptime(start, FORMAT).replace(tzinfo=UTC)
    return _row_at(start, (begin + timedelta(hours=1)).strftime(FORMAT), total, var)


def test_the_forecast_is_an_hourly_frame_with_gaps_and_nulls_as_nan(
    minstroem_service: MinStroemService,
) -> None:
    # Deliberately unordered: an interior hour is missing, one total and one
    # variable charge are null.
    minstroem_service.forecast = [
        minstroem_price_row(_t("2026-11-02 15:00").to_pydatetime(), 1.7, None),
        minstroem_price_row(_t("2026-11-02 12:00").to_pydatetime(), 1.5, 0.3),
        minstroem_price_row(_t("2026-11-02 13:00").to_pydatetime(), None, 0.3),
    ]

    with minstroem_service.client() as c:
        frame = get_forecast_prices(address_id=ADDRESS_ID, client=c)

    expected = pd.date_range(_t("2026-11-02 12:00"), periods=4, freq="h").as_unit("ns")
    assert frame.index.equals(expected)
    assert frame.index.name == "time"
    assert str(frame.index.dtype) == f"datetime64[ns, {CPH}]"
    assert list(frame.columns) == [TOTAL, VARIABLE]
    assert (frame.dtypes == "float64").all()
    assert frame[TOTAL].tolist()[0] == 1.5 and frame[TOTAL].tolist()[3] == 1.7
    assert frame[TOTAL].isna().tolist() == [False, True, True, False]
    assert frame[VARIABLE].tolist()[:2] == [0.3, 0.3]
    assert frame[VARIABLE].isna().tolist() == [False, False, True, True]


def test_the_forecast_attrs_are_exactly_the_documented_four(
    minstroem_service: MinStroemService,
) -> None:
    minstroem_service.forecast = _forecast(
        _hours("2026-11-02 12:00", "2026-11-02 14:00")
    )
    before = pd.Timestamp.now(tz=CPH)

    with minstroem_service.client() as c:
        frame = get_forecast_prices(address_id=ADDRESS_ID, client=c)

    after = pd.Timestamp.now(tz=CPH)
    assert set(frame.attrs) == {"address_id", "currency", "vat_included", "fetched_at"}
    assert frame.attrs["address_id"] == ADDRESS_ID
    assert frame.attrs["currency"] == "DKK"
    assert frame.attrs["vat_included"] is True
    fetched = frame.attrs["fetched_at"]
    assert isinstance(fetched, pd.Timestamp)
    assert fetched.tzinfo is not None
    assert str(fetched.tz) == CPH
    assert before <= fetched <= after


@pytest.mark.parametrize("currency", ["SEK", "NOK"])
def test_the_forecast_carries_the_responses_currency(currency: str) -> None:
    service = MinStroemService(currency=currency)
    service.forecast = _forecast(_hours("2026-11-02 12:00", "2026-11-02 13:00"))

    with service.client() as c:
        frame = get_forecast_prices(address_id=ADDRESS_ID, client=c)

    assert frame.attrs["currency"] == currency


def test_an_empty_forecast_is_the_documented_empty_frame(
    minstroem_service: MinStroemService,
) -> None:
    minstroem_service.forecast = []

    with minstroem_service.client() as c:
        frame = get_forecast_prices(address_id=ADDRESS_ID, client=c)

    assert len(frame) == 0
    assert list(frame.columns) == [TOTAL, VARIABLE]
    assert (frame.dtypes == "float64").all()
    assert isinstance(frame.index, pd.DatetimeIndex)
    assert str(frame.index.dtype) == f"datetime64[ns, {CPH}]"
    assert frame.index.name == "time"
    assert set(frame.attrs) == {"address_id", "currency", "vat_included", "fetched_at"}
    assert frame.attrs["currency"] == "DKK"
    assert frame.attrs["fetched_at"].tzinfo is not None


def test_a_single_forecast_row_is_a_one_row_frame(
    minstroem_service: MinStroemService,
) -> None:
    minstroem_service.forecast = [_utc_row("2026-11-02T10:00:00Z", 2.5, 0.5)]

    with minstroem_service.client() as c:
        frame = get_forecast_prices(address_id=ADDRESS_ID, client=c)

    assert len(frame) == 1
    assert frame.index[0] == _t("2026-11-02 11:00")
    assert frame[TOTAL].iloc[0] == 2.5 and frame[VARIABLE].iloc[0] == 0.5


def test_the_forecast_rows_come_out_as_the_api_sent_them_not_trimmed_to_now(
    minstroem_service: MinStroemService,
) -> None:
    # A first row in the past is kept: the frame is the API's rows, no more.
    minstroem_service.forecast = _forecast(
        _hours("2020-01-01 00:00", "2020-01-01 03:00")
    )

    with minstroem_service.client() as c:
        frame = get_forecast_prices(address_id=ADDRESS_ID, client=c)

    assert len(frame) == 3
    assert frame.index[0] == _t("2020-01-01 00:00")


def test_forecast_hours_across_the_autumn_change_are_25_and_not_duplicates(
    minstroem_service: MinStroemService,
) -> None:
    minstroem_service.forecast = _forecast(_hours("2026-10-25", "2026-10-26"))

    with minstroem_service.client() as c:
        frame = get_forecast_prices(address_id=ADDRESS_ID, client=c)

    assert len(frame) == 25
    assert frame.index.is_unique
    assert frame.index[2].utcoffset() == timedelta(hours=2)
    assert frame.index[3].utcoffset() == timedelta(hours=1)
    assert frame[TOTAL].notna().all()
    assert frame[TOTAL].iloc[2] != frame[TOTAL].iloc[3]


def test_forecast_hours_across_the_spring_change_are_23_with_no_hour_2(
    minstroem_service: MinStroemService,
) -> None:
    minstroem_service.forecast = _forecast(_hours("2026-03-29", "2026-03-30"))

    with minstroem_service.client() as c:
        frame = get_forecast_prices(address_id=ADDRESS_ID, client=c)

    assert len(frame) == 23
    assert 2 not in list(pd.DatetimeIndex(frame.index).hour)


def test_a_null_price_field_is_nan_with_the_row_kept(
    minstroem_service: MinStroemService,
) -> None:
    minstroem_service.forecast = [
        _utc_row("2026-11-02T10:00:00Z", None, 0.1),
        _utc_row("2026-11-02T11:00:00Z", 1.0, None),
        _utc_row("2026-11-02T12:00:00Z", None, None),
    ]

    with minstroem_service.client() as c:
        frame = get_forecast_prices(address_id=ADDRESS_ID, client=c)

    assert len(frame) == 3
    assert frame[TOTAL].isna().tolist() == [True, False, True]
    assert frame[VARIABLE].isna().tolist() == [False, True, True]


def test_a_negative_forecast_price_is_data(minstroem_service: MinStroemService) -> None:
    minstroem_service.forecast = [_utc_row("2026-11-02T10:00:00Z", -0.25, 0.1)]

    with minstroem_service.client() as c:
        frame = get_forecast_prices(address_id=ADDRESS_ID, client=c)

    assert frame[TOTAL].iloc[0] == -0.25


@pytest.mark.parametrize(
    "rows",
    [
        [_row_at("2026-11-02T12:30:00Z", "2026-11-02T13:30:00Z")],
        [_row_at("2026-11-02T12:00:00Z", "2026-11-02T14:00:00Z")],
        [_row_at("2026-11-02T12:00:00Z", "2026-11-02T12:15:00Z")],
        [_row_at("2026-11-02T12:00:00Z", "2026-11-02T12:00:00Z")],
        [_row_at("2026-11-02T13:00:00Z", "2026-11-02T12:00:00Z")],
        [
            _row_at("2026-11-02T12:00:00Z", "2026-11-02T13:00:00Z"),
            _row_at("2026-11-02T12:00:00Z", "2026-11-02T13:00:00Z"),
        ],
        [_row_at("2026-11-02T12:00:00+00:30", "2026-11-02T13:00:00+00:30")],
    ],
    ids=[
        "half-hour",
        "two-hours",
        "quarter",
        "zero",
        "negative",
        "duplicate",
        "offset",
    ],
)
def test_a_row_that_is_not_one_whole_hour_or_repeats_is_a_minstroem_error(
    minstroem_service: MinStroemService, rows: list[Row]
) -> None:
    _serve_forecast(minstroem_service, rows)

    with minstroem_service.client() as c, pytest.raises(MinStroemError) as caught:
        get_forecast_prices(address_id=ADDRESS_ID, client=c)

    assert caught.value.status is None


@pytest.mark.parametrize(
    "fields",
    [
        {"resolution": "fifteenMinutes"},
        {"resolution": None},
        {"resolution": "Hour"},
        {"currency": "EUR"},
        {"currency": None},
        {"vatIncluded": "true"},
        {"vatIncluded": None},
        {"data": None},
        {"data": {}},
    ],
    ids=repr,
)
def test_a_forecast_response_with_the_wrong_resolution_currency_or_shape_is_an_error(
    minstroem_service: MinStroemService, fields: dict[str, object]
) -> None:
    _serve_forecast(minstroem_service, [_utc_row("2026-11-02T10:00:00Z")], **fields)

    with minstroem_service.client() as c, pytest.raises(MinStroemError) as caught:
        get_forecast_prices(address_id=ADDRESS_ID, client=c)

    assert caught.value.status is None


@pytest.mark.parametrize("missing", ["resolution", "currency", "vatIncluded", "data"])
def test_a_forecast_response_missing_a_required_key_is_a_minstroem_error(
    minstroem_service: MinStroemService, missing: str
) -> None:
    body = _forecast_body([_utc_row("2026-11-02T10:00:00Z")])
    del body[missing]
    minstroem_service.overrides["forecast-prices"] = lambda _r: httpx.Response(
        200, json=body
    )

    with minstroem_service.client() as c, pytest.raises(MinStroemError):
        get_forecast_prices(address_id=ADDRESS_ID, client=c)


def test_a_forecast_row_with_a_missing_price_key_is_a_minstroem_error(
    minstroem_service: MinStroemService,
) -> None:
    row = _utc_row("2026-11-02T10:00:00Z")
    del row["variableChargesPerKWh"]
    _serve_forecast(minstroem_service, [row])

    with minstroem_service.client() as c, pytest.raises(MinStroemError) as caught:
        get_forecast_prices(address_id=ADDRESS_ID, client=c)

    assert "variableChargesPerKWh" in str(caught.value)


def test_the_forecast_request_is_one_get_with_no_query(
    minstroem_service: MinStroemService,
) -> None:
    with minstroem_service.client() as c:
        get_forecast_prices(address_id=ADDRESS_ID, client=c)

    assert minstroem_service.calls("/forecast-prices") == [{}]
    (request,) = minstroem_service.requests
    assert request.method == "GET"
    assert request.url.path == f"/consumer/v1/me/addresses/{ADDRESS_ID}/forecast-prices"


def test_the_forecast_capability_is_named_whichever_route_finds_it_missing() -> None:
    service = MinStroemService([minstroem_address(forecastPrices=False)])

    with service.client() as c:
        with pytest.raises(MinStroemError) as looked_up:
            get_forecast_prices(client=c)
        looked_up_requests = len(service.requests)
        with pytest.raises(MinStroemError) as explicit:
            get_forecast_prices(address_id=ADDRESS_ID, client=c)

    assert "'forecastPrices'" in str(looked_up.value)
    assert looked_up.value.status is None
    assert looked_up_requests == 1
    assert explicit.value.status == 422
    assert explicit.value.error_code == "feature_unavailable"
    assert "the address lacks the 'forecastPrices' capability" in str(explicit.value)


def test_the_forecast_is_idempotent_and_fetched_at_never_goes_back(
    minstroem_service: MinStroemService,
) -> None:
    minstroem_service.forecast = _forecast(
        _hours("2026-11-02 12:00", "2026-11-02 16:00")
    )

    with minstroem_service.client() as c:
        first = get_forecast_prices(address_id=ADDRESS_ID, client=c)
        second = get_forecast_prices(address_id=ADDRESS_ID, client=c)

    pd.testing.assert_frame_equal(first, second)
    assert first.attrs["fetched_at"] <= second.attrs["fetched_at"]


def test_the_forecast_client_is_keyword_only(
    minstroem_service: MinStroemService,
) -> None:
    with minstroem_service.client() as c, pytest.raises(TypeError):
        get_forecast_prices(ADDRESS_ID, client=c)  # type: ignore[misc]  # keyword-only


# === get_prices_with_forecast (A2) ============================================
#
# Fixture used below, unless a test says otherwise (all local time, today is
# 2026-11-02, the cap on confirmed requests is 2026-11-04 00:00):
#   confirmed  2026-11-01 00:00 .. 2026-11-02 13:00   (totals near 1)
#   forecast   2026-11-02 10:00 .. 2026-11-03 23:00   (totals near 9)


@pytest.fixture
def stitched(minstroem_service: MinStroemService) -> MinStroemService:
    _set_prices(minstroem_service, _confirmed(_hours("2026-11-01", "2026-11-02 14:00")))
    minstroem_service.forecast = _forecast(_hours("2026-11-02 10:00", "2026-11-04"))
    return minstroem_service


def _confirmed_cell(h: pd.Timestamp) -> tuple[float | None, ...]:
    return (_value(h, 1.0), _value(h, 1.0) / 10, False)


def _forecast_cell(h: pd.Timestamp) -> tuple[float | None, ...]:
    return (_value(h, 9.0), _value(h, 9.0) / 10, True)


def _neither(_h: pd.Timestamp) -> tuple[float | None, ...]:
    return (None, None, None)


def test_confirmed_then_forecast_with_the_flag_and_confirmed_winning_the_overlap(
    stitched: MinStroemService,
) -> None:
    with stitched.client() as c:
        frame = get_prices_with_forecast(
            "2026-11-02", "2026-11-03", address_id=ADDRESS_ID, client=c
        )

    last_confirmed = _t("2026-11-02 13:00")
    expected = _frame(
        _expected_index("2026-11-02", "2026-11-03"),
        lambda h: _confirmed_cell(h) if h <= last_confirmed else _forecast_cell(h),
    )
    _same(frame, expected)
    # 10:00-13:00 have both a confirmed and a forecast row: confirmed wins.
    assert frame[FLAG].tolist()[10:14] == [False] * 4
    assert frame[TOTAL].iloc[10] == _value(_t("2026-11-02 10:00"), 1.0)
    assert frame[FLAG].tolist()[14:] == [True] * 10


def test_the_combined_columns_dtypes_index_and_attrs(
    stitched: MinStroemService,
) -> None:
    before = pd.Timestamp.now(tz=CPH)

    with stitched.client() as c:
        frame = get_prices_with_forecast(
            "2026-11-02", "2026-11-03", address_id=ADDRESS_ID, client=c
        )

    assert list(frame.columns) == [TOTAL, VARIABLE, FLAG]
    assert str(frame[TOTAL].dtype) == "float64"
    assert str(frame[VARIABLE].dtype) == "float64"
    assert str(frame[FLAG].dtype) == "boolean"
    assert str(frame.index.dtype) == f"datetime64[ns, {CPH}]"
    assert frame.index.name == "time"
    assert set(frame.attrs) == {"address_id", "currency", "vat_included", "fetched_at"}
    assert frame.attrs["address_id"] == ADDRESS_ID
    assert frame.attrs["currency"] == "DKK"
    assert frame.attrs["vat_included"] is True
    assert before <= frame.attrs["fetched_at"] <= pd.Timestamp.now(tz=CPH)


def test_a_confirmed_gap_before_the_last_confirmed_hour_is_not_filled_from_the_forecast(
    stitched: MinStroemService,
) -> None:
    confirmed = [
        r
        for r in _confirmed(_hours("2026-11-01", "2026-11-02 14:00"))
        if r["start"] != _t("2026-11-02 05:00").tz_convert("UTC").strftime(FORMAT)
    ]
    _set_prices(stitched, confirmed)
    stitched.forecast = _forecast(_hours("2026-11-02 00:00", "2026-11-04"))

    with stitched.client() as c:
        frame = get_prices_with_forecast("2026-11-02", "2026-11-03", client=c)

    gap = _t("2026-11-02 05:00")
    expected = _frame(
        _expected_index("2026-11-02", "2026-11-03"),
        lambda h: (
            _neither(h)
            if h == gap
            else _confirmed_cell(h)
            if h <= _t("2026-11-02 13:00")
            else _forecast_cell(h)
        ),
    )
    _same(frame, expected)


def test_confirmed_prices_are_never_requested_beyond_local_midnight_two_days_ahead(
    stitched: MinStroemService,
) -> None:
    with stitched.client() as c:
        frame = get_prices_with_forecast(
            "2026-11-02", "2026-11-07", address_id=ADDRESS_ID, client=c
        )

    assert len(frame) == 120
    sent = stitched.calls("/prices")
    assert max(q["to"] for q in sent) == "2026-11-03T23:00:00Z"
    assert all(q["to"] <= "2026-11-03T23:00:00Z" for q in sent)
    assert all(q["resolution"] == "hour" for q in sent)
    # The forecast runs to 2026-11-03 23:00; later hours have neither source.
    later = frame.loc[_t("2026-11-04") :]
    assert len(later) == 72
    assert later[[TOTAL, VARIABLE]].isna().all().all()
    assert later[FLAG].isna().all()


def test_a_period_wholly_beyond_the_cap_is_all_forecast_with_no_prices_request(
    stitched: MinStroemService,
) -> None:
    stitched.forecast = _forecast(_hours("2026-11-02 10:00", "2026-11-05"))

    with stitched.client() as c:
        frame = get_prices_with_forecast(
            "2026-11-04", "2026-11-05", address_id=ADDRESS_ID, client=c
        )

    assert stitched.calls("/prices") == []
    _same(frame, _frame(_expected_index("2026-11-04", "2026-11-05"), _forecast_cell))


def test_a_period_straddling_the_cap_requests_only_the_hours_before_it() -> None:
    service = MinStroemService()
    service.forecast = _forecast(_hours("2026-11-03 20:00", "2026-11-05"))

    with service.client() as c:
        frame = get_prices_with_forecast(
            "2026-11-03T23:00", "2026-11-04T02:00", address_id=ADDRESS_ID, client=c
        )

    assert service.calls("/prices") == [
        {
            "from": "2026-11-03T22:00:00Z",
            "to": "2026-11-03T23:00:00Z",
            "resolution": "hour",
        }
    ]
    assert frame[FLAG].tolist() == [False, True, True]


def test_without_an_end_it_runs_to_the_last_forecast_hour(
    stitched: MinStroemService,
) -> None:
    with stitched.client() as c:
        frame = get_prices_with_forecast("2026-11-02", address_id=ADDRESS_ID, client=c)

    # A lone date is not "the whole day" here: the frame runs to the forecast's end.
    assert len(frame) == 48
    assert frame.index[0] == _t("2026-11-02 00:00")
    assert frame.index[-1] == _t("2026-11-03 23:00")
    assert frame[FLAG].iloc[-1]
    assert frame[FLAG].iloc[0] == False  # noqa: E712 - a boolean-array compare


def test_without_an_end_the_frame_stops_at_the_last_forecast_hour_even_with_later_confirmed_rows(
    minstroem_service: MinStroemService,
) -> None:
    # A2 as written: "without end it runs to the last forecast hour".
    _set_prices(minstroem_service, _confirmed(_hours("2026-11-02", "2026-11-04")))
    minstroem_service.forecast = _forecast(
        _hours("2026-11-02 10:00", "2026-11-02 14:00")
    )

    with minstroem_service.client() as c:
        frame = get_prices_with_forecast("2026-11-02", client=c)

    assert frame.index[-1] == _t("2026-11-02 13:00")
    assert len(frame) == 14
    assert frame[FLAG].tolist() == [False] * 14


def test_with_an_end_the_frame_is_exactly_the_period_and_the_end_hour_is_excluded(
    stitched: MinStroemService,
) -> None:
    with stitched.client() as c:
        frame = get_prices_with_forecast(
            "2026-11-02T06:00", "2026-11-02T20:00", address_id=ADDRESS_ID, client=c
        )

    assert frame.index[0] == _t("2026-11-02 06:00")
    assert frame.index[-1] == _t("2026-11-02 19:00")
    assert len(frame) == 14


def test_a_forecast_that_ends_before_the_start_gives_the_empty_frame_and_no_request(
    stitched: MinStroemService,
) -> None:
    with stitched.client() as c:
        frame = get_prices_with_forecast("2026-11-05", address_id=ADDRESS_ID, client=c)

    assert len(frame) == 0
    assert list(frame.columns) == [TOTAL, VARIABLE, FLAG]
    assert [str(d) for d in frame.dtypes] == ["float64", "float64", "boolean"]
    assert isinstance(frame.index, pd.DatetimeIndex)
    assert str(frame.index.dtype) == f"datetime64[ns, {CPH}]"
    assert frame.index.name == "time"
    assert set(frame.attrs) == {"address_id", "currency", "vat_included", "fetched_at"}
    assert stitched.calls("/prices") == []


def test_an_end_equal_to_the_computed_end_is_empty_and_one_hour_later_is_one_row(
    minstroem_service: MinStroemService,
) -> None:
    minstroem_service.forecast = _forecast(
        _hours("2026-11-02 10:00", "2026-11-02 12:00")
    )
    _set_prices(minstroem_service, [])

    with minstroem_service.client() as c:
        empty = get_prices_with_forecast("2026-11-02T12:00", client=c)
        one = get_prices_with_forecast("2026-11-02T11:00", client=c)

    assert len(empty) == 0
    assert len(one) == 1
    assert one[FLAG].iloc[0]


def test_an_empty_forecast_runs_to_the_confirmed_cap(
    minstroem_service: MinStroemService,
) -> None:
    _set_prices(minstroem_service, _confirmed(_hours("2026-11-02", "2026-11-02 14:00")))
    minstroem_service.forecast = []

    with minstroem_service.client() as c:
        frame = get_prices_with_forecast("2026-11-02", address_id=ADDRESS_ID, client=c)

    assert len(frame) == 48
    assert frame.index[-1] == _t("2026-11-03 23:00")
    assert frame[FLAG].tolist()[:14] == [False] * 14
    assert frame[FLAG].iloc[14:].isna().all()
    assert frame[TOTAL].iloc[14:].isna().all()


def test_an_empty_forecast_and_a_start_at_the_cap_is_the_empty_frame_without_a_request(
    minstroem_service: MinStroemService,
) -> None:
    minstroem_service.forecast = []

    with minstroem_service.client() as c:
        frame = get_prices_with_forecast("2026-11-04", address_id=ADDRESS_ID, client=c)

    assert len(frame) == 0
    assert minstroem_service.calls("/prices") == []
    assert frame.attrs["currency"] == "DKK"


def test_with_no_confirmed_rows_every_forecast_hour_is_forecast(
    minstroem_service: MinStroemService,
) -> None:
    _set_prices(minstroem_service, [])
    minstroem_service.forecast = _forecast(
        _hours("2026-11-02 10:00", "2026-11-02 14:00")
    )

    with minstroem_service.client() as c:
        frame = get_prices_with_forecast(
            "2026-11-02T08:00", "2026-11-02T16:00", client=c
        )

    _same(
        frame,
        _frame(
            _expected_index("2026-11-02 08:00", "2026-11-02 16:00"),
            lambda h: (
                _forecast_cell(h)
                if _t("2026-11-02 10:00") <= h < _t("2026-11-02 14:00")
                else _neither(h)
            ),
        ),
    )


def test_a_forecast_gap_after_the_last_confirmed_hour_is_neither(
    stitched: MinStroemService,
) -> None:
    gap = _t("2026-11-03 03:00")
    stitched.forecast = [
        r
        for r in _forecast(_hours("2026-11-02 10:00", "2026-11-04"))
        if r["start"] != gap.tz_convert("UTC").strftime(FORMAT)
    ]

    with stitched.client() as c:
        frame = get_prices_with_forecast("2026-11-02", "2026-11-04", client=c)

    cell = _cell(frame, gap)
    assert pd.isna(cell[TOTAL]) and pd.isna(cell[VARIABLE])
    assert pd.isna(cell[FLAG])
    assert frame[FLAG].loc[_t("2026-11-03 02:00")]
    assert frame[FLAG].loc[_t("2026-11-03 04:00")]
    _never_confirmed_after_forecast(frame)


def test_forecast_rows_outside_the_period_are_dropped(
    minstroem_service: MinStroemService,
) -> None:
    _set_prices(minstroem_service, [])
    minstroem_service.forecast = _forecast(_hours("2026-11-02", "2026-11-04"))

    with minstroem_service.client() as c:
        frame = get_prices_with_forecast(
            "2026-11-03T06:00", "2026-11-03T12:00", client=c
        )

    assert len(frame) == 6
    _same(
        frame,
        _frame(_expected_index("2026-11-03 06:00", "2026-11-03 12:00"), _forecast_cell),
    )


@pytest.mark.parametrize(("day", "hours"), [("2026-10-25", 25), ("2026-03-29", 23)])
def test_a_dst_day_is_25_or_23_confirmed_hours(
    monkeypatch: pytest.MonkeyPatch, day: str, hours: int
) -> None:
    monkeypatch.setattr(prices, "_local_midnight", _local_midnight_on(day))
    service = MinStroemService()
    next_day = (pd.Timestamp(day) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    with service.client() as c:
        frame = get_prices_with_forecast(day, next_day, address_id=ADDRESS_ID, client=c)

    assert len(frame) == hours
    assert frame.index.is_unique
    assert frame[FLAG].tolist() == [False] * hours
    assert frame[TOTAL].notna().all()
    assert len(service.calls("/prices")) == 1
    if hours == 25:
        assert frame[TOTAL].iloc[2] != frame[TOTAL].iloc[3]


def test_a_long_confirmed_period_is_windowed_like_get_prices(
    minstroem_service: MinStroemService,
) -> None:
    minstroem_service.forecast = _forecast(_hours("2026-11-02 10:00", "2026-11-04"))

    with minstroem_service.client() as c:
        frame = get_prices_with_forecast(
            "2025-01-01", "2026-11-02", address_id=ADDRESS_ID, client=c
        )

    assert len(minstroem_service.calls("/prices")) == 2
    assert (
        len(frame)
        == (pd.Timestamp("2026-11-02") - pd.Timestamp("2025-01-01")).days * 24
    )
    assert frame[FLAG].tolist() == [False] * len(frame)


# --- null prices (A2: "confirmed wherever they exist") --------------------------


def test_a_forecast_row_with_a_null_total_is_forecast_with_a_nan_total(
    stitched: MinStroemService,
) -> None:
    row_time = _t("2026-11-02 15:00")
    forecast = _forecast(_hours("2026-11-02 10:00", "2026-11-04"))
    for row in forecast:
        if row["start"] == row_time.tz_convert("UTC").strftime(FORMAT):
            row["totalPricePerKWh"] = None
    stitched.forecast = forecast

    with stitched.client() as c:
        frame = get_prices_with_forecast("2026-11-02", "2026-11-03", client=c)

    cell = _cell(frame, row_time)
    assert pd.isna(cell[TOTAL])
    assert cell[VARIABLE] == _value(row_time, 9.0) / 10
    assert cell[FLAG]


@pytest.mark.parametrize(
    ("total", "variable"), [(None, 0.3), (None, None)], ids=["total-null", "both-null"]
)
def test_a_confirmed_row_of_nulls_is_still_the_confirmed_hour_and_wins_the_overlap(
    stitched: MinStroemService, total: float | None, variable: float | None
) -> None:
    last = _t("2026-11-02 13:00")
    confirmed = _confirmed(_hours("2026-11-01", "2026-11-02 14:00"))
    confirmed[-1] = minstroem_price_row(last.to_pydatetime(), total, variable)
    _set_prices(stitched, confirmed)

    with stitched.client() as c:
        frame = get_prices_with_forecast("2026-11-02", "2026-11-03", client=c)

    cell = _cell(frame, last)
    assert cell[FLAG] == False  # noqa: E712 - a boolean-array compare
    assert pd.isna(cell[TOTAL])
    if variable is None:
        assert pd.isna(cell[VARIABLE])
    else:
        assert cell[VARIABLE] == variable
    # The forecast starts the hour after the last confirmed hour.
    assert frame[FLAG].loc[_t("2026-11-02 14:00")]
    _never_confirmed_after_forecast(frame)


def test_a_confirmed_row_of_nulls_in_the_middle_is_confirmed_nan_not_forecast(
    stitched: MinStroemService,
) -> None:
    middle = _t("2026-11-02 11:00")
    confirmed = _confirmed(_hours("2026-11-01", "2026-11-02 14:00"))
    for row in confirmed:
        if row["start"] == middle.tz_convert("UTC").strftime(FORMAT):
            row["totalPricePerKWh"] = None
            row["variableChargesPerKWh"] = None
    _set_prices(stitched, confirmed)

    with stitched.client() as c:
        frame = get_prices_with_forecast("2026-11-02", "2026-11-03", client=c)

    cell = _cell(frame, middle)
    assert cell[FLAG] == False  # noqa: E712 - a boolean-array compare
    assert pd.isna(cell[TOTAL]) and pd.isna(cell[VARIABLE])


def test_a_null_variable_charge_on_a_confirmed_hour_is_not_filled_from_the_forecast(
    stitched: MinStroemService,
) -> None:
    hour = _t("2026-11-02 11:00")
    confirmed = _confirmed(_hours("2026-11-01", "2026-11-02 14:00"))
    for row in confirmed:
        if row["start"] == hour.tz_convert("UTC").strftime(FORMAT):
            row["variableChargesPerKWh"] = None
    _set_prices(stitched, confirmed)

    with stitched.client() as c:
        frame = get_prices_with_forecast("2026-11-02", "2026-11-03", client=c)

    cell = _cell(frame, hour)
    assert cell[TOTAL] == _value(hour, 1.0)
    assert pd.isna(cell[VARIABLE])
    assert cell[FLAG] == False  # noqa: E712 - a boolean-array compare


# --- the two sources, capabilities and validation --------------------------------


@pytest.mark.parametrize(
    "fields",
    [
        {"currency": "SEK"},
        {"vatIncluded": False},
        {"currency": "NOK", "vatIncluded": False},
    ],
    ids=repr,
)
@pytest.mark.parametrize("forecast_rows", [True, False], ids=["rows", "no-rows"])
def test_sources_that_disagree_on_currency_or_vat_are_an_error(
    stitched: MinStroemService, fields: dict[str, object], forecast_rows: bool
) -> None:
    rows = _forecast(_hours("2026-11-02 10:00", "2026-11-04")) if forecast_rows else []
    _serve_forecast(stitched, rows, **fields)

    with stitched.client() as c, pytest.raises(MinStroemError) as caught:
        get_prices_with_forecast("2026-11-02", "2026-11-03", client=c)

    assert "disagree" in str(caught.value)
    assert caught.value.status is None


def test_no_comparison_is_made_when_no_confirmed_prices_were_requested(
    stitched: MinStroemService,
) -> None:
    _serve_forecast(
        stitched, _forecast(_hours("2026-11-02 10:00", "2026-11-05")), currency="SEK"
    )

    with stitched.client() as c:
        frame = get_prices_with_forecast("2026-11-04", "2026-11-05", client=c)

    assert frame.attrs["currency"] == "SEK"
    assert stitched.calls("/prices") == []


def test_the_forecast_capability_error_stops_the_combined_series_on_either_route() -> (
    None
):
    lacking = MinStroemService([minstroem_address(forecastPrices=False)])

    with lacking.client() as c:
        with pytest.raises(MinStroemError) as looked_up:
            get_prices_with_forecast("2026-11-02", "2026-11-03", client=c)
        with pytest.raises(MinStroemError) as explicit:
            get_prices_with_forecast(
                "2026-11-02", "2026-11-03", address_id=ADDRESS_ID, client=c
            )

    assert "'forecastPrices'" in str(looked_up.value)
    assert explicit.value.status == 422
    assert "'forecastPrices'" in str(explicit.value)
    assert lacking.calls("/prices") == []
    assert lacking.calls("/forecast-prices") == [{}]


def test_an_address_without_the_prices_capability_is_refused_before_prices_are_requested() -> (
    None
):
    address = minstroem_address()
    address["capabilities"]["prices"] = False
    service = MinStroemService([address])

    with service.client() as c, pytest.raises(MinStroemError) as caught:
        get_prices_with_forecast("2026-11-02", "2026-11-03", client=c)

    assert "'prices'" in str(caught.value)
    assert caught.value.status is None
    assert service.calls("/prices") == []
    assert len(service.calls("/forecast-prices")) == 1


def test_with_an_explicit_id_a_missing_prices_capability_is_the_servers_422() -> None:
    address = minstroem_address()
    address["capabilities"]["prices"] = False
    service = MinStroemService([address])
    service.forecast = _forecast(_hours("2026-11-02 10:00", "2026-11-04"))

    with service.client() as c, pytest.raises(MinStroemError) as caught:
        get_prices_with_forecast(
            "2026-11-02", "2026-11-03", address_id=ADDRESS_ID, client=c
        )

    assert caught.value.status == 422
    assert "the address lacks the 'prices' capability" in str(caught.value)


def test_without_the_prices_capability_a_period_that_needs_no_confirmed_price_still_works() -> (
    None
):
    # The capability is checked where /prices would be requested; a period
    # entirely beyond the confirmed cap never requests it.
    address = minstroem_address()
    address["capabilities"]["prices"] = False
    service = MinStroemService([address])
    service.forecast = _forecast(_hours("2026-11-04", "2026-11-06"))

    with service.client() as c:
        frame = get_prices_with_forecast("2026-11-04", "2026-11-06", client=c)

    assert len(frame) == 48
    assert frame[FLAG].tolist() == [True] * 48
    assert service.calls("/prices") == []


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2026-11-02T10:30", None),
        ("2026-11-02T10:00", "2026-11-02T12:15"),
        ("2026-11-03", "2026-11-02"),
        ("2026-11-03", "2026-11-03"),
        ("2026-03-29T02:00", None),
    ],
)
def test_a_bad_period_is_a_value_error_before_any_key_or_request(
    monkeypatch: pytest.MonkeyPatch,
    minstroem_service: MinStroemService,
    start: TimeLike,
    end: TimeLike | None,
) -> None:
    monkeypatch.delenv(API_KEY_ENV, raising=False)

    with minstroem_service.client(None) as c, pytest.raises(ValueError) as caught:
        get_prices_with_forecast(start, end, client=c)

    assert not isinstance(caught.value, MissingApiKeyError)
    assert minstroem_service.requests == []


def test_a_non_uuid_address_is_a_value_error_before_any_request(
    minstroem_service: MinStroemService,
) -> None:
    with minstroem_service.client() as c, pytest.raises(ValueError, match="UUID"):
        get_prices_with_forecast("2026-11-02", address_id="x", client=c)

    assert minstroem_service.requests == []


def test_the_combined_series_is_idempotent(stitched: MinStroemService) -> None:
    with stitched.client() as c:
        first = get_prices_with_forecast("2026-11-02", "2026-11-04", client=c)
        second = get_prices_with_forecast("2026-11-02", "2026-11-04", client=c)

    pd.testing.assert_frame_equal(first, second)
    assert first.attrs["fetched_at"] <= second.attrs["fetched_at"]


def test_the_address_is_looked_up_once_for_both_requests(
    stitched: MinStroemService,
) -> None:
    with stitched.client() as c:
        get_prices_with_forecast("2026-11-02", "2026-11-03", client=c)

    assert len(stitched.calls("/me/addresses")) == 1
    assert len(stitched.calls("/forecast-prices")) == 1
    assert len(stitched.calls("/prices")) == 1


def test_the_forecast_is_fetched_before_the_confirmed_prices(
    stitched: MinStroemService,
) -> None:
    with stitched.client() as c:
        get_prices_with_forecast("2026-11-02", "2026-11-03", client=c)

    paths = [r.url.path.rsplit("/", 1)[1] for r in stitched.requests]
    assert paths.index("forecast-prices") < paths.index("prices")


def test_the_combined_series_closes_a_client_it_created_even_on_a_capability_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = MinStroemService([minstroem_address(forecastPrices=False)])
    made: list[MinStroemClient] = []

    def make() -> MinStroemClient:
        client = service.client()
        made.append(client)
        return client

    monkeypatch.setattr(_series, "MinStroemClient", make)

    with pytest.raises(MinStroemError):
        get_prices_with_forecast("2026-11-02", "2026-11-03")

    assert made[0].closed is True


class Scenario(NamedTuple):
    """A confirmed and forecast layout, for the 'never False after True' property."""

    confirmed: tuple[str, str]
    forecast: tuple[str, str]
    period: tuple[str, str]


@pytest.mark.parametrize(
    "scenario",
    [
        Scenario(
            ("2026-11-01", "2026-11-02 14:00"),
            ("2026-11-02 10:00", "2026-11-04"),
            ("2026-11-02", "2026-11-04"),
        ),
        Scenario(
            ("2026-11-02", "2026-11-02 06:00"),
            ("2026-11-02 06:00", "2026-11-04"),
            ("2026-11-02", "2026-11-04"),
        ),
        Scenario(
            ("2026-11-02", "2026-11-04"),
            ("2026-11-02", "2026-11-04"),
            ("2026-11-02", "2026-11-04"),
        ),
        Scenario(
            ("2026-11-02", "2026-11-02 01:00"),
            ("2026-11-03", "2026-11-04"),
            ("2026-11-02", "2026-11-04"),
        ),
    ],
)
def test_a_confirmed_hour_never_follows_a_forecast_hour(
    minstroem_service: MinStroemService, scenario: Scenario
) -> None:
    _set_prices(minstroem_service, _confirmed(_hours(*scenario.confirmed)))
    minstroem_service.forecast = _forecast(_hours(*scenario.forecast))

    with minstroem_service.client() as c:
        frame = get_prices_with_forecast(*scenario.period, client=c)

    _never_confirmed_after_forecast(frame)
    assert len(frame) == len(_hours(*scenario.period))


# === showcase ==================================================================


def test_main_without_a_key_prints_one_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(API_KEY_ENV, raising=False)

    prices.main()

    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    assert API_KEY_ENV in lines[0]


def test_main_with_a_key_shows_the_forecast_the_combined_series_and_the_history(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    minstroem_service: MinStroemService,
) -> None:
    now = pd.Timestamp.now(tz=CPH).floor("h")
    minstroem_service.forecast = _forecast(
        list(pd.date_range(now, periods=30, freq="h"))
    )
    monkeypatch.setenv(API_KEY_ENV, "test-key")
    monkeypatch.setattr(_series, "MinStroemClient", minstroem_service.client)

    prices.main()

    out = capsys.readouterr().out
    assert "forecast: 30 hours, fetched " in out
    assert "combined: " in out
    assert "history: (" in out
    assert "test-key" not in out
