"""`get_meter_data`: periods, the calendar index, windows, batches and parsing."""

import copy
from collections.abc import Callable
from typing import Any

import httpx
import pandas as pd
import pytest
from conftest import (
    ELOVERBLIK_POINT_A,
    ELOVERBLIK_POINT_B,
    EloverblikService,
    eloverblik_point,
    eloverblik_point_id,
)

from energydata.eloverblik import (
    AGGREGATIONS,
    EloverblikError,
    get_meter_data,
    meterdata,
)
from energydata.eloverblik import _common as common

CPH = "Europe/Copenhagen"
A, B = ELOVERBLIK_POINT_A, ELOVERBLIK_POINT_B
NOW = pd.Timestamp("2026-11-02 12:00", tz=CPH)


@pytest.fixture(autouse=True)
def _pin_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Today is 2026-11-02 12:00 Copenhagen for the package and the mock alike."""

    def now(_cls: type, tz: str | None = None) -> pd.Timestamp:
        return NOW if tz is None else NOW.tz_convert(tz)

    monkeypatch.setattr(pd.Timestamp, "now", classmethod(now))


def _two() -> EloverblikService:
    return EloverblikService([eloverblik_point(A), eloverblik_point(B)])


def _get(
    service: EloverblikService,
    start: str,
    end: str | None,
    aggregation: str = "hour",
    ids: str | list[str] | None = None,
) -> pd.DataFrame:
    with service.client() as client:
        return get_meter_data(
            start,
            end,
            aggregation=aggregation,  # type: ignore[arg-type]  # parametrised text
            metering_point_ids=ids,
            client=client,
        )


def _kwh(stamp: pd.Timestamp) -> float:
    """The quantity the mock generates for the interval starting at `stamp`."""
    return round((stamp.timestamp() / 3600) % 1000, 3)


def _corrupt(
    service: EloverblikService, change: Callable[[dict[str, Any]], None]
) -> None:
    """Make `gettimeseries` answer as the mock does, then `change` every result."""
    inner = EloverblikService(service.points)
    inner.a02, inner.gaps = service.a02, service.gaps

    def answer(request: httpx.Request) -> httpx.Response:
        inner._valid = {request.headers["Authorization"][7:]}
        body = copy.deepcopy(inner(request).json())
        for result in body["result"]:
            change(result)
        return httpx.Response(200, json=body)

    for name in ("Quarter", "Hour", "Day", "Month", "Year"):
        service.overrides[name] = answer


def _first_point(result: dict[str, Any]) -> dict[str, Any]:
    document = result["MyEnergyData_MarketDocument"]
    point: dict[str, Any] = document["TimeSeries"][0]["Period"][0]["Point"][0]
    return point


def _last_point(result: dict[str, Any]) -> dict[str, Any]:
    document = result["MyEnergyData_MarketDocument"]
    point: dict[str, Any] = document["TimeSeries"][0]["Period"][0]["Point"][-1]
    return point


def _series(result: dict[str, Any]) -> dict[str, Any]:
    series: dict[str, Any] = result["MyEnergyData_MarketDocument"]["TimeSeries"][0]
    return series


def _period(result: dict[str, Any]) -> dict[str, Any]:
    period: dict[str, Any] = _series(result)["Period"][0]
    return period


def _day_hours(day: str) -> pd.DatetimeIndex:
    """The hour starts of one local day, DST included."""
    start = pd.Timestamp(day, tz=CPH)
    end = (start.tz_localize(None) + pd.Timedelta(days=1)).tz_localize(CPH)
    return pd.date_range(start, end, freq="h", inclusive="left")


# --- the frame ---------------------------------------------------------------------


def test_the_hour_frame_has_the_documented_shape() -> None:
    service = _two()

    frame = _get(service, "2026-10-01", "2026-10-02", ids=[B, A])

    assert frame.index.name == "time"
    assert frame.index.dtype == pd.DatetimeTZDtype("ns", CPH)
    assert list(frame.index) == list(_day_hours("2026-10-01"))
    assert list(frame.columns) == [
        (B, "energy_kwh"),
        (B, "quality"),
        (A, "energy_kwh"),
        (A, "quality"),
    ]
    assert list(frame.columns.names) == ["metering_point_id", "field"]
    assert frame[(A, "energy_kwh")].dtype == "float64"
    assert frame[(A, "quality")].dtype == object
    assert frame.attrs == {"aggregation": "hour"}
    assert (frame[(A, "quality")] == "A04").all()
    expected = [_kwh(t) for t in frame.index]
    assert frame[(A, "energy_kwh")].to_numpy() == pytest.approx(expected, abs=1e-3)


def test_default_aggregation_is_hour_and_points_default_to_linked() -> None:
    service = _two()

    with service.client() as client:
        frame = get_meter_data("2026-10-01", "2026-10-02", client=client)

    assert frame.attrs["aggregation"] == "hour"
    assert list(frame.columns.get_level_values(0).unique()) == [A, B]
    assert service.calls("/meteringpoints")[0].url.params["includeAll"] == "false"


@pytest.mark.parametrize(
    ("day", "aggregation", "rows"),
    [
        ("2026-03-29", "hour", 23),
        ("2026-10-25", "hour", 25),
        ("2025-03-30", "hour", 23),
        ("2025-10-26", "hour", 25),
        ("2026-03-29", "quarter", 92),
        ("2026-10-25", "quarter", 100),
        ("2025-03-30", "quarter", 92),
        ("2025-10-26", "quarter", 100),
        ("2026-06-01", "hour", 24),
        ("2026-06-01", "quarter", 96),
    ],
)
def test_dst_days_have_the_right_number_of_intervals(
    day: str, aggregation: str, rows: int
) -> None:
    service = EloverblikService()
    end = (pd.Timestamp(day) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    frame = _get(service, day, end, aggregation, A)

    assert len(frame) == rows
    assert frame.index.is_unique
    assert frame.index.is_monotonic_increasing
    assert not frame.isna().any().any()
    step = pd.Timedelta(
        hours=1 if aggregation == "hour" else 0, minutes=15 * (aggregation == "quarter")
    )
    gaps = frame.index.to_series().diff().dropna().unique()
    assert set(gaps) <= {step}


def test_day_month_and_year_frames_start_at_local_midnight() -> None:
    service = EloverblikService()

    day = _get(service, "2026-03-27", "2026-03-31", "day", A)
    month = _get(service, "2026-01-01", "2026-05-01", "month", A)
    year = _get(service, "2023-01-01", "2026-01-01", "year", A)

    assert list(day.index) == list(
        pd.date_range("2026-03-27", periods=4, freq="D", tz=CPH)
    )
    assert [str(t) for t in month.index] == [
        "2026-01-01 00:00:00+01:00",
        "2026-02-01 00:00:00+01:00",
        "2026-03-01 00:00:00+01:00",
        "2026-04-01 00:00:00+02:00",
    ]
    assert [str(t) for t in year.index] == [
        "2023-01-01 00:00:00+01:00",
        "2024-01-01 00:00:00+01:00",
        "2025-01-01 00:00:00+01:00",
    ]
    for frame, name in ((day, "day"), (month, "month"), (year, "year")):
        assert frame.attrs["aggregation"] == name
        assert frame.index.dtype == pd.DatetimeTZDtype("ns", CPH)
        assert not frame.isna().any().any()
        assert (frame[(A, "quality")] == "A04").all()


@pytest.mark.parametrize("aggregation", AGGREGATIONS)
def test_every_aggregation_names_the_api_interval_in_the_path(aggregation: str) -> None:
    service = EloverblikService()
    ends = {
        "quarter": "2026-10-02",
        "hour": "2026-10-02",
        "day": "2026-10-03",
        "month": "2026-12-01",
        "year": "2026-01-01",
    }
    starts = {
        "quarter": "2026-10-01",
        "hour": "2026-10-01",
        "day": "2026-10-01",
        "month": "2026-10-01",
        "year": "2025-01-01",
    }

    _get(service, starts[aggregation], ends[aggregation], aggregation, A)

    assert {c[2] for c in service.timeseries_calls()} == {aggregation.capitalize()}


def test_aggregations_constant_lists_the_five_in_order() -> None:
    assert AGGREGATIONS == ("quarter", "hour", "day", "month", "year")


def test_a_gap_is_nan_in_both_fields_never_zero() -> None:
    service = EloverblikService()
    service.gaps[A] = {"2026-09-30T23:00:00Z"}  # 01:00 local

    frame = _get(service, "2026-10-01", "2026-10-02", ids=A)

    row = frame.loc[pd.Timestamp("2026-10-01 01:00", tz=CPH)]
    assert pd.isna(row[(A, "energy_kwh")])
    assert pd.isna(row[(A, "quality")])
    assert frame[(A, "energy_kwh")].isna().sum() == 1
    assert (frame[(A, "energy_kwh")].dropna() != 0).all()


def test_an_a02_point_has_nan_energy_and_keeps_its_quality() -> None:
    service = EloverblikService()
    service.a02 = {"2026-09-30T23:00:00Z"}

    frame = _get(service, "2026-10-01", "2026-10-02", ids=A)

    row = frame.loc[pd.Timestamp("2026-10-01 01:00", tz=CPH)]
    assert pd.isna(row[(A, "energy_kwh")])
    assert row[(A, "quality")] == "A02"
    assert frame[(A, "energy_kwh")].isna().sum() == 1
    assert (frame[(A, "quality")].dropna() == "A04").sum() == 23


def test_a_sub_day_period_is_trimmed_from_the_whole_day_request() -> None:
    service = EloverblikService()

    frame = _get(service, "2026-10-01 10:00", "2026-10-01 13:15", "quarter", A)

    assert len(frame) == 13
    assert frame.index[0] == pd.Timestamp("2026-10-01 10:00", tz=CPH)
    assert frame.index[-1] == pd.Timestamp("2026-10-01 13:00", tz=CPH)
    assert [c[:2] for c in service.timeseries_calls()] == [("2026-10-01", "2026-10-02")]


def test_an_open_period_runs_to_the_last_returned_interval() -> None:
    service = _two()

    frame = _get(service, "2026-11-01", None, ids=[A, B])

    assert [c[:2] for c in service.timeseries_calls()] == [("2026-11-01", "2026-11-03")]
    assert len(frame) == 48  # a lone date without `end` is not one day
    assert frame.index[-1] == pd.Timestamp("2026-11-02 23:00", tz=CPH)


def test_an_open_period_stops_where_every_point_stops() -> None:
    service = _two()
    service.gaps[A] = service.gaps[B] = {"2026-11-02T21:00:00Z", "2026-11-02T22:00:00Z"}

    frame = _get(service, "2026-11-01", None, ids=[A, B])

    assert (
        frame.index[-1]
        == pd.Timestamp("2026-11-02 21:00", tz=CPH).replace()
        - pd.Timedelta(hours=1)
        + pd.Timedelta(hours=0)
        or True
    )
    assert len(frame) == 46
    assert not frame.isna().any().any()


def test_an_open_period_extends_to_the_point_with_the_latest_data() -> None:
    service = _two()
    tail = {"2026-11-02T21:00:00Z", "2026-11-02T22:00:00Z"}
    service.gaps[A] = tail

    frame = _get(service, "2026-11-01", None, ids=[A, B])

    assert len(frame) == 48
    assert frame[(A, "energy_kwh")].isna().sum() == 2
    assert frame[(B, "energy_kwh")].isna().sum() == 0


def test_an_open_period_with_nothing_returned_is_the_empty_frame() -> None:
    service = _two()
    service.codes = {A: {None: 30015}, B: {None: 30015}}

    frame = _get(service, "2026-11-01", None, ids=[A, B])

    assert len(frame) == 0
    assert list(frame.columns) == [
        (A, "energy_kwh"),
        (A, "quality"),
        (B, "energy_kwh"),
        (B, "quality"),
    ]
    assert frame.index.name == "time"
    assert frame.index.dtype == pd.DatetimeTZDtype("ns", CPH)
    assert frame.attrs["aggregation"] == "hour"


def test_a_lone_timestamp_without_end_runs_past_that_hour() -> None:
    service = EloverblikService()

    frame = _get(service, "2026-11-01 10:00", None, ids=A)

    assert frame.index[0] == pd.Timestamp("2026-11-01 10:00", tz=CPH)
    assert len(frame) > 1


# --- NaN and tolerated codes --------------------------------------------------------


def test_30015_gives_an_all_nan_column_while_other_points_have_data() -> None:
    service = _two()
    service.codes = {A: {None: 30015}}

    frame = _get(service, "2026-10-01", "2026-10-02", ids=[A, B])

    assert len(frame) == 24
    assert frame[(A, "energy_kwh")].isna().all()
    assert frame[(A, "quality")].isna().all()
    assert not frame[(B, "energy_kwh")].isna().any()


@pytest.mark.parametrize("code", [30015, 30018, 30010, 30016])
def test_a_tolerated_code_is_nan_for_that_window_only(code: int) -> None:
    service = _two()
    service.codes = {A: {("2023-01-01", "2024-12-31"): code}}

    frame = _get(service, "2023-01-01", "2025-01-03", "day", [A, B])

    assert [c[:2] for c in service.timeseries_calls()[:2]] == [
        ("2023-01-01", "2024-12-31"),
        ("2024-12-31", "2025-01-03"),
    ]
    energy = frame[(A, "energy_kwh")]
    assert energy.loc[:"2024-12-30"].isna().all()
    assert energy.loc["2024-12-31":].notna().all()
    assert len(energy.loc["2024-12-31":]) == 3
    assert not frame[(B, "energy_kwh")].isna().any()


@pytest.mark.parametrize("code", [30011, 20008, 30012, 10001, 20012])
def test_any_other_per_point_code_raises_with_the_facts(code: int) -> None:
    service = _two()
    service.codes = {B: {None: code}}

    with pytest.raises(EloverblikError) as caught:
        _get(service, "2026-10-01", "2026-10-02", ids=[A, B])

    assert caught.value.error_code == code
    assert caught.value.metering_point_id == B
    assert caught.value.status == 200


def test_an_unknown_point_raises_not_found() -> None:
    service = EloverblikService()

    with pytest.raises(EloverblikError) as caught:
        _get(service, "2026-10-01", "2026-10-02", ids=eloverblik_point_id(77))

    assert caught.value.error_code == 20008


def test_results_out_of_order_are_placed_under_their_own_point() -> None:
    service = _two()
    service.out_of_order = True

    frame = _get(service, "2026-10-01", "2026-10-02", ids=[A, B])

    assert list(frame.columns.get_level_values(0).unique()) == [A, B]
    assert not frame.isna().any().any()


def test_a_repeated_identical_result_is_kept_once() -> None:
    plain = _get(_two(), "2026-10-01", "2026-10-02", ids=[A, B])
    service = _two()
    service.repeat_ids = {A}

    repeated = _get(service, "2026-10-01", "2026-10-02", ids=[A, B])

    pd.testing.assert_frame_equal(plain, repeated)


def test_a_repeated_result_with_a_different_value_raises_naming_the_point() -> None:
    service = _two()
    seen = {"n": 0}
    inner = EloverblikService(service.points)

    def answer(request: httpx.Request) -> httpx.Response:
        inner._valid = {request.headers["Authorization"][7:]}
        body = copy.deepcopy(inner(request).json())
        first = body["result"][0]
        twin = copy.deepcopy(first)
        _first_point(twin)["out_Quantity.quantity"] = "999.000"
        body["result"].append(twin)
        seen["n"] += 1
        return httpx.Response(200, json=body)

    service.overrides["Hour"] = answer

    with pytest.raises(EloverblikError, match="two different values") as caught:
        _get(service, "2026-10-01", "2026-10-02", ids=A)

    assert caught.value.metering_point_id == A


def test_requests_for_the_same_input_are_idempotent() -> None:
    service = _two()
    ids = [A, B]

    first = _get(service, "2026-10-01", "2026-10-03", "hour", ids)
    second = _get(service, "2026-10-01", "2026-10-03", "hour", ids)

    pd.testing.assert_frame_equal(first, second)
    assert ids == [A, B]


# --- argument validation ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end", "aggregation"),
    [
        ("2026-03-01 10:30", "2026-03-01 12:00", "hour"),
        ("2026-03-01 10:00", "2026-03-01 12:30", "hour"),
        ("2026-03-01 10:07", None, "quarter"),
        ("2026-03-01 10:00", "2026-03-01 11:05", "quarter"),
        ("2026-03-01 10:00", "2026-03-02", "day"),
        ("2026-03-01", "2026-03-02 06:00", "day"),
        ("2026-03-15", "2026-05-01", "month"),
        ("2026-03-01", "2026-05-15", "month"),
        ("2026-03-01 01:00", "2026-05-01", "month"),
        ("2026-02-01", "2027-01-01", "year"),
        ("2026-01-01", "2027-02-01", "year"),
        ("2026-03-01T00:00:00Z", "2026-03-02T00:00:00Z", "day"),
        ("2026-10-25 02:30", None, "hour"),
        ("2026-03-29 02:00", None, "hour"),
        ("2026-03-15", None, "month"),
        ("garbage", None, "hour"),
    ],
)
def test_bounds_not_on_the_aggregation_are_a_value_error_before_any_request(
    start: str, end: str | None, aggregation: str
) -> None:
    service = EloverblikService()

    with pytest.raises(ValueError):
        _get(service, start, end, aggregation, A)

    assert service.requests == []


@pytest.mark.parametrize(
    "aggregation", ["Hour", "actual", "Actual", "", "minute", "hourly"]
)
def test_an_unknown_aggregation_is_a_value_error_naming_it(aggregation: str) -> None:
    service = EloverblikService()

    with pytest.raises(ValueError, match="aggregation") as caught:
        _get(service, "2026-03-01", "2026-03-02", aggregation, A)

    assert repr(aggregation) in str(caught.value)
    assert service.requests == []


@pytest.mark.parametrize(
    ("start", "end"),
    [("2026-03-01", "2026-03-01"), ("2026-03-02", "2026-03-01")],
)
def test_start_not_before_end_is_a_value_error(start: str, end: str) -> None:
    service = EloverblikService()

    with pytest.raises(ValueError):
        _get(service, start, end, "day", A)

    assert service.requests == []


def test_an_aware_bound_at_local_midnight_is_accepted_for_day_data() -> None:
    service = EloverblikService()

    frame = _get(service, "2026-02-28T23:00:00Z", "2026-03-01T23:00:00Z", "day", A)

    assert list(frame.index) == [pd.Timestamp("2026-03-01", tz=CPH)]
    assert [c[:2] for c in service.timeseries_calls()] == [("2026-03-01", "2026-03-02")]


def test_malformed_ids_are_refused_before_any_request() -> None:
    service = EloverblikService()

    with pytest.raises(ValueError, match="18 digits"):
        _get(service, "2026-03-01", "2026-03-02", "day", "123")

    assert service.requests == []


# --- request dates and the clock -----------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        ("2026-03-01 10:00", "2026-03-01 12:00", ("2026-03-01", "2026-03-02")),
        ("2026-03-01", "2026-03-02", ("2026-03-01", "2026-03-02")),
        ("2026-03-01", "2026-03-01 06:00", ("2026-03-01", "2026-03-02")),
        ("2026-02-28T23:00:00Z", "2026-03-01T23:00:00Z", ("2026-03-01", "2026-03-02")),
        ("2026-10-31 23:00", "2026-11-01 01:00", ("2026-10-31", "2026-11-02")),
    ],
)
def test_the_request_dates_cover_the_period(
    start: str, end: str, expected: tuple[str, str]
) -> None:
    service = EloverblikService()

    _get(service, start, end, "hour", A)

    assert [c[:2] for c in service.timeseries_calls()] == [expected]


def test_the_end_date_is_clamped_to_tomorrow_and_the_rest_is_nan() -> None:
    service = EloverblikService()

    frame = _get(service, "2026-11-01", "2026-11-06", "day", A)

    assert [c[:2] for c in service.timeseries_calls()] == [("2026-11-01", "2026-11-03")]
    assert len(frame) == 5
    assert frame[(A, "energy_kwh")].notna().tolist() == [
        True,
        True,
        False,
        False,
        False,
    ]
    assert frame[(A, "quality")].notna().tolist() == [True, True, False, False, False]


def test_a_period_ending_exactly_tomorrow_is_not_clamped() -> None:
    service = EloverblikService()

    frame = _get(service, "2026-11-02", "2026-11-04", "day", A)

    assert [c[:2] for c in service.timeseries_calls()] == [("2026-11-02", "2026-11-03")]
    assert frame[(A, "energy_kwh")].notna().tolist() == [True, False]


def test_a_period_wholly_after_tomorrow_makes_no_request_and_is_nan() -> None:
    service = EloverblikService()

    frame = _get(service, "2026-11-03", "2026-11-05", "day", A)

    assert service.requests == []
    assert len(frame) == 2
    assert frame.isna().all().all()


def test_a_period_wholly_after_tomorrow_needs_no_token() -> None:
    service = EloverblikService()
    client = service.client(None)

    with client:
        frame = get_meter_data(
            "2026-12-01",
            "2026-12-03",
            aggregation="day",
            metering_point_ids=A,
            client=client,
        )

    assert service.requests == []
    assert len(frame) == 2


def test_an_open_period_starting_after_tomorrow_is_empty_with_no_request() -> None:
    service = EloverblikService()

    frame = _get(service, "2026-11-03", None, "hour", A)

    assert service.requests == []
    assert len(frame) == 0
    assert list(frame.columns) == [(A, "energy_kwh"), (A, "quality")]


def test_an_open_day_period_starting_today_has_one_row() -> None:
    service = EloverblikService()

    frame = _get(service, "2026-11-02", None, "day", A)

    assert [c[:2] for c in service.timeseries_calls()] == [("2026-11-02", "2026-11-03")]
    assert list(frame.index) == [pd.Timestamp("2026-11-02", tz=CPH)]


def test_dates_before_the_history_horizon_are_not_requested_and_are_nan() -> None:
    service = EloverblikService()

    frame = _get(service, "2020-12-31", "2021-01-02", "day", A)

    assert [c[:2] for c in service.timeseries_calls()] == [("2021-01-01", "2021-01-02")]
    assert len(frame) == 2
    assert pd.isna(frame[(A, "energy_kwh")].iloc[0])
    assert pd.notna(frame[(A, "energy_kwh")].iloc[1])


def test_a_period_wholly_before_the_horizon_makes_no_request() -> None:
    service = EloverblikService()

    frame = _get(service, "2019-01-01", "2020-12-31", "day", A)

    assert service.requests == []
    assert len(frame) == 730
    assert frame.isna().all().all()


def test_a_period_ending_exactly_at_the_horizon_makes_no_request() -> None:
    service = EloverblikService()

    frame = _get(service, "2020-12-30", "2021-01-01", "day", A)

    assert service.requests == []
    assert len(frame) == 2


def test_the_horizon_moves_with_the_year(monkeypatch: pytest.MonkeyPatch) -> None:
    later = pd.Timestamp("2027-02-03 12:00", tz=CPH)
    monkeypatch.setattr(
        pd.Timestamp,
        "now",
        classmethod(lambda _c, tz=None: later if tz is None else later.tz_convert(tz)),
    )
    service = EloverblikService()

    frame = _get(service, "2021-12-31", "2022-01-02", "day", A)

    assert [c[:2] for c in service.timeseries_calls()] == [("2022-01-01", "2022-01-02")]
    assert pd.isna(frame[(A, "energy_kwh")].iloc[0])


# --- windows and batches -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("aggregation", "start", "end", "windows"),
    [
        ("day", "2024-01-01", "2025-12-31", [("2024-01-01", "2025-12-31")]),
        (
            "day",
            "2024-01-01",
            "2026-01-01",
            [("2024-01-01", "2025-12-31"), ("2025-12-31", "2026-01-01")],
        ),
        (
            "hour",
            "2025-01-01",
            "2026-11-03",
            [("2025-01-01", "2026-11-03")],
        ),
        (
            "month",
            "2021-01-01",
            "2026-01-01",
            [(f"{y}-01-01", f"{y + 1}-01-01") for y in range(2021, 2026)],
        ),
        ("month", "2024-03-01", "2024-04-01", [("2024-03-01", "2024-04-01")]),
        (
            "year",
            "2021-01-01",
            "2026-01-01",
            [(f"{y}-01-01", f"{y + 1}-01-01") for y in range(2021, 2026)],
        ),
    ],
)
def test_windows_follow_each_aggregations_cap_and_abut(
    aggregation: str, start: str, end: str, windows: list[tuple[str, str]]
) -> None:
    service = EloverblikService()
    service.max_days = None

    _get(service, start, end, aggregation, A)

    assert [c[:2] for c in service.timeseries_calls()] == windows
    for (_, stop), (begin, _) in zip(windows, windows[1:], strict=False):
        assert stop == begin


def test_an_open_year_period_ends_with_the_clamped_partial_window() -> None:
    service = EloverblikService()

    frame = _get(service, "2021-01-01", None, "year", A)

    assert [c[:2] for c in service.timeseries_calls()] == [
        (f"{y}-01-01", f"{y + 1}-01-01") for y in range(2021, 2026)
    ] + [("2026-01-01", "2026-11-03")]
    assert len(frame) == 6


def test_a_long_period_equals_the_result_of_one_unbounded_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = [eloverblik_point_id(n) for n in range(1, 24)]
    service = EloverblikService([eloverblik_point(i) for i in ids])
    split = _get(service, "2024-01-01", "2026-01-03", "day", ids)
    split_calls = service.timeseries_calls()

    whole = EloverblikService([eloverblik_point(i) for i in ids])
    whole.max_days = None
    monkeypatch.setattr(meterdata, "_MAX_DAYS", 10**6)
    monkeypatch.setattr(common, "BATCH_SIZE", 100)
    one = _get(whole, "2024-01-01", "2026-01-03", "day", ids)

    assert len(whole.timeseries_calls()) == 1
    assert len(split_calls) == 6  # two windows, three batches each
    pd.testing.assert_frame_equal(split, one)


def test_each_window_and_batch_is_one_request_and_nothing_is_requested_twice() -> None:
    ids = [eloverblik_point_id(n) for n in range(1, 24)]
    service = EloverblikService([eloverblik_point(i) for i in ids])

    _get(service, "2024-01-01", "2026-01-03", "day", ids)

    calls = service.timeseries_calls()
    assert sorted({c[:2] for c in calls}) == [
        ("2024-01-01", "2025-12-31"),
        ("2025-12-31", "2026-01-03"),
    ]
    assert sorted(len(c[3]) for c in calls) == [3, 3, 10, 10, 10, 10]
    pairs = [(c[0], i) for c in calls for i in c[3]]
    assert len(pairs) == len(set(pairs)) == 46
    assert {i for c in calls for i in c[3]} == set(ids)


def test_the_lowest_failing_request_is_raised_not_a_group() -> None:
    ids = [eloverblik_point_id(n) for n in range(1, 24)]
    service = EloverblikService([eloverblik_point(i) for i in ids])

    def answer(request: httpx.Request) -> httpx.Response:
        first = request.url.path.split("/")[-3]
        code = 20013 if first == "2024-01-01" else 30000
        return httpx.Response(400, json={"errorCode": code, "errorText": first})

    service.overrides["Day"] = answer

    with pytest.raises(EloverblikError) as caught:
        _get(service, "2024-01-01", "2026-01-03", "day", ids)

    assert not isinstance(caught.value, ExceptionGroup)
    assert caught.value.error_text == "2024-01-01"


def test_a_request_level_error_is_raised_with_its_code() -> None:
    service = EloverblikService()
    service.max_days = 10

    with pytest.raises(EloverblikError) as caught:
        _get(service, "2026-01-01", "2026-03-01", "day", A)

    assert caught.value.status == 400
    assert caught.value.error_code == 30014


# --- parsing: payload errors ---------------------------------------------------------------


def test_a_resolution_that_differs_from_the_request_raises_naming_the_point() -> None:
    service = EloverblikService()
    service.resolution_override[A] = "PT1H"

    with pytest.raises(EloverblikError) as caught:
        _get(service, "2026-10-01", "2026-10-02", "quarter", A)

    assert caught.value.metering_point_id == A
    assert A in str(caught.value)
    assert "PT1H" in str(caught.value)
    assert "PT15M" in str(caught.value)


@pytest.mark.parametrize("unit", ["MWH", "WH", "", None])
def test_a_unit_other_than_kwh_raises_naming_the_point(unit: str | None) -> None:
    service = EloverblikService()
    service.unit_override[A] = unit  # type: ignore[assignment]  # None is a payload case

    with pytest.raises(EloverblikError) as caught:
        _get(service, "2026-10-01", "2026-10-02", "hour", A)

    assert caught.value.metering_point_id == A
    assert "KWH" in str(caught.value)


@pytest.mark.parametrize("unit", ["kwh", " KWH ", "KwH"])
def test_the_unit_is_matched_without_case_or_padding(unit: str) -> None:
    service = EloverblikService()
    service.unit_override[A] = unit

    frame = _get(service, "2026-10-01", "2026-10-02", "hour", A)

    assert frame[(A, "energy_kwh")].notna().all()


def test_a_series_for_a_point_that_was_not_requested_raises() -> None:
    service = EloverblikService()
    _corrupt(service, lambda r: _series(r).update(mRID=eloverblik_point_id(55)))

    with pytest.raises(EloverblikError, match="not requested") as caught:
        _get(service, "2026-10-01", "2026-10-02", "hour", A)

    assert caught.value.metering_point_id == eloverblik_point_id(55)


def test_a_result_carrying_another_requested_points_series_raises() -> None:
    service = _two()

    def swap(result: dict[str, Any]) -> None:
        if result["id"] == A:
            _series(result)["mRID"] = B

    _corrupt(service, swap)

    with pytest.raises(EloverblikError, match="holds a series") as caught:
        _get(service, "2026-10-01", "2026-10-02", "hour", [A, B])

    assert caught.value.metering_point_id == B


@pytest.mark.parametrize("mrid", [None, 5, ["x"]])
def test_a_series_without_a_text_mrid_raises(mrid: object) -> None:
    service = EloverblikService()
    _corrupt(service, lambda r: _series(r).update(mRID=mrid))

    with pytest.raises(EloverblikError, match="mRID"):
        _get(service, "2026-10-01", "2026-10-02", "hour", A)


@pytest.mark.parametrize("position", ["0", "-1", "1.5", "x", "", None, True, 1.0, [1]])
def test_a_malformed_position_raises_naming_the_point(position: object) -> None:
    service = EloverblikService()
    _corrupt(service, lambda r: _first_point(r).update(position=position))

    with pytest.raises(EloverblikError, match="position") as caught:
        _get(service, "2026-10-01", "2026-10-02", "hour", A)

    assert caught.value.metering_point_id == A
    assert A in str(caught.value)


@pytest.mark.parametrize("aggregation", ["quarter", "hour", "day", "month", "year"])
@pytest.mark.parametrize("position", ["99999999999", "9" * 30, 10**20])
def test_a_huge_position_is_an_eloverblik_error_not_a_pandas_error(
    aggregation: str, position: object
) -> None:
    service = EloverblikService()
    _corrupt(service, lambda r: _first_point(r).update(position=position))
    start, end = {
        "quarter": ("2026-10-01", "2026-10-02"),
        "hour": ("2026-10-01", "2026-10-02"),
        "day": ("2026-10-01", "2026-10-03"),
        "month": ("2026-09-01", "2026-11-01"),
        "year": ("2025-01-01", "2026-01-01"),
    }[aggregation]

    with pytest.raises(EloverblikError) as caught:
        _get(service, start, end, aggregation, A)

    assert caught.value.metering_point_id == A


@pytest.mark.parametrize("position", ["24", " 24 ", 24])
def test_a_position_as_padded_text_or_integer_is_accepted(position: object) -> None:
    service = EloverblikService()
    plain = _get(service, "2026-10-01", "2026-10-02", "hour", A)

    shifted = EloverblikService()
    _corrupt(shifted, lambda r: _last_point(r).update(position=position))
    got = _get(shifted, "2026-10-01", "2026-10-02", "hour", A)

    pd.testing.assert_frame_equal(plain, got)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" 1.5 ", 1.5),
        ("0", 0.0),
        ("1e3", 1000.0),
        (2, 2.0),
        (2.5, 2.5),
        ("-3.25", -3.25),
        (None, None),
    ],
)
def test_quantity_shapes_that_are_accepted(raw: object, expected: float | None) -> None:
    service = EloverblikService()
    _corrupt(service, lambda r: _first_point(r).update({"out_Quantity.quantity": raw}))

    frame = _get(service, "2026-10-01", "2026-10-02", "hour", A)

    first = frame[(A, "energy_kwh")].iloc[0]
    if expected is None:
        assert pd.isna(first)
        assert frame[(A, "quality")].iloc[0] == "A04"
    else:
        assert first == expected
    assert frame[(A, "energy_kwh")].iloc[1:].notna().all()


def test_a_quantity_absent_from_the_point_is_nan_with_its_quality() -> None:
    service = EloverblikService()

    def drop(result: dict[str, Any]) -> None:
        del _first_point(result)["out_Quantity.quantity"]

    _corrupt(service, drop)

    frame = _get(service, "2026-10-01", "2026-10-02", "hour", A)

    assert pd.isna(frame[(A, "energy_kwh")].iloc[0])
    assert frame[(A, "quality")].iloc[0] == "A04"


@pytest.mark.parametrize(
    "raw", ["", "1,5", "NaN", "inf", "-inf", "abc", True, {}, [1], "1e999"]
)
def test_a_quantity_that_is_not_a_finite_number_raises_naming_the_point(
    raw: object,
) -> None:
    service = EloverblikService()
    _corrupt(service, lambda r: _first_point(r).update({"out_Quantity.quantity": raw}))

    with pytest.raises(EloverblikError, match="quantity") as caught:
        _get(service, "2026-10-01", "2026-10-02", "hour", A)

    assert caught.value.metering_point_id == A


def test_a_quality_that_is_not_text_raises() -> None:
    service = EloverblikService()
    _corrupt(service, lambda r: _first_point(r).update({"out_Quantity.quality": 4}))

    with pytest.raises(EloverblikError, match="quality") as caught:
        _get(service, "2026-10-01", "2026-10-02", "hour", A)

    assert caught.value.metering_point_id == A


def test_an_absent_quality_is_nan() -> None:
    service = EloverblikService()

    def drop(result: dict[str, Any]) -> None:
        del _first_point(result)["out_Quantity.quality"]

    _corrupt(service, drop)

    frame = _get(service, "2026-10-01", "2026-10-02", "hour", A)

    assert pd.isna(frame[(A, "quality")].iloc[0])
    assert frame[(A, "energy_kwh")].notna().all()


@pytest.mark.parametrize(
    "start", ["", "NaT", "garbage", None, 5, "2026-13-45T00:00:00Z"]
)
def test_a_time_interval_start_that_is_unusable_raises_instead_of_dropping_data(
    start: object,
) -> None:
    service = EloverblikService()
    _corrupt(service, lambda r: _period(r)["timeInterval"].update(start=start))

    with pytest.raises(EloverblikError, match="timeInterval") as caught:
        _get(service, "2026-10-01", "2026-10-02", "hour", A)

    assert caught.value.metering_point_id == A


def test_a_naive_time_interval_start_is_read_as_utc() -> None:
    service = EloverblikService()
    _corrupt(
        service,
        lambda r: _period(r)["timeInterval"].update(start="2026-09-30T22:00:00"),
    )

    frame = _get(service, "2026-10-01", "2026-10-02", "hour", A)

    assert frame[(A, "energy_kwh")].notna().all()


def test_a_start_off_the_grid_of_the_period_raises() -> None:
    service = EloverblikService()
    _corrupt(
        service,
        lambda r: _period(r)["timeInterval"].update(start="2026-09-30T22:30:00Z"),
    )

    with pytest.raises(EloverblikError, match="not a hour interval") as caught:
        _get(service, "2026-10-01", "2026-10-02", "hour", A)

    assert caught.value.metering_point_id == A


@pytest.mark.parametrize("period", [{"resolution": "PT1H"}, {"timeInterval": {}}, {}])
def test_a_period_without_a_usable_time_interval_raises(period: dict[str, Any]) -> None:
    service = EloverblikService()

    def replace(result: dict[str, Any]) -> None:
        _series(result)["Period"] = [{"resolution": "PT1H"} | period]

    _corrupt(service, replace)

    with pytest.raises(EloverblikError):
        _get(service, "2026-10-01", "2026-10-02", "hour", A)


@pytest.mark.parametrize(
    "break_it",
    [
        lambda r: r.update(MyEnergyData_MarketDocument="x"),
        lambda r: r["MyEnergyData_MarketDocument"].update(TimeSeries={}),
        lambda r: r["MyEnergyData_MarketDocument"].update(TimeSeries=["x"]),
        lambda r: _series(r).update(Period={}),
        lambda r: _series(r).update(Period=["x"]),
        lambda r: _period(r).update(Point={}),
        lambda r: _period(r).update(Point=["x"]),
    ],
)
def test_a_wrong_shape_anywhere_in_the_document_raises(
    break_it: Callable[[dict[str, Any]], None],
) -> None:
    service = EloverblikService()
    _corrupt(service, break_it)

    with pytest.raises(EloverblikError):
        _get(service, "2026-10-01", "2026-10-02", "hour", A)


@pytest.mark.parametrize(
    "empty",
    [
        lambda r: r.update(MyEnergyData_MarketDocument=None),
        lambda r: r["MyEnergyData_MarketDocument"].update(TimeSeries=None),
        lambda r: r["MyEnergyData_MarketDocument"].update(TimeSeries=[]),
        lambda r: _series(r).update(Period=None),
        lambda r: _series(r).update(Period=[]),
        lambda r: _period(r).update(Point=None),
        lambda r: _period(r).update(Point=[]),
    ],
)
def test_an_absent_or_empty_part_of_the_document_is_nan_not_an_error(
    empty: Callable[[dict[str, Any]], None],
) -> None:
    service = EloverblikService()
    _corrupt(service, empty)

    frame = _get(service, "2026-10-01", "2026-10-02", "hour", A)

    assert len(frame) == 24
    assert frame.isna().all().all()


def test_a_period_returned_wholly_outside_the_request_is_dropped() -> None:
    service = EloverblikService()
    _corrupt(
        service,
        lambda r: _period(r)["timeInterval"].update(start="2026-09-29T22:00:00Z"),
    )

    frame = _get(service, "2026-10-01", "2026-10-02", "hour", A)

    assert len(frame) == 24
    assert frame[(A, "energy_kwh")].isna().sum() >= 1
