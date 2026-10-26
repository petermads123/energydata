from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

import httpx
import pandas as pd
import pytest
from conftest import ADDRESS_ID, MinStroemService

from energydata.minstroem import (
    API_KEY_ENV,
    Aggregation,
    MinStroemError,
    _series,
    costs,
    get_consumption_costs,
    get_solar_export_revenue,
)

CPH = "Europe/Copenhagen"
FORMAT = "%Y-%m-%dT%H:%M:%SZ"

type Fetch = Callable[..., pd.DataFrame]


class Case(NamedTuple):
    """One of the two money endpoints."""

    fn: Fetch
    suffix: str
    total_key: str
    total_column: str
    vat_included: bool


CASES = [
    pytest.param(
        Case(
            get_consumption_costs, "consumption-costs", "totalCost", "total_cost", True
        ),
        id="costs",
    ),
    pytest.param(
        Case(
            get_solar_export_revenue,
            "solar-export-revenue",
            "netRevenue",
            "net_revenue",
            False,
        ),
        id="revenue",
    ),
]


def _row(
    case: Case,
    start: str,
    *,
    energy: float | None = 2.0,
    rate: float | None = 1.25,
    fixed: float | None = 0.4,
    total: float | None = 2.9,
    estimate: bool | None = False,
    hours: int = 1,
) -> dict[str, object]:
    begin = datetime.strptime(start, FORMAT).replace(tzinfo=UTC)
    return {
        "start": start,
        "end": (begin + timedelta(hours=hours)).strftime(FORMAT),
        "energyKWh": energy,
        "totalPricePerKWh": rate,
        "fixedFeesForInterval": fixed,
        case.total_key: total,
        "isEstimate": estimate,
    }


def _serve(
    service: MinStroemService,
    case: Case,
    rows: list[dict[str, object]],
    **fields: object,
) -> None:
    body: dict[str, object] = {
        "data": rows,
        "currency": "DKK",
        "vatIncluded": case.vat_included,
    }
    body.update(fields)
    service.overrides[case.suffix] = lambda _r: httpx.Response(200, json=body)


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("currency", ["DKK", "SEK", "NOK"])
def test_columns_dtypes_index_and_attrs_are_the_documented_ones(
    case: Case, currency: str
) -> None:
    service = MinStroemService(currency=currency)

    with service.client() as c:
        frame = case.fn("2026-11-02", address_id=ADDRESS_ID, client=c)

    assert list(frame.columns) == [
        "energy_kwh",
        "total_price_per_kwh",
        "fixed_fees",
        case.total_column,
        "is_estimate",
    ]
    assert (frame.drop(columns="is_estimate").dtypes == "float64").all()
    assert str(frame["is_estimate"].dtype) == "boolean"
    assert str(frame.index.dtype) == f"datetime64[ns, {CPH}]"
    assert frame.index.name == "time"
    assert frame.attrs == {
        "address_id": ADDRESS_ID,
        "aggregation": "hour",
        "currency": currency,
        "vat_included": case.vat_included,
    }
    assert frame.attrs["vat_included"] is case.vat_included
    assert len(frame) == 24
    assert frame.notna().all().all()


@pytest.mark.parametrize("case", CASES)
def test_every_field_lands_in_its_own_column(
    minstroem_service: MinStroemService, case: Case
) -> None:
    row = _row(
        case,
        "2026-11-01T23:00:00Z",
        energy=2.0,
        rate=1.25,
        fixed=0.4,
        total=2.9,
        estimate=True,
    )
    _serve(minstroem_service, case, [row])

    with minstroem_service.client() as c:
        frame = case.fn("2026-11-02", address_id=ADDRESS_ID, client=c)

    first = frame.iloc[0]
    assert first["energy_kwh"] == 2.0
    assert first["total_price_per_kwh"] == 1.25
    assert first["fixed_fees"] == 0.4
    assert first[case.total_column] == 2.9
    assert first["is_estimate"] == True  # noqa: E712 - a boolean-array compare
    assert frame.iloc[1:].drop(columns="is_estimate").isna().all().all()
    assert frame["is_estimate"].iloc[1:].isna().all()


@pytest.mark.parametrize("case", CASES)
def test_a_null_rate_is_nan_while_the_money_fields_are_kept(
    minstroem_service: MinStroemService, case: Case
) -> None:
    row = _row(case, "2026-11-01T23:00:00Z", energy=0, rate=None, fixed=0.5, total=0.5)
    _serve(minstroem_service, case, [row])

    with minstroem_service.client() as c:
        frame = case.fn("2026-11-02", address_id=ADDRESS_ID, client=c)

    first = frame.iloc[0]
    assert first["energy_kwh"] == 0.0
    assert pd.isna(first["total_price_per_kwh"])
    assert first["fixed_fees"] == 0.5
    assert first[case.total_column] == 0.5


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize(
    "field", ["energyKWh", "fixedFeesForInterval", "TOTAL", "isEstimate"]
)
def test_a_null_in_a_required_field_is_a_minstroem_error(
    minstroem_service: MinStroemService, case: Case, field: str
) -> None:
    row = _row(case, "2026-11-01T23:00:00Z")
    row[case.total_key if field == "TOTAL" else field] = None
    _serve(minstroem_service, case, [row])

    with minstroem_service.client() as c, pytest.raises(MinStroemError) as caught:
        case.fn("2026-11-02", address_id=ADDRESS_ID, client=c)

    assert caught.value.status is None


@pytest.mark.parametrize("case", CASES)
def test_a_missing_field_in_a_row_is_a_minstroem_error(
    minstroem_service: MinStroemService, case: Case
) -> None:
    for name in (
        "energyKWh",
        "totalPricePerKWh",
        "fixedFeesForInterval",
        case.total_key,
    ):
        row = _row(case, "2026-11-01T23:00:00Z")
        del row[name]
        _serve(minstroem_service, case, [row])

        with minstroem_service.client() as c, pytest.raises(MinStroemError) as caught:
            case.fn("2026-11-02", address_id=ADDRESS_ID, client=c)

        assert name in str(caught.value)


@pytest.mark.parametrize("case", CASES)
def test_the_rate_the_api_sends_at_zero_energy_is_passed_through_not_recomputed(
    minstroem_service: MinStroemService, case: Case
) -> None:
    row = _row(case, "2026-11-01T23:00:00Z", energy=0.0, rate=1.5, fixed=0.5, total=0.5)
    _serve(minstroem_service, case, [row])

    with minstroem_service.client() as c:
        frame = case.fn("2026-11-02", address_id=ADDRESS_ID, client=c)

    assert frame["total_price_per_kwh"].iloc[0] == 1.5


@pytest.mark.parametrize("case", CASES)
def test_a_period_with_no_rows_is_nan_never_zero_but_a_zero_total_is_kept(
    minstroem_service: MinStroemService, case: Case
) -> None:
    zero = _row(
        case, "2026-11-01T23:00:00Z", energy=0.0, rate=None, fixed=0.0, total=0.0
    )
    _serve(minstroem_service, case, [zero])

    with minstroem_service.client() as c:
        frame = case.fn("2026-11-02", address_id=ADDRESS_ID, client=c)

    assert frame[case.total_column].iloc[0] == 0.0
    assert frame[case.total_column].iloc[1:].isna().all()
    assert frame["fixed_fees"].iloc[0] == 0.0

    minstroem_service.overrides.clear()
    minstroem_service.rows[case.suffix] = []
    with minstroem_service.client() as c:
        empty = case.fn("2026-11-02", address_id=ADDRESS_ID, client=c)
    assert len(empty) == 24
    assert empty.drop(columns="is_estimate").isna().all().all()
    assert empty["is_estimate"].isna().all()
    assert empty.attrs["currency"] == "DKK"


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize(
    "fields",
    [
        {"currency": "EUR"},
        {"currency": "dkk"},
        {"currency": ""},
        {"currency": None},
        {"currency": 208},
        {"vatIncluded": "true"},
        {"vatIncluded": None},
        {"vatIncluded": 1},
    ],
    ids=repr,
)
def test_an_unknown_currency_or_a_non_boolean_vat_flag_is_a_minstroem_error(
    minstroem_service: MinStroemService, case: Case, fields: dict[str, object]
) -> None:
    _serve(minstroem_service, case, [_row(case, "2026-11-01T23:00:00Z")], **fields)

    with minstroem_service.client() as c, pytest.raises(MinStroemError) as caught:
        case.fn("2026-11-02", address_id=ADDRESS_ID, client=c)

    assert caught.value.status is None


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("missing", ["currency", "vatIncluded"])
def test_a_response_without_currency_or_vat_flag_is_a_minstroem_error(
    minstroem_service: MinStroemService, case: Case, missing: str
) -> None:
    body: dict[str, object] = {
        "data": [],
        "currency": "DKK",
        "vatIncluded": case.vat_included,
    }
    del body[missing]
    minstroem_service.overrides[case.suffix] = lambda _r: httpx.Response(200, json=body)

    with minstroem_service.client() as c, pytest.raises(MinStroemError):
        case.fn("2026-11-02", address_id=ADDRESS_ID, client=c)


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize(
    ("second_currency", "flip_vat"), [("SEK", False), ("DKK", True), ("NOK", True)]
)
def test_windows_that_disagree_on_currency_or_vat_are_an_error_naming_both(
    minstroem_service: MinStroemService,
    case: Case,
    second_currency: str,
    flip_vat: bool,
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        # `from`, not call order, decides: the windows run concurrently.
        first = request.url.params["from"] == "2023-12-31T23:00:00Z"
        body = {
            "data": [],
            "currency": "DKK" if first else second_currency,
            "vatIncluded": case.vat_included != (flip_vat and not first),
        }
        return httpx.Response(200, json=body)

    minstroem_service.overrides[case.suffix] = respond

    with minstroem_service.client() as c, pytest.raises(MinStroemError) as caught:
        case.fn("2024-01-01", "2025-01-02", address_id=ADDRESS_ID, client=c)

    message = str(caught.value)
    assert "windows disagree" in message
    assert f"DKK/{case.vat_included}" in message
    assert f"{second_currency}/{case.vat_included != flip_vat}" in message
    assert caught.value.status is None


@pytest.mark.parametrize("case", CASES)
def test_windows_that_agree_give_one_currency_and_flag(case: Case) -> None:
    service = MinStroemService(currency="NOK")

    with service.client() as c:
        frame = case.fn("2024-01-01", "2025-01-02", address_id=ADDRESS_ID, client=c)

    assert len(service.calls("/" + case.suffix)) == 2
    assert frame.attrs["currency"] == "NOK"
    assert frame.attrs["vat_included"] is case.vat_included


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize(
    ("aggregation", "start", "end", "rows", "windows"),
    [
        ("hour", "2025-01-01", "2026-03-01", 10176, 2),
        ("day", "2025-01-01", "2026-03-01", 424, 2),
        ("month", "2025-01-01", "2026-02-01", 13, 2),
        ("month", "2023-03-01", "2024-04-01", 13, 2),
        ("year", "2023-01-01", "2026-01-01", 3, 3),
        ("year", "2024-01-01", "2025-01-01", 1, 1),
    ],
)
def test_a_long_period_is_windowed_under_the_money_endpoints_caps(
    minstroem_service: MinStroemService,
    case: Case,
    aggregation: Aggregation,
    start: str,
    end: str,
    rows: int,
    windows: int,
) -> None:
    with minstroem_service.client() as c:
        frame = case.fn(
            start, end, aggregation=aggregation, address_id=ADDRESS_ID, client=c
        )

    calls = sorted(minstroem_service.calls("/" + case.suffix), key=lambda q: q["from"])
    assert len(calls) == windows
    assert all(a["to"] == b["from"] for a, b in zip(calls, calls[1:], strict=False))
    assert len(frame) == rows
    assert frame.index.is_unique
    assert frame[case.total_column].notna().all()


def test_a_yearly_request_across_new_year_is_what_the_mock_refuses(
    minstroem_service: MinStroemService,
) -> None:
    # Why the yearly windows are one year each: costs and revenue cap at 366 days.
    with minstroem_service.client() as c, pytest.raises(MinStroemError) as caught:
        c.get_json(
            f"/me/addresses/{ADDRESS_ID}/consumption-costs",
            {
                "from": "2022-12-31T23:00:00Z",
                "to": "2025-12-31T23:00:00Z",
                "aggregation": "year",
            },
        )

    assert caught.value.error_code == "range_too_large"


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize(
    ("day", "length"), [("2026-03-29", 23), ("2026-10-25", 25), ("2026-11-02", 24)]
)
def test_hourly_money_rows_follow_the_danish_calendar(
    minstroem_service: MinStroemService, case: Case, day: str, length: int
) -> None:
    with minstroem_service.client() as c:
        frame = case.fn(day, address_id=ADDRESS_ID, client=c)

    assert len(frame) == length
    assert frame.index.is_unique


@pytest.mark.parametrize("case", CASES)
def test_each_endpoint_requests_only_its_own_path(
    minstroem_service: MinStroemService, case: Case
) -> None:
    other = (
        "solar-export-revenue"
        if case.suffix == "consumption-costs"
        else "consumption-costs"
    )

    with minstroem_service.client() as c:
        case.fn("2026-11-02", address_id=ADDRESS_ID, client=c)

    assert len(minstroem_service.calls("/" + case.suffix)) == 1
    assert minstroem_service.calls("/" + other) == []


def test_costs_and_revenue_differ_in_the_total_column_and_the_vat_flag_only(
    minstroem_service: MinStroemService,
) -> None:
    with minstroem_service.client() as c:
        cost = get_consumption_costs("2026-11-02", address_id=ADDRESS_ID, client=c)
        revenue = get_solar_export_revenue(
            "2026-11-02", address_id=ADDRESS_ID, client=c
        )

    assert "total_cost" in cost.columns
    assert "net_revenue" in revenue.columns
    assert "net_revenue" not in cost.columns
    assert "total_cost" not in revenue.columns
    assert cost.attrs["vat_included"] is True
    assert revenue.attrs["vat_included"] is False


def test_main_without_a_key_prints_one_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(API_KEY_ENV, raising=False)

    costs.main()

    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    assert API_KEY_ENV in lines[0]


def test_main_with_a_key_shows_monthly_costs_and_daily_revenue(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    minstroem_service: MinStroemService,
) -> None:
    monkeypatch.setenv(API_KEY_ENV, "test-key")
    monkeypatch.setattr(_series, "MinStroemClient", minstroem_service.client)

    costs.main()

    out = capsys.readouterr().out
    assert "costs per month in DKK: (1, 5)" in out
    assert "revenue per day: (7, 5)" in out
