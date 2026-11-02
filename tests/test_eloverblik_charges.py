"""`get_charges`."""

import httpx
import pandas as pd
import pytest
from conftest import (
    ELOVERBLIK_POINT_A,
    ELOVERBLIK_POINT_B,
    EloverblikService,
    eloverblik_charge,
    eloverblik_point,
    eloverblik_tariff,
)

from energydata.eloverblik import EloverblikError, get_charges

COLUMNS = [
    "metering_point_id",
    "kind",
    "price_id",
    "name",
    "description",
    "owner",
    "period_type",
    "valid_from",
    "valid_to",
    "price",
    "quantity",
    "position",
]
CPH = "Europe/Copenhagen"


def _one(
    subscriptions: list[dict[str, object]] | None = None,
    tariffs: list[dict[str, object]] | None = None,
    fees: list[dict[str, object]] | None = None,
    point: str = ELOVERBLIK_POINT_A,
) -> pd.DataFrame:
    service = EloverblikService()
    service.charges[point] = {
        "subscriptions": subscriptions or [],
        "tariffs": tariffs or [],
        "fees": fees or [],
    }
    with service.client() as client:
        return get_charges(point, client=client)


def _price(position: object, price: float = 0.1) -> dict[str, object]:
    return {"position": position, "price": price}


def test_default_charges_rows_kinds_and_order() -> None:
    service = EloverblikService()

    with service.client() as client:
        frame = get_charges(client=client)

    assert list(frame.columns) == COLUMNS
    assert isinstance(frame.index, pd.RangeIndex)
    assert list(frame["kind"]) == ["subscription"] + ["tariff"] * 24 + ["fee"]
    assert list(frame["position"].iloc[1:25]) == list(range(1, 25))
    assert frame["position"].iloc[0] is pd.NA
    assert frame["position"].iloc[-1] is pd.NA
    assert frame["quantity"].iloc[0] == 1
    assert frame["quantity"].iloc[1] is pd.NA
    assert frame["price"].iloc[0] == 49.5
    assert frame["price"].iloc[5] == pytest.approx(0.5)
    assert frame["price"].iloc[-1] == 0.25
    assert (frame["metering_point_id"] == ELOVERBLIK_POINT_A).all()


def test_dtypes() -> None:
    frame = _one([eloverblik_charge()], [eloverblik_tariff(prices=2)])

    for name in (
        "metering_point_id",
        "kind",
        "price_id",
        "name",
        "description",
        "owner",
    ):
        assert frame[name].dtype == object
    assert frame["period_type"].dtype == object
    assert frame["valid_from"].dtype == pd.DatetimeTZDtype("ns", CPH)
    assert frame["valid_to"].dtype == pd.DatetimeTZDtype("ns", CPH)
    assert frame["price"].dtype == "float64"
    assert frame["quantity"].dtype == "Int64"
    assert frame["position"].dtype == "Int64"


def test_a_charge_carries_its_text_fields() -> None:
    frame = _one(
        [
            eloverblik_charge(
                "S9", name="Abonnement", description="Beskrivelse æøå", owner="42"
            )
        ]
    )

    row = frame.iloc[0]
    assert (row["price_id"], row["name"]) == ("S9", "Abonnement")
    assert row["description"] == "Beskrivelse æøå"
    assert row["owner"] == "42"
    assert row["period_type"] == "P1M"


def test_rows_per_point_follow_request_order_and_kind_order() -> None:
    service = EloverblikService(
        [eloverblik_point(ELOVERBLIK_POINT_A), eloverblik_point(ELOVERBLIK_POINT_B)]
    )
    for point in (ELOVERBLIK_POINT_A, ELOVERBLIK_POINT_B):
        service.charges[point] = {
            "fees": [eloverblik_charge("F")],
            "tariffs": [eloverblik_tariff("T", prices=2)],
            "subscriptions": [eloverblik_charge("S")],
        }

    with service.client() as client:
        frame = get_charges([ELOVERBLIK_POINT_B, ELOVERBLIK_POINT_A], client=client)

    expected = [
        (p, k)
        for p in (ELOVERBLIK_POINT_B, ELOVERBLIK_POINT_A)
        for k in ("subscription", "tariff", "tariff", "fee")
    ]
    assert list(zip(frame["metering_point_id"], frame["kind"], strict=True)) == expected


def test_tariff_rows_are_sorted_by_position() -> None:
    frame = _one(
        tariffs=[
            {
                **eloverblik_tariff(prices=0),
                "prices": [_price("3", 0.3), _price("1", 0.1), _price("2", 0.2)],
            }
        ]
    )

    assert list(frame["position"]) == [1, 2, 3]
    assert list(frame["price"]) == [0.1, 0.2, 0.3]


def test_tariff_positions_sort_numerically_not_as_text() -> None:
    frame = _one(
        tariffs=[{**eloverblik_tariff(prices=0), "prices": [_price("10"), _price("9")]}]
    )

    assert list(frame["position"]) == [9, 10]


def test_equal_positions_keep_the_api_order() -> None:
    frame = _one(
        tariffs=[
            {
                **eloverblik_tariff(prices=0),
                "prices": [_price("1", 0.5), _price("1", 0.4)],
            }
        ]
    )

    assert list(frame["price"]) == [0.5, 0.4]


def test_a_null_tariff_position_is_na_and_sorts_last() -> None:
    frame = _one(
        tariffs=[
            {
                **eloverblik_tariff(prices=0),
                "prices": [_price(None, 0.9), _price("2", 0.2), _price("1", 0.1)],
            }
        ]
    )

    assert frame["position"].isna().tolist() == [False, False, True]
    assert list(frame["price"]) == [0.1, 0.2, 0.9]
    assert frame["position"].dtype == "Int64"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("7", 7), (" 7 ", 7), (7, 7), (7.0, 7), ("007", 7), ("-1", -1), ("0", 0)],
)
def test_tariff_position_parsing(raw: object, expected: int) -> None:
    frame = _one(tariffs=[{**eloverblik_tariff(prices=0), "prices": [_price(raw)]}])

    assert frame["position"].iloc[0] == expected


@pytest.mark.parametrize("raw", ["7.5", "3.0", "x", "", 7.5, True, [1], {"a": 1}])
def test_a_tariff_position_that_is_not_an_integer_raises(raw: object) -> None:
    with pytest.raises(EloverblikError, match="position"):
        _one(tariffs=[{**eloverblik_tariff(prices=0), "prices": [_price(raw)]}])


@pytest.mark.parametrize("prices", [[], None, "absent"])
def test_a_tariff_without_prices_is_one_nan_row(prices: object) -> None:
    tariff = eloverblik_tariff("T1", prices=0)
    if prices == "absent":
        del tariff["prices"]
    else:
        tariff["prices"] = prices

    frame = _one(tariffs=[tariff])

    assert len(frame) == 1
    assert frame["kind"].iloc[0] == "tariff"
    assert frame["price_id"].iloc[0] == "T1"
    assert pd.isna(frame["price"].iloc[0])
    assert frame["position"].iloc[0] is pd.NA
    assert frame["quantity"].iloc[0] is pd.NA


@pytest.mark.parametrize("prices", [{}, "x", 5])
def test_tariff_prices_that_are_not_a_list_raise(prices: object) -> None:
    with pytest.raises(EloverblikError, match="prices"):
        _one(tariffs=[{**eloverblik_tariff(prices=0), "prices": prices}])


@pytest.mark.parametrize("entry", ["x", 5, None, []])
def test_a_price_entry_that_is_not_an_object_raises(entry: object) -> None:
    with pytest.raises(EloverblikError, match="not a JSON object"):
        _one(tariffs=[{**eloverblik_tariff(prices=0), "prices": [entry]}])


@pytest.mark.parametrize("price", ["1.5", None, True, "absent"])
def test_a_tariff_price_that_is_not_a_number_raises(price: object) -> None:
    entry: dict[str, object] = {"position": "1", "price": price}
    if price == "absent":
        del entry["price"]

    with pytest.raises(EloverblikError, match="price"):
        _one(tariffs=[{**eloverblik_tariff(prices=0), "prices": [entry]}])


def test_subscription_and_fee_quantity_and_price() -> None:
    frame = _one(
        [eloverblik_charge("S", quantity=2, price=10)],
        fees=[eloverblik_charge("F", quantity="3", price=0.5)],
    )

    assert list(frame["quantity"]) == [2, 3]
    assert list(frame["price"]) == [10.0, 0.5]
    assert frame["price"].dtype == "float64"


@pytest.mark.parametrize(
    "change",
    [
        {"quantity": None},
        {"price": None},
        {"quantity": 1.5},
        {"quantity": "x"},
        {"price": "49.5"},
        {"quantity": True},
    ],
)
def test_a_subscription_with_a_bad_quantity_or_price_raises(
    change: dict[str, object],
) -> None:
    with pytest.raises(EloverblikError):
        _one([eloverblik_charge(**change)])


@pytest.mark.parametrize("missing", ["quantity", "price"])
def test_a_subscription_without_quantity_or_price_raises(missing: str) -> None:
    charge = eloverblik_charge()
    del charge[missing]

    with pytest.raises(EloverblikError, match=missing):
        _one([charge])


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-01-01T00:00:00Z", "2026-01-01 01:00"),
        ("2026-01-01T00:00:00.000Z", "2026-01-01 01:00"),
        ("2026-01-01T00:00:00", "2026-01-01 00:00"),
        ("2026-01-01", "2026-01-01 00:00"),
        ("2025-12-31T23:00:00Z", "2026-01-01 00:00"),
        ("2026-07-01T00:00:00+02:00", "2026-07-01 00:00"),
        ("2026-07-01T00:00:00", "2026-07-01 00:00"),
    ],
)
def test_validity_dates_are_copenhagen_time(raw: str, expected: str) -> None:
    frame = _one([eloverblik_charge(validFromDate=raw)])

    assert frame["valid_from"].iloc[0] == pd.Timestamp(expected, tz=CPH)
    assert str(frame["valid_from"].dt.tz) == CPH


@pytest.mark.parametrize("raw", [None, ""])
def test_a_null_or_empty_validity_date_is_nat(raw: object) -> None:
    frame = _one([eloverblik_charge(validToDate=raw)])

    assert pd.isna(frame["valid_to"].iloc[0])
    assert frame["valid_to"].dtype == pd.DatetimeTZDtype("ns", CPH)


@pytest.mark.parametrize(
    "raw",
    [
        "garbage",
        "2026-13-45",
        "2026-10-25T02:30:00",  # ambiguous Danish time
        "2026-03-29T02:30:00",  # nonexistent Danish time
        "9999-12-31T23:00:00.000Z",
        "9999-12-31T00:00:00",
        "2262-04-12T00:00:00Z",
    ],
)
def test_an_unusable_validity_date_is_an_eloverblik_error_naming_the_field(
    raw: str,
) -> None:
    with pytest.raises(EloverblikError, match="validFromDate"):
        _one([eloverblik_charge(validFromDate=raw)])


@pytest.mark.parametrize("raw", [20260101, True, ["x"]])
def test_a_validity_date_that_is_not_text_raises(raw: object) -> None:
    with pytest.raises(EloverblikError, match="validFromDate"):
        _one([eloverblik_charge(validFromDate=raw)])


def test_no_charges_gives_an_empty_frame_with_the_dtypes() -> None:
    frame = _one()

    assert len(frame) == 0
    assert list(frame.columns) == COLUMNS
    assert frame["valid_from"].dtype == pd.DatetimeTZDtype("ns", CPH)
    assert frame["valid_to"].dtype == pd.DatetimeTZDtype("ns", CPH)
    assert frame["price"].dtype == "float64"
    assert frame["quantity"].dtype == "Int64"
    assert frame["position"].dtype == "Int64"
    assert frame["kind"].dtype == object


@pytest.mark.parametrize("key", ["subscriptions", "tariffs", "fees"])
def test_a_null_charge_list_counts_as_empty(key: str) -> None:
    service = EloverblikService()
    service.charges[ELOVERBLIK_POINT_A] = {
        "subscriptions": [],
        "tariffs": [],
        "fees": [],
    }
    service.charges[ELOVERBLIK_POINT_A][key] = None  # type: ignore[assignment]  # null list

    with service.client() as client:
        frame = get_charges(ELOVERBLIK_POINT_A, client=client)

    assert len(frame) == 0


@pytest.mark.parametrize("key", ["subscriptions", "tariffs", "fees"])
def test_a_charge_list_that_is_not_a_list_raises(key: str) -> None:
    service = EloverblikService()
    service.charges[ELOVERBLIK_POINT_A] = {
        "subscriptions": [],
        "tariffs": [],
        "fees": [],
    }
    service.charges[ELOVERBLIK_POINT_A][key] = {}  # type: ignore[assignment]  # wrong shape

    with service.client() as client, pytest.raises(EloverblikError, match=key):
        get_charges(ELOVERBLIK_POINT_A, client=client)


@pytest.mark.parametrize("item", ["x", 5, None, []])
def test_a_charge_that_is_not_an_object_raises(item: object) -> None:
    with pytest.raises(EloverblikError, match="not a JSON object"):
        _one([item])  # type: ignore[list-item]  # wrong shape


def test_a_result_that_is_not_an_object_raises() -> None:
    service = EloverblikService()
    service.overrides["getcharges"] = lambda _r: httpx.Response(
        200,
        json={"result": [{"success": True, "id": ELOVERBLIK_POINT_A, "result": []}]},
    )

    with service.client() as client, pytest.raises(EloverblikError, match="result"):
        get_charges(client=client)


def test_a_per_point_error_raises_with_code_text_and_point() -> None:
    service = EloverblikService()
    service.charge_codes = {ELOVERBLIK_POINT_A: 20012}

    with service.client() as client, pytest.raises(EloverblikError) as caught:
        get_charges(client=client)

    assert caught.value.status == 200
    assert caught.value.error_code == 20012
    assert caught.value.metering_point_id == ELOVERBLIK_POINT_A


def test_nothing_is_tolerated_for_charges() -> None:
    service = EloverblikService()
    service.charge_codes = {ELOVERBLIK_POINT_A: 30015}

    with service.client() as client, pytest.raises(EloverblikError) as caught:
        get_charges(client=client)

    assert caught.value.error_code == 30015


def test_charges_are_idempotent_and_do_not_mutate_the_id_list() -> None:
    service = EloverblikService()
    ids = [ELOVERBLIK_POINT_A]

    with service.client() as client:
        first = get_charges(ids, client=client)
        second = get_charges(ids, client=client)

    assert ids == [ELOVERBLIK_POINT_A]
    pd.testing.assert_frame_equal(first, second)


def test_unicode_in_text_fields_survives() -> None:
    frame = _one([eloverblik_charge(name="Nettarif – øre/kWh ☃")])

    assert frame["name"].iloc[0] == "Nettarif – øre/kWh ☃"
