import asyncio
import dataclasses
import json
import math
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import httpx
import pandas as pd
import pytest
from conftest import MarketRecords, MarketsService

import energydata.energidataservice as package
from energydata.energidataservice import (
    DSOS,
    EnergiDataServiceClient,
    EnergiDataServiceError,
    get_dso_subscriptions,
    get_dso_tariffs,
    get_electricity_tax,
    get_energinet_subscriptions,
    get_energinet_tariffs,
    pricelist,
)
from energydata.energidataservice.pricelist import main

ENERGINET = "5790000432752"
RADIUS = DSOS["radius"].gln
KONSTANT = DSOS["konstant-151"].gln
ZONE = "Europe/Copenhagen"
PRICES = [f"Price{n}" for n in range(1, 25)]
ORIGIN_START = "2013-12-31T23:00"  # 2014-01-01 local midnight, as the client sends it
HOURLY_TYPE = pd.DatetimeTZDtype(unit="ns", tz=ZONE)

type Rows = list[dict[str, Any]]


# --- helpers --------------------------------------------------------------------


def _record(
    code: str,
    valid_from: str,
    valid_to: str | None = None,
    *,
    resolution: str = "P1D",
    price: float | None = 1.0,
    prices: list[float | None] | None = None,
    gln: str = RADIUS,
    charge_type: str = "D03",
    **fields: object,
) -> dict[str, Any]:
    """A hand-built price-list record, in the form the API returns it."""
    count = 24 if resolution == "PT1H" else 1
    values = prices if prices is not None else [price] * count
    record: dict[str, Any] = {
        "GLN_Number": gln,
        "ChargeType": charge_type,
        "ChargeTypeCode": code,
        "ValidFrom": f"{valid_from}T00:00:00",
        "ValidTo": None if valid_to is None else f"{valid_to}T00:00:00",
        "ResolutionDuration": resolution,
    }
    for number, name in enumerate(PRICES):
        record[name] = values[number] if number < len(values) else None
    record.update(fields)
    return record


def _service(records: Rows) -> MarketsService:
    return MarketsService({"DatahubPricelist": records})


def _unfiltered(records: Rows) -> MarketsService:
    """A service that answers every request with `records`, whatever they hold."""
    service = _service([])
    service.respond = lambda request: httpx.Response(
        200,
        json={
            "total": len(records),
            "records": [
                {k: r[k] for k in request.url.params["columns"].split(",")}
                for r in records
            ],
        },
    )
    return service


def _expected(
    records: MarketRecords,
    gln: str,
    charge_type: str,
    codes: tuple[str, ...],
    when: pd.Timestamp,
) -> float:
    """The fixture's price for one local hour, found independently of the code."""
    day = when.date().isoformat()
    valid = [
        r
        for r in records["records"]
        if r["GLN_Number"] == gln
        and r["ChargeType"] == charge_type
        and r["ChargeTypeCode"] in codes
        and r["ValidFrom"][:10] <= day
        and (r["ValidTo"] is None or day < r["ValidTo"][:10])
    ]
    if not valid:
        return math.nan
    row = max(valid, key=lambda r: (r["ValidFrom"], -codes.index(r["ChargeTypeCode"])))
    value = (
        row["Price1"]
        if row["ResolutionDuration"] == "P1D"
        else row[f"Price{when.hour + 1}"]
    )
    return float(value)


def _values(frame: pd.DataFrame, column: str) -> list[float]:
    return [float(v) for v in frame[column]]


def _filters(service: MarketsService) -> dict[str, list[str]]:
    return service.filters("DatahubPricelist")


# --- get_dso_tariffs: row picking -------------------------------------------------


@pytest.mark.parametrize("hour", [0, 1, 12, 17, 18, 23])
def test_get_dso_tariffs_hour_n_takes_price_n_plus_one(
    pricelist_service: MarketsService, pricelist_fixture: MarketRecords, hour: int
) -> None:
    frame = get_dso_tariffs("radius", "2026-10-15", client=pricelist_service.client())

    row = next(
        r
        for r in pricelist_fixture["records"]
        if r["ChargeTypeCode"] == "DT_C_01" and r["ValidFrom"].startswith("2026-10-01")
    )
    assert len(frame) == 24
    assert frame["tariff"].iloc[hour] == row[f"Price{hour + 1}"]


def test_get_dso_tariffs_radius_evening_peak_is_the_live_checked_value(
    pricelist_service: MarketsService,
) -> None:
    frame = get_dso_tariffs("radius", "2026-10-15", client=pricelist_service.client())

    assert frame["tariff"].iloc[18] == pytest.approx(0.955573)


@pytest.mark.parametrize(
    ("day", "rows", "skipped"),
    [("2026-03-29", 23, 2), ("2026-10-25", 25, None), ("2026-06-01", 24, None)],
)
def test_get_dso_tariffs_prices_every_hour_by_its_wall_clock_hour(
    pricelist_service: MarketsService,
    pricelist_fixture: MarketRecords,
    day: str,
    rows: int,
    skipped: int | None,
) -> None:
    frame = get_dso_tariffs("radius", day, client=pricelist_service.client())

    assert len(frame) == rows
    assert _values(frame, "tariff") == [
        _expected(pricelist_fixture, RADIUS, "D03", ("DT_C_01",), s)
        for s in frame["start"]
    ]
    hours = list(frame["start"].dt.hour)
    if skipped is not None:
        assert skipped not in hours  # 02:00 does not exist on the spring day
    assert (frame["end"] - frame["start"] == pd.Timedelta(hours=1)).all()


def test_get_dso_tariffs_autumn_repeats_hour_two_with_price_three_twice(
    pricelist_service: MarketsService, pricelist_fixture: MarketRecords
) -> None:
    frame = get_dso_tariffs("radius", "2026-10-25", client=pricelist_service.client())

    twice = frame[frame["start"].dt.hour == 2]
    row = next(
        r
        for r in pricelist_fixture["records"]
        if r["ChargeTypeCode"] == "DT_C_01" and r["ValidFrom"].startswith("2026-10-01")
    )
    assert len(twice) == 2
    assert list(twice["tariff"]) == [row["Price3"], row["Price3"]]
    assert list(frame["start"].dt.hour)[:5] == [0, 1, 2, 2, 3]


def test_get_dso_tariffs_season_switch_changes_rows_at_local_midnight(
    pricelist_service: MarketsService, pricelist_fixture: MarketRecords
) -> None:
    frame = get_dso_tariffs(
        "radius",
        "2026-03-31T22:00",
        "2026-04-01T02:00",
        client=pricelist_service.client(),
    )

    rows = {
        r["ValidFrom"][:10]: r
        for r in pricelist_fixture["records"]
        if r["ChargeTypeCode"] == "DT_C_01"
    }
    old, new = rows["2025-10-01"], rows["2026-04-01"]
    assert _values(frame, "tariff") == [
        old["Price23"],
        old["Price24"],
        new["Price1"],
        new["Price2"],
    ]


@pytest.mark.parametrize(
    ("start", "end"),
    [("2026-04-01T00:00", None), ("2026-03-31T23:00", "2026-04-01T01:00")],
)
def test_get_dso_tariffs_summer_midnight_on_a_valid_from_day_takes_the_new_row(
    pricelist_service: MarketsService, start: str, end: str | None
) -> None:
    frame = get_dso_tariffs("radius", start, end, client=pricelist_service.client())

    # 2026-04-01 Price1 is 0.106175; the row it replaced had 0.0976. Local midnight in
    # summer is the previous day in UTC, so a one-day end padding would lose the row.
    assert frame["tariff"].iloc[-1] == pytest.approx(0.106175)
    assert frame["start"].iloc[-1] == pd.Timestamp("2026-04-01 00:00", tz=ZONE)


def test_get_dso_tariffs_daily_row_fills_every_hour(
    pricelist_service: MarketsService,
) -> None:
    frame = get_dso_tariffs("elinord", "2026-06-01", client=pricelist_service.client())

    assert len(frame) == 24
    assert set(_values(frame, "tariff")) == {0.1864}


def test_get_dso_tariffs_follows_the_konstant_code_change(
    pricelist_service: MarketsService,
) -> None:
    client = pricelist_service.client()

    before = get_dso_tariffs("konstant-151", "2025-06-01", client=client)
    after = get_dso_tariffs("konstant-151", "2026-06-01", client=client)

    assert before["tariff"].iloc[18] == pytest.approx(0.2518)  # 151-NT01T
    assert after["tariff"].iloc[18] == pytest.approx(0.2361)  # C_FBTNTR_B
    for frame in (before, after):
        assert not frame["tariff"].isna().any()
    codes = [
        json.loads(r.url.params["filter"])["ChargeTypeCode"]
        for r in pricelist_service.requests
    ]
    assert codes == [["151-NT01T", "C_FBTNTR_B"]] * 2


def test_get_dso_tariffs_hour_before_the_first_row_is_nan_and_the_period_is_full(
    pricelist_service: MarketsService,
) -> None:
    frame = get_dso_tariffs(
        "radius",
        "2017-09-29T23:00",
        "2017-09-30T02:00",
        client=pricelist_service.client(),
    )

    assert len(frame) == 3
    assert math.isnan(frame["tariff"].iloc[0])
    assert frame["tariff"].iloc[1] == pytest.approx(0.2589)


def test_get_dso_tariffs_open_ended_row_covers_the_far_future() -> None:
    service = _service([_record("DT_C_01", "2026-01-01", None, price=0.5)])

    frame = get_dso_tariffs("radius", "2035-06-01", client=service.client())

    assert set(_values(frame, "tariff")) == {0.5}


def test_get_dso_tariffs_valid_to_is_exclusive() -> None:
    service = _service([_record("DT_C_01", "2025-01-01", "2026-01-01", price=0.5)])

    frame = get_dso_tariffs(
        "radius", "2025-12-31T23:00", "2026-01-01T01:00", client=service.client()
    )

    assert _values(frame, "tariff")[0] == 0.5
    assert math.isnan(frame["tariff"].iloc[1])


def test_get_dso_tariffs_valid_from_is_inclusive() -> None:
    service = _service([_record("DT_C_01", "2026-01-01", None, price=0.5)])

    frame = get_dso_tariffs(
        "radius", "2025-12-31T23:00", "2026-01-01T01:00", client=service.client()
    )

    assert math.isnan(frame["tariff"].iloc[0])
    assert frame["tariff"].iloc[1] == 0.5


def test_get_dso_tariffs_latest_valid_from_wins_and_the_older_row_resumes() -> None:
    service = _service(
        [
            _record("DT_C_01", "2026-01-01", None, price=1.0),
            _record("DT_C_01", "2026-02-01", "2026-03-01", price=2.0),
        ]
    )

    frame = get_dso_tariffs(
        "radius", "2026-01-31T23:00", "2026-03-01T01:00", client=service.client()
    )

    values = _values(frame, "tariff")
    assert values[0] == 1.0
    assert set(values[1:-1]) == {2.0}
    assert values[-1] == 1.0


@pytest.mark.parametrize("reverse", [False, True])
def test_get_dso_tariffs_equal_valid_from_is_won_by_the_earlier_code(
    reverse: bool,
) -> None:
    records = [
        _record("151-NT01T", "2026-01-01", None, price=1.0, gln=KONSTANT),
        _record("C_FBTNTR_B", "2026-01-01", None, price=2.0, gln=KONSTANT),
    ]
    service = _service(records[::-1] if reverse else records)

    frame = get_dso_tariffs("konstant-151", "2026-06-01", client=service.client())

    assert set(_values(frame, "tariff")) == {1.0}


def test_get_dso_tariffs_later_valid_from_beats_the_earlier_code() -> None:
    service = _service(
        [
            _record("151-NT01T", "2026-01-01", None, price=1.0, gln=KONSTANT),
            _record("C_FBTNTR_B", "2026-03-01", None, price=2.0, gln=KONSTANT),
        ]
    )

    frame = get_dso_tariffs(
        "konstant-151", "2026-02-28T23:00", "2026-03-01T01:00", client=service.client()
    )

    assert _values(frame, "tariff") == [1.0, 2.0]


def test_get_dso_tariffs_ignores_the_order_the_service_returns_rows_in(
    pricelist_service: MarketsService,
) -> None:
    ordered = get_dso_tariffs("radius", "2026-10-15", client=pricelist_service.client())
    mine = [
        r
        for r in pricelist_service.data["DatahubPricelist"]
        if r["GLN_Number"] == RADIUS and r["ChargeType"] == "D03"
    ]
    reverse = _service(mine)
    reverse.respond = lambda request: httpx.Response(
        200,
        json={
            "total": len(mine),
            "records": [
                {k: r[k] for k in request.url.params["columns"].split(",")}
                for r in reversed(mine)
            ],
        },
    )

    flipped = get_dso_tariffs("radius", "2026-10-15", client=reverse.client())

    pd.testing.assert_frame_equal(ordered, flipped)


def test_get_dso_tariffs_a_none_price_is_nan_and_hides_the_older_row() -> None:
    old = _record("DT_C_01", "2026-01-01", None, resolution="PT1H", price=1.0)
    new_prices: list[float | None] = [2.0] * 24
    new_prices[4] = None
    new = _record("DT_C_01", "2026-02-01", None, resolution="PT1H", prices=new_prices)
    service = _service([old, new])

    frame = get_dso_tariffs("radius", "2026-02-10", client=service.client())

    assert math.isnan(frame["tariff"].iloc[4])
    assert frame["tariff"].iloc[3] == 2.0
    assert frame["tariff"].iloc[5] == 2.0


def test_get_dso_tariffs_a_zero_price_is_zero_not_nan() -> None:
    prices: list[float | None] = [0.5] * 24
    prices[5] = 0
    service = _service(
        [_record("DT_C_01", "2026-01-01", None, resolution="PT1H", prices=prices)]
    )

    frame = get_dso_tariffs("radius", "2026-02-10", client=service.client())

    assert frame["tariff"].iloc[5] == 0.0
    assert not frame["tariff"].isna().any()


def test_get_dso_tariffs_integer_prices_become_floats() -> None:
    service = _service([_record("DT_C_01", "2026-01-01", None, price=1)])

    frame = get_dso_tariffs("radius", "2026-02-10", client=service.client())

    assert frame["tariff"].dtype == "float64"


# --- get_dso_tariffs: before the price list begins ---------------------------------


@pytest.mark.parametrize(
    ("start", "end", "rows"),
    [
        ("2013-12-31", None, 24),
        ("2013-06-01", "2013-07-01", 30 * 24),
        ("2013-12-31", "2014-01-01", 24),
    ],
)
def test_get_dso_tariffs_before_2014_is_all_nan_and_makes_no_request(
    pricelist_service: MarketsService, start: str, end: str | None, rows: int
) -> None:
    frame = get_dso_tariffs("radius", start, end, client=pricelist_service.client())

    assert len(frame) == rows
    assert frame["tariff"].isna().all()
    assert pricelist_service.requests == []


def test_get_dso_tariffs_touching_2014_makes_one_request(
    pricelist_service: MarketsService,
) -> None:
    frame = get_dso_tariffs(
        "radius",
        "2013-12-31T23:00",
        "2014-01-01T01:00",
        client=pricelist_service.client(),
    )

    assert len(frame) == 2
    assert len(pricelist_service.requests) == 1


# --- shape, periods, validation --------------------------------------------------------


def test_get_dso_tariffs_frame_has_exactly_the_documented_shape(
    pricelist_service: MarketsService,
) -> None:
    frame = get_dso_tariffs("radius", "2026-06-01", client=pricelist_service.client())

    assert list(frame.columns) == ["start", "end", "tariff"]
    assert frame["start"].dtype == HOURLY_TYPE
    assert frame["end"].dtype == HOURLY_TYPE
    assert frame["tariff"].dtype == "float64"
    assert frame.index.equals(pd.RangeIndex(24))
    assert frame["start"].is_monotonic_increasing


@pytest.mark.parametrize(
    ("start", "end", "rows"),
    [
        ("2026-06-01", None, 24),
        ("2026-06-01T13:00", None, 1),
        ("2026-06-01T13:00", "2026-06-01T16:00", 3),
        ("2026-06-01", "2026-06-03", 48),
    ],
)
def test_get_dso_tariffs_follows_the_start_and_end_rules(
    pricelist_service: MarketsService, start: str, end: str | None, rows: int
) -> None:
    frame = get_dso_tariffs("radius", start, end, client=pricelist_service.client())

    assert len(frame) == rows
    assert frame["start"].iloc[0] == pd.Timestamp(start, tz=ZONE)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2026-06-01T13:30", None),
        ("2026-06-01T13:15", "2026-06-01T15:00"),
        ("2026-03-29T02:00", None),  # does not exist
        ("2026-10-25T02:00", None),  # happens twice
        ("garbage", None),
        ("2026-06-02", "2026-06-01"),
        ("2026-06-01T10:00", "2026-06-01T10:00"),
    ],
)
def test_get_dso_tariffs_rejects_a_bad_period_without_a_request(
    pricelist_service: MarketsService, start: str, end: str | None
) -> None:
    with pytest.raises(ValueError):
        get_dso_tariffs("radius", start, end, client=pricelist_service.client())

    assert pricelist_service.requests == []


# --- the DSO name ----------------------------------------------------------------------


@pytest.mark.parametrize("name", ["Radius", "RADIUS", "rAdIuS"])
def test_dso_name_is_case_insensitive(
    pricelist_service: MarketsService, name: str
) -> None:
    client = pricelist_service.client()

    same = get_dso_tariffs(name, "2026-06-01", client=client)
    lower = get_dso_tariffs("radius", "2026-06-01", client=client)

    pd.testing.assert_frame_equal(same, lower)


def test_dso_name_case_insensitivity_covers_names_with_digits(
    pricelist_service: MarketsService,
) -> None:
    frame = get_dso_tariffs("N1-131", "2026-06-01", client=pricelist_service.client())

    assert _filters(pricelist_service)["GLN_Number"] == [DSOS["n1-131"].gln]
    assert len(frame) == 24


@pytest.mark.parametrize(
    "name", ["radius ", " radius", "", "xyz", "n1_131", "n1 131", "læsø", "radius\n"]
)
@pytest.mark.parametrize("function", [get_dso_tariffs, get_dso_subscriptions])
def test_unknown_dso_raises_listing_every_name_and_makes_no_request(
    pricelist_service: MarketsService,
    function: Callable[..., pd.DataFrame],
    name: str,
) -> None:
    with pytest.raises(ValueError) as caught:
        function(name, "2026-06-01", client=pricelist_service.client())

    message = str(caught.value)
    assert repr(name) in message
    assert ", ".join(sorted(DSOS)) in message
    assert pricelist_service.requests == []


@pytest.mark.parametrize("name", [None, 1, b"radius"])
@pytest.mark.parametrize("function", [get_dso_tariffs, get_dso_subscriptions])
def test_a_dso_name_that_is_not_text_raises_value_error(
    pricelist_service: MarketsService,
    function: Callable[..., pd.DataFrame],
    name: object,
) -> None:
    with pytest.raises(ValueError, match="unknown DSO"):
        function(name, "2026-06-01", client=pricelist_service.client())

    assert pricelist_service.requests == []


def test_unknown_dso_with_an_owned_client_never_creates_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse() -> EnergiDataServiceClient:
        raise AssertionError("a client was created")

    monkeypatch.setattr(pricelist, "EnergiDataServiceClient", refuse)

    with pytest.raises(ValueError, match="unknown DSO"):
        get_dso_tariffs("xyz", "2026-06-01")


# --- rows the service should not have sent -------------------------------------------


@pytest.mark.parametrize("resolution", ["PT15M", "P1M", None, "", "pt1h"])
def test_hourly_functions_reject_an_unsupported_resolution_naming_it_and_the_code(
    resolution: str | None,
) -> None:
    service = _service(
        [
            _record(
                "DT_C_01",
                "2026-01-01",
                None,
                resolution="PT1H",
                ResolutionDuration=resolution,
            )
        ]
    )

    with pytest.raises(EnergiDataServiceError) as caught:
        get_dso_tariffs("radius", "2026-06-01", client=service.client())

    assert repr(resolution) in str(caught.value)
    assert "DT_C_01" in str(caught.value)


@pytest.mark.parametrize("resolution", ["PT1H", "P1D", None])
def test_subscriptions_reject_a_resolution_other_than_monthly(
    resolution: str | None,
) -> None:
    service = _service(
        [
            _record(
                "41004",
                "2026-01-01",
                None,
                gln=ENERGINET,
                charge_type="D01",
                ResolutionDuration=resolution,
            )
        ]
    )

    with pytest.raises(EnergiDataServiceError) as caught:
        get_energinet_subscriptions("2026-06-01", client=service.client())

    assert repr(resolution) in str(caught.value)
    assert "41004" in str(caught.value)


def test_tax_rejects_a_code_it_did_not_ask_for() -> None:
    service = _service([_record("41000", "2026-01-01", None, gln=ENERGINET)])
    service.honour_filter = False

    with pytest.raises(EnergiDataServiceError, match="'41000'"):
        get_electricity_tax("2026-06-01", client=service.client())


def test_tariffs_reject_a_record_of_another_gln_that_shares_the_code() -> None:
    # C-Tarif is hammel's, hjerting's, kimbrer's and n1-016's code: only the GLN differs.
    service = _service(
        [_record("C-Tarif", "2026-01-01", None, gln=DSOS["hjerting"].gln)]
    )
    service.honour_filter = False

    with pytest.raises(EnergiDataServiceError, match=DSOS["hjerting"].gln):
        get_dso_tariffs("hammel", "2026-06-01", client=service.client())


def test_tariffs_reject_a_subscription_record_that_shares_the_code(
    pricelist_service: MarketsService,
) -> None:
    pricelist_service.honour_filter = (
        False  # code CD is both n1-131's tariff and subscription
    )

    with pytest.raises(EnergiDataServiceError, match="D0[13]"):
        get_dso_tariffs("n1-131", "2026-06-01", client=pricelist_service.client())


def test_subscriptions_reject_a_tariff_record_that_shares_the_code() -> None:
    service = _service([_record("CD", "2026-01-01", None, gln=DSOS["n1-131"].gln)])
    service.honour_filter = False

    with pytest.raises(EnergiDataServiceError, match="'D03'"):
        get_dso_subscriptions("n1-131", "2026-06-01", client=service.client())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ValidFrom", None),
        ("ValidFrom", ""),
        ("ValidFrom", "NaT"),
        ("ValidFrom", "not-a-date"),
        ("ValidFrom", 123),
        ("ValidTo", ""),
        ("ValidTo", "NaT"),
        ("ValidTo", "not-a-date"),
        ("ValidTo", 5),
        ("Price1", "0.5"),
        ("Price1", True),
        ("Price1", [1.0]),
    ],
)
def test_a_malformed_row_raises_instead_of_being_priced(
    field: str, value: object
) -> None:
    record = _record("DT_C_01", "2026-01-01", None)
    record[field] = value
    service = _unfiltered([record])

    with pytest.raises(EnergiDataServiceError, match="DT_C_01"):
        get_dso_tariffs("radius", "2026-06-01", client=service.client())


def test_a_malformed_price_names_the_field() -> None:
    service = _service(
        [_record("DT_C_01", "2026-01-01", None, resolution="PT1H", Price7="x")]
    )

    with pytest.raises(EnergiDataServiceError, match="Price7"):
        get_dso_tariffs("radius", "2026-06-01", client=service.client())


def test_a_zoned_valid_from_counts_as_its_local_date() -> None:
    # 2025-12-31T23:00Z is Danish midnight of 2026-01-01, 2026-01-01T00:00Z is 01:00 there.
    service = _service(
        [
            _record(
                "DT_C_01", "2025-01-01", None, price=1.0, ValidTo="2026-01-01T00:00:00Z"
            ),
            _record(
                "DT_C_01",
                "2026-01-01",
                None,
                price=2.0,
                ValidFrom="2026-01-01T00:00:00Z",
            ),
        ]
    )

    frame = get_dso_tariffs(
        "radius", "2025-12-31T23:00", "2026-01-01T02:00", client=service.client()
    )

    assert _values(frame, "tariff") == [1.0, 2.0, 2.0]


# --- Energinet tariffs and the tax ----------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "system", "transmission"),
    [("2026-06-01", 0.072, 0.043), ("2025-06-01", 0.074, 0.061)],
)
def test_get_energinet_tariffs_keeps_system_and_transmission_apart(
    pricelist_service: MarketsService, day: str, system: float, transmission: float
) -> None:
    frame = get_energinet_tariffs(day, client=pricelist_service.client())

    assert len(frame) == 24
    assert set(_values(frame, "system_tariff")) == {system}
    assert set(_values(frame, "transmission_tariff")) == {transmission}


def test_get_energinet_tariffs_is_one_request_with_both_codes(
    pricelist_service: MarketsService,
) -> None:
    get_energinet_tariffs("2026-06-01", client=pricelist_service.client())

    assert len(pricelist_service.requests) == 1
    assert _filters(pricelist_service) == {
        "GLN_Number": [ENERGINET],
        "ChargeType": ["D03"],
        "ChargeTypeCode": ["41000", "40000"],
    }


def test_get_energinet_tariffs_columns_are_exact(
    pricelist_service: MarketsService,
) -> None:
    frame = get_energinet_tariffs("2026-06-01", client=pricelist_service.client())

    assert list(frame.columns) == [
        "start",
        "end",
        "system_tariff",
        "transmission_tariff",
    ]
    assert frame["start"].dtype == HOURLY_TYPE
    assert frame["system_tariff"].dtype == "float64"
    assert frame["transmission_tariff"].dtype == "float64"


def test_get_energinet_tariffs_on_the_autumn_day_has_25_constant_rows(
    pricelist_service: MarketsService,
) -> None:
    frame = get_energinet_tariffs("2026-10-25", client=pricelist_service.client())

    assert len(frame) == 25
    assert set(_values(frame, "system_tariff")) == {0.072}
    assert (frame["end"] - frame["start"] == pd.Timedelta(hours=1)).all()


def test_get_energinet_tariffs_changes_value_at_the_year_boundary(
    pricelist_service: MarketsService,
) -> None:
    frame = get_energinet_tariffs(
        "2025-12-31T23:00", "2026-01-01T01:00", client=pricelist_service.client()
    )

    assert _values(frame, "system_tariff") == [0.074, 0.072]
    assert _values(frame, "transmission_tariff") == [0.061, 0.043]


def test_get_energinet_tariffs_with_one_code_missing_gives_nan_for_it_only() -> None:
    service = _service([_record("41000", "2026-01-01", None, price=0.1, gln=ENERGINET)])

    frame = get_energinet_tariffs("2026-06-01", client=service.client())

    assert set(_values(frame, "system_tariff")) == {0.1}
    assert frame["transmission_tariff"].isna().all()


def test_get_energinet_tariffs_with_no_rows_at_all_is_all_nan() -> None:
    service = _service([])

    frame = get_energinet_tariffs("2026-06-01", client=service.client())

    assert len(frame) == 24
    assert frame["system_tariff"].isna().all()
    assert frame["transmission_tariff"].isna().all()
    assert frame["system_tariff"].dtype == "float64"


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        ("2025-12-31T22:00", "2026-01-01T02:00", [0.72, 0.72, 0.008, 0.008]),
        ("2023-06-30T23:00", "2023-07-01T01:00", [0.008, 0.697]),
        ("2025-12-31", "2026-01-01", [0.72] * 24),
        ("2026-01-01T00:00", None, [0.008]),
        ("2023-07-01T00:00", None, [0.697]),
        ("2022-06-30T23:00", "2022-07-01T01:00", [0.903, 0.763]),
    ],
)
def test_get_electricity_tax_changes_on_the_right_local_hour(
    pricelist_service: MarketsService,
    start: str,
    end: str | None,
    expected: list[float],
) -> None:
    frame = get_electricity_tax(start, end, client=pricelist_service.client())

    assert _values(frame, "electricity_tax") == expected


def test_get_electricity_tax_columns_and_request_are_exact(
    pricelist_service: MarketsService,
) -> None:
    frame = get_electricity_tax("2026-06-01", client=pricelist_service.client())

    assert list(frame.columns) == ["start", "end", "electricity_tax"]
    assert frame["end"].dtype == HOURLY_TYPE
    assert _filters(pricelist_service) == {
        "GLN_Number": [ENERGINET],
        "ChargeType": ["D03"],
        "ChargeTypeCode": ["EA-001"],
    }
    assert set(_values(frame, "electricity_tax")) == {0.008}


# --- subscriptions ---------------------------------------------------------------------------


def test_get_energinet_subscriptions_splits_at_the_year_and_clips_the_ends(
    pricelist_service: MarketsService,
) -> None:
    frame = get_energinet_subscriptions(
        "2025-12-15", "2026-01-15", client=pricelist_service.client()
    )

    assert list(frame.columns) == ["start", "end", "subscription"]
    assert len(frame) == 2
    assert list(frame["start"]) == [
        pd.Timestamp("2025-12-15", tz=ZONE),
        pd.Timestamp("2026-01-01", tz=ZONE),
    ]
    assert list(frame["end"]) == [
        pd.Timestamp("2026-01-01", tz=ZONE),
        pd.Timestamp("2026-01-15", tz=ZONE),
    ]
    assert _values(frame, "subscription") == pytest.approx([15.166666, 15.583333])


def test_get_energinet_subscriptions_a_lone_date_is_one_day_on_the_boundary(
    pricelist_service: MarketsService,
) -> None:
    frame = get_energinet_subscriptions("2026-01-01", client=pricelist_service.client())

    assert len(frame) == 1
    assert frame["start"].iloc[0] == pd.Timestamp("2026-01-01", tz=ZONE)
    assert frame["end"].iloc[0] == pd.Timestamp("2026-01-02", tz=ZONE)
    assert frame["subscription"].iloc[0] == pytest.approx(15.583333)


def test_get_energinet_subscriptions_a_lone_timestamp_is_one_hour(
    pricelist_service: MarketsService,
) -> None:
    frame = get_energinet_subscriptions(
        "2026-06-01T05:00", client=pricelist_service.client()
    )

    assert len(frame) == 1
    assert frame["start"].iloc[0] == pd.Timestamp("2026-06-01 05:00", tz=ZONE)
    assert frame["end"].iloc[0] == pd.Timestamp("2026-06-01 06:00", tz=ZONE)


def test_get_energinet_subscriptions_requests_code_41004_as_d01(
    pricelist_service: MarketsService,
) -> None:
    get_energinet_subscriptions("2026-06-01", client=pricelist_service.client())

    assert _filters(pricelist_service) == {
        "GLN_Number": [ENERGINET],
        "ChargeType": ["D01"],
        "ChargeTypeCode": ["41004"],
    }


def test_get_dso_subscriptions_one_row_spans_a_request_inside_one_validity(
    pricelist_service: MarketsService,
) -> None:
    frame = get_dso_subscriptions(
        "radius", "2025-12-01", "2026-02-01", client=pricelist_service.client()
    )

    assert len(frame) == 1
    assert frame["subscription"].iloc[0] == pytest.approx(36.773011)
    assert frame["start"].iloc[0] == pd.Timestamp("2025-12-01", tz=ZONE)
    assert frame["end"].iloc[0] == pd.Timestamp("2026-02-01", tz=ZONE)


def test_get_dso_subscriptions_two_rows_with_the_same_price_stay_two_rows(
    pricelist_service: MarketsService,
) -> None:
    # Radius' 2023 and 2024-01..07 rows both cost 44.75: one row per validity period.
    frame = get_dso_subscriptions(
        "radius", "2023-06-01", "2024-06-01", client=pricelist_service.client()
    )

    assert _values(frame, "subscription") == [44.75, 44.75]
    assert (
        frame["end"].iloc[0]
        == frame["start"].iloc[1]
        == pd.Timestamp("2024-01-01", tz=ZONE)
    )


def test_get_dso_subscriptions_a_gap_before_the_first_row_is_a_nan_row(
    pricelist_service: MarketsService,
) -> None:
    frame = get_dso_subscriptions(
        "radius", "2015-06-01", "2015-07-01", client=pricelist_service.client()
    )

    assert len(frame) == 2
    assert math.isnan(frame["subscription"].iloc[0])
    assert (
        frame["end"].iloc[0]
        == frame["start"].iloc[1]
        == pd.Timestamp("2015-06-19", tz=ZONE)
    )
    assert frame["subscription"].iloc[1] == 111.0


def test_get_dso_subscriptions_cover_the_request_without_gaps_or_overlaps(
    pricelist_service: MarketsService,
) -> None:
    frame = get_dso_subscriptions(
        "radius", "2025-01-01", "2027-01-01", client=pricelist_service.client()
    )

    assert frame["start"].iloc[0] == pd.Timestamp("2025-01-01", tz=ZONE)
    assert frame["end"].iloc[-1] == pd.Timestamp("2027-01-01", tz=ZONE)
    assert list(frame["end"].iloc[:-1]) == list(frame["start"].iloc[1:])
    assert _values(frame, "subscription") == pytest.approx([36.773011, 40.835055])


def test_get_dso_subscriptions_a_lone_timestamp_is_one_hour(
    pricelist_service: MarketsService,
) -> None:
    frame = get_dso_subscriptions(
        "radius", "2026-06-01T10:00", client=pricelist_service.client()
    )

    assert len(frame) == 1
    assert frame["start"].iloc[0] == pd.Timestamp("2026-06-01 10:00", tz=ZONE)
    assert frame["end"].iloc[0] == pd.Timestamp("2026-06-01 11:00", tz=ZONE)
    assert frame["subscription"].iloc[0] == pytest.approx(40.835055)


def test_get_dso_subscriptions_frame_has_the_documented_dtypes(
    pricelist_service: MarketsService,
) -> None:
    frame = get_dso_subscriptions(
        "radius", "2025-01-01", "2027-01-01", client=pricelist_service.client()
    )

    assert list(frame.columns) == ["start", "end", "subscription"]
    assert frame["start"].dtype == HOURLY_TYPE
    assert frame["end"].dtype == HOURLY_TYPE
    assert frame["subscription"].dtype == "float64"
    assert frame.index.equals(pd.RangeIndex(len(frame)))


def test_get_dso_subscriptions_sunds_is_one_nan_row_and_no_request(
    pricelist_service: MarketsService,
) -> None:
    frame = get_dso_subscriptions(
        "sunds", "2026-01-01", "2026-03-01", client=pricelist_service.client()
    )

    assert len(frame) == 1
    assert frame["start"].iloc[0] == pd.Timestamp("2026-01-01", tz=ZONE)
    assert frame["end"].iloc[0] == pd.Timestamp("2026-03-01", tz=ZONE)
    assert math.isnan(frame["subscription"].iloc[0])
    assert frame["subscription"].dtype == "float64"
    assert pricelist_service.requests == []


@pytest.mark.parametrize(
    ("start", "end"), [("2013-06-01", "2013-07-01"), ("2013-12-31", "2014-01-01")]
)
def test_energinet_subscriptions_before_2014_is_one_nan_row_and_no_request(
    pricelist_service: MarketsService, start: str, end: str
) -> None:
    frame = get_energinet_subscriptions(start, end, client=pricelist_service.client())

    assert len(frame) == 1
    assert math.isnan(frame["subscription"].iloc[0])
    assert pricelist_service.requests == []


def test_get_dso_subscriptions_konstant_requests_d01_and_both_codes(
    pricelist_service: MarketsService,
) -> None:
    frame = get_dso_subscriptions(
        "konstant-151", "2026-06-01", client=pricelist_service.client()
    )

    assert _filters(pricelist_service) == {
        "GLN_Number": [KONSTANT],
        "ChargeType": ["D01"],
        "ChargeTypeCode": ["151-E5004", "C_FBAHM__B"],
    }
    assert frame["subscription"].iloc[0] == pytest.approx(36.9167)


def test_n1_131_shares_code_cd_and_only_the_charge_type_tells_tariff_from_subscription(
    pricelist_service: MarketsService,
) -> None:
    client = pricelist_service.client()

    tariff = get_dso_tariffs("n1-131", "2026-06-01", client=client)
    subscription = get_dso_subscriptions("n1-131", "2026-06-01", client=client)

    assert [
        json.loads(r.url.params["filter"])["ChargeType"]
        for r in pricelist_service.requests
    ] == [["D03"], ["D01"]]
    assert tariff["tariff"].iloc[18] == pytest.approx(0.342632)
    assert subscription["subscription"].iloc[0] == pytest.approx(28.122861)


def _sub(code: str, start: str, end: str | None, price: float) -> dict[str, Any]:
    return _record(code, start, end, resolution="P1M", price=price, charge_type="D01")


def test_subscriptions_an_inner_bounded_row_splits_the_open_row_in_three() -> None:
    service = _service(
        [
            _sub("DA_C_F_01", "2026-01-01", None, 10.0),
            _sub("DA_C_F_01", "2026-02-01", "2026-03-01", 20.0),
        ]
    )

    frame = get_dso_subscriptions(
        "radius", "2026-01-15", "2026-03-15", client=service.client()
    )

    assert _values(frame, "subscription") == [10.0, 20.0, 10.0]
    assert list(frame["start"]) == [
        pd.Timestamp(d, tz=ZONE) for d in ("2026-01-15", "2026-02-01", "2026-03-01")
    ]


def test_subscriptions_a_zero_length_row_does_not_split_the_period() -> None:
    service = _service(
        [
            _sub("DA_C_F_01", "2026-01-01", None, 10.0),
            _sub("DA_C_F_01", "2026-02-01", "2026-02-01", 20.0),
        ]
    )

    frame = get_dso_subscriptions(
        "radius", "2026-01-15", "2026-03-15", client=service.client()
    )

    assert _values(frame, "subscription") == [10.0]


def test_subscriptions_a_none_price_is_a_nan_row() -> None:
    service = _service([_sub("DA_C_F_01", "2026-01-01", None, 10.0)])
    service.data["DatahubPricelist"][0]["Price1"] = None

    frame = get_dso_subscriptions(
        "radius", "2026-01-15", "2026-03-15", client=service.client()
    )

    assert len(frame) == 1
    assert frame["subscription"].isna().all()


def test_subscriptions_period_edge_on_a_row_boundary_does_not_use_the_old_row() -> None:
    service = _service(
        [
            _sub("DA_C_F_01", "2025-01-01", "2026-01-01", 10.0),
            _sub("DA_C_F_01", "2026-01-01", None, 20.0),
        ]
    )

    frame = get_dso_subscriptions("radius", "2026-01-01", client=service.client())

    assert _values(frame, "subscription") == [20.0]


# --- the request ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end", "sent_end"),
    [
        ("2026-01-01", None, "2026-01-02T23:00"),
        ("2026-07-01", None, "2026-07-02T22:00"),
        ("2026-01-01T23:00", None, "2026-01-02T23:00"),
        ("2026-01-01", "2026-01-02", "2026-01-02T23:00"),
        ("2026-06-01T22:00", "2026-06-02T00:00", "2026-06-02T22:00"),
    ],
)
def test_the_fetch_runs_from_2014_to_two_local_days_after_the_last_date(
    pricelist_service: MarketsService, start: str, end: str | None, sent_end: str
) -> None:
    get_electricity_tax(start, end, client=pricelist_service.client())

    params = pricelist_service.params("DatahubPricelist")
    assert params["start"] == ORIGIN_START
    assert params["end"] == sent_end
    assert params["timezone"] == "UTC"
    assert params["limit"] == "0"
    assert params["sort"] == "ValidFrom asc"


def test_the_fetch_asks_for_exactly_the_columns_it_reads(
    pricelist_service: MarketsService,
) -> None:
    get_electricity_tax("2026-06-01", client=pricelist_service.client())

    columns = pricelist_service.params("DatahubPricelist")["columns"].split(",")
    assert columns == [
        "GLN_Number",
        "ChargeType",
        "ChargeTypeCode",
        "ValidFrom",
        "ValidTo",
        "ResolutionDuration",
        *PRICES,
    ]


@pytest.mark.parametrize(
    ("function", "args", "filters"),
    [
        (
            get_dso_tariffs,
            ("radius", "2026-06-01"),
            {
                "GLN_Number": [RADIUS],
                "ChargeType": ["D03"],
                "ChargeTypeCode": ["DT_C_01"],
            },
        ),
        (
            get_dso_subscriptions,
            ("radius", "2026-06-01"),
            {
                "GLN_Number": [RADIUS],
                "ChargeType": ["D01"],
                "ChargeTypeCode": ["DA_C_F_01"],
            },
        ),
        (
            get_dso_tariffs,
            ("konstant-151", "2026-06-01"),
            {
                "GLN_Number": [KONSTANT],
                "ChargeType": ["D03"],
                "ChargeTypeCode": ["151-NT01T", "C_FBTNTR_B"],
            },
        ),
    ],
)
def test_each_function_sends_exactly_its_filter(
    pricelist_service: MarketsService,
    function: Callable[..., pd.DataFrame],
    args: tuple[str, ...],
    filters: dict[str, list[str]],
) -> None:
    function(*args, client=pricelist_service.client())

    assert _filters(pricelist_service) == filters


@pytest.mark.parametrize("name", ["dinel", "flow", "hammel"])
def test_codes_with_special_characters_round_trip_through_the_filter(name: str) -> None:
    dso = DSOS[name]
    service = _service(
        [_record(dso.tariff_codes[0], "2026-01-01", None, price=0.3, gln=dso.gln)]
    )

    frame = get_dso_tariffs(name, "2026-06-01", client=service.client())

    assert _filters(service)["ChargeTypeCode"] == [dso.tariff_codes[0]]
    assert set(_values(frame, "tariff")) == {0.3}


@pytest.mark.parametrize("end", ["2026-01-01", "2026-07-01"])
def test_a_ten_year_period_is_still_one_request_on_a_31_day_client(
    pricelist_service: MarketsService, end: str
) -> None:
    frame = get_dso_tariffs(
        "radius", "2016-01-01", end, client=pricelist_service.client()
    )

    assert len(pricelist_service.requests) == 1
    assert len(frame) == len(
        pd.date_range("2016-01-01", end, freq="h", tz=ZONE, inclusive="left")
    )


def test_a_long_period_costs_one_request_per_function(
    pricelist_service: MarketsService,
) -> None:
    client = pricelist_service.client()

    get_energinet_tariffs("2020-01-01", "2026-01-01", client=client)
    get_dso_subscriptions("radius", "2020-01-01", "2026-01-01", client=client)

    assert len(pricelist_service.requests) == 2


# --- the client --------------------------------------------------------------------------------------


def _owned(
    monkeypatch: pytest.MonkeyPatch, service: MarketsService
) -> list[EnergiDataServiceClient]:
    made: list[EnergiDataServiceClient] = []

    def factory() -> EnergiDataServiceClient:
        made.append(service.client())
        return made[-1]

    monkeypatch.setattr(pricelist, "EnergiDataServiceClient", factory)
    return made


@pytest.mark.parametrize(
    ("function", "args"),
    [
        (get_dso_tariffs, ("radius", "2026-06-01")),
        (get_energinet_tariffs, ("2026-06-01",)),
        (get_dso_subscriptions, ("radius", "2026-06-01")),
        (get_energinet_subscriptions, ("2026-06-01",)),
        (get_electricity_tax, ("2026-06-01",)),
    ],
)
def test_an_owned_client_is_created_used_and_closed(
    monkeypatch: pytest.MonkeyPatch,
    pricelist_service: MarketsService,
    function: Callable[..., pd.DataFrame],
    args: tuple[str, ...],
) -> None:
    made = _owned(monkeypatch, pricelist_service)

    frame = function(*args)

    assert len(frame) > 0
    assert len(made) == 1
    assert made[0].closed


def test_an_owned_client_is_closed_when_the_service_refuses(
    monkeypatch: pytest.MonkeyPatch, pricelist_service: MarketsService
) -> None:
    made = _owned(monkeypatch, pricelist_service)
    pricelist_service.respond = lambda _request: httpx.Response(400, json={})

    with pytest.raises(httpx.HTTPStatusError):
        get_electricity_tax("2026-06-01")

    assert made[0].closed


def test_an_owned_client_is_closed_when_a_row_is_malformed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made = _owned(
        monkeypatch,
        _unfiltered(
            [_record("EA-001", "2026-01-01", None, gln=ENERGINET, ValidFrom=None)]
        ),
    )

    with pytest.raises(EnergiDataServiceError):
        get_electricity_tax("2026-06-01")

    assert made[0].closed


def test_a_period_before_2014_creates_an_owned_client_but_sends_nothing(
    monkeypatch: pytest.MonkeyPatch, pricelist_service: MarketsService
) -> None:
    made = _owned(monkeypatch, pricelist_service)

    frame = get_energinet_tariffs("2013-06-01")

    assert frame["system_tariff"].isna().all()
    assert pricelist_service.requests == []
    assert all(client.closed for client in made)


@pytest.mark.parametrize(
    ("function", "args"),
    [
        (get_dso_tariffs, ("radius", "2026-06-01")),
        (get_energinet_tariffs, ("2026-06-01",)),
        (get_dso_subscriptions, ("radius", "2026-06-01")),
        (get_energinet_subscriptions, ("2026-06-01",)),
        (get_electricity_tax, ("2026-06-01",)),
    ],
)
def test_a_passed_client_is_left_open(
    pricelist_service: MarketsService,
    function: Callable[..., pd.DataFrame],
    args: tuple[str, ...],
) -> None:
    client = pricelist_service.client()

    function(*args, client=client)

    assert not client.closed
    client.close()


def test_calling_twice_on_one_client_gives_equal_frames(
    pricelist_service: MarketsService,
) -> None:
    client = pricelist_service.client()

    first = get_energinet_tariffs("2026-06-01", client=client)
    second = get_energinet_tariffs("2026-06-01", client=client)

    pd.testing.assert_frame_equal(first, second)
    assert not client.closed


@pytest.mark.parametrize(
    ("function", "args"),
    [
        (get_dso_tariffs, ("radius", "2026-06-01")),
        (get_energinet_tariffs, ("2026-06-01",)),
        (get_dso_subscriptions, ("radius", "2026-06-01")),
        (get_energinet_subscriptions, ("2026-06-01",)),
        (get_electricity_tax, ("2026-06-01",)),
    ],
)
def test_a_closed_passed_client_raises_runtime_error_when_a_request_is_needed(
    pricelist_service: MarketsService,
    function: Callable[..., pd.DataFrame],
    args: tuple[str, ...],
) -> None:
    client = pricelist_service.client()
    client.close()

    with pytest.raises(RuntimeError):
        function(*args, client=client)


@pytest.mark.parametrize(
    ("function", "args"),
    [
        (get_dso_subscriptions, ("sunds", "2026-06-01")),
        (get_energinet_tariffs, ("2013-06-01",)),
        (get_electricity_tax, ("2013-06-01",)),
    ],
)
def test_a_closed_passed_client_goes_unnoticed_when_no_request_is_needed(
    pricelist_service: MarketsService,
    function: Callable[..., pd.DataFrame],
    args: tuple[str, ...],
) -> None:
    client = pricelist_service.client()
    client.close()

    frame = function(
        *args, client=client
    )  # documented: RuntimeError only with a request

    assert len(frame) > 0


def test_the_functions_work_inside_a_running_event_loop(
    pricelist_service: MarketsService,
) -> None:
    async def inside() -> pd.DataFrame:
        with pricelist_service.client() as client:
            return get_dso_tariffs("radius", "2026-06-01", client=client)

    frame = asyncio.run(inside())

    assert len(frame) == 24


def test_a_real_client_cannot_reach_the_network_in_tests() -> None:
    with pytest.raises(Exception, match="network"):
        get_electricity_tax("2026-06-01")


def test_a_second_request_keeps_the_service_untouched_by_the_callers_arguments(
    pricelist_service: MarketsService,
) -> None:
    before = json.dumps(pricelist_service.data, sort_keys=True)

    get_dso_tariffs("radius", "2026-06-01", client=pricelist_service.client())

    assert json.dumps(pricelist_service.data, sort_keys=True) == before


# --- documentation (T8) -------------------------------------------------------------------------------

DOCUMENTED: list[tuple[Callable[..., pd.DataFrame], tuple[str, ...]]] = [
    (get_dso_tariffs, ("D03", "DKK/kWh", "DSOS", "`tariff`")),
    (get_energinet_tariffs, ("D03", "DKK/kWh", "`41000`", "`40000`", "5790000432752")),
    (get_dso_subscriptions, ("D01", "DKK/month", "DSOS", "`subscription`")),
    (get_energinet_subscriptions, ("D01", "DKK/month", "`41004`", "5790000432752")),
    (get_electricity_tax, ("D03", "DKK/kWh", "`EA-001`", "5790000432752")),
]


@pytest.mark.parametrize(("function", "needles"), DOCUMENTED)
def test_each_docstring_names_dataset_codes_unit_resolution_and_format(
    function: Callable[..., pd.DataFrame], needles: tuple[str, ...]
) -> None:
    doc = function.__doc__ or ""

    for needle in ("DatahubPricelist", "Resolution", "Format", "excl. VAT", *needles):
        assert needle in doc, (function.__name__, needle)


@pytest.mark.parametrize("function", [row[0] for row in DOCUMENTED])
def test_each_docstring_has_args_returns_and_raises(
    function: Callable[..., pd.DataFrame],
) -> None:
    doc = function.__doc__ or ""

    for section in (
        "Args:",
        "Returns:",
        "Raises:",
        "ValueError",
        "EnergiDataServiceError",
        "RuntimeError",
    ):
        assert section in doc, (function.__name__, section)
    assert ("`dso`" in doc) == ("dso" in function.__name__)


def test_the_tax_docstring_says_the_reduced_rate_is_not_published() -> None:
    doc = " ".join((get_electricity_tax.__doc__ or "").split())

    assert "reduced rate" in doc
    assert "is not published in *DatahubPricelist*" in doc


def test_the_dso_docstrings_say_the_name_is_case_insensitive() -> None:
    for function in (get_dso_tariffs, get_dso_subscriptions):
        assert "case-insensitively" in (function.__doc__ or "")


def test_the_subscription_docstring_names_sunds_and_the_missing_request() -> None:
    doc = " ".join((get_dso_subscriptions.__doc__ or "").split())

    assert "sunds" in doc
    assert "no request is made" in doc


def test_the_package_reexports_the_public_names() -> None:
    for name in (
        "Dso",
        "DSOS",
        "get_dso_tariffs",
        "get_energinet_tariffs",
        "get_dso_subscriptions",
        "get_energinet_subscriptions",
        "get_electricity_tax",
    ):
        assert hasattr(package, name), name
    assert package.get_dso_tariffs is pricelist.get_dso_tariffs
    assert package.DSOS is DSOS


def test_dso_is_a_frozen_dataclass_in_the_package() -> None:
    assert dataclasses.is_dataclass(package.Dso)


# --- showcase -----------------------------------------------------------------------------------------


def test_main_prints_each_showcase_against_a_stub(
    monkeypatch: pytest.MonkeyPatch,
    pricelist_service: MarketsService,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _owned(monkeypatch, pricelist_service)

    main()

    out = capsys.readouterr().out
    assert "radius tariff, DKK/kWh, 24 hours" in out
    assert "0.955573" in out
    assert "0.008" in out
    assert "36.773011" in out
    assert len(pricelist_service.requests) == 4


def test_the_max_span_the_functions_use_covers_the_whole_history(
    pricelist_service: MarketsService,
) -> None:
    get_electricity_tax(
        "2026-06-01", client=pricelist_service.client(timedelta(days=1))
    )

    assert len(pricelist_service.requests) == 1  # not 4000 one-day windows
