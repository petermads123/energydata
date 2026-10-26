from collections.abc import Callable
from typing import Any

import httpx
import pandas as pd
import pytest
from conftest import ADDRESS_ID, MinStroemService, minstroem_address

from energydata.minstroem import (
    API_KEY_ENV,
    MinStroemError,
    MissingApiKeyError,
    _series,
    addresses,
    get_addresses,
)

OTHER = "2b4bf7a8-b5d0-45ab-8d42-3a5688bba167"
TEXT_COLUMNS = [
    "id",
    "label",
    "formatted_address",
    "region",
    "time_zone",
    "grid_operator_name",
    "supplier_name",
]
FLAG_COLUMNS = [
    "consumption",
    "solar_export",
    "consumption_costs",
    "solar_export_revenue",
    "prices",
    "forecast_prices",
]
COLUMNS = TEXT_COLUMNS + FLAG_COLUMNS

type Mutation = Callable[[dict[str, Any]], None]


def _serve(service: MinStroemService, body: object) -> None:
    service.overrides["addresses"] = lambda _r: httpx.Response(200, json=body)


def _check_shape(frame: pd.DataFrame) -> None:
    assert list(frame.columns) == COLUMNS
    assert all(frame[c].dtype == object for c in TEXT_COLUMNS)
    assert all(frame[c].dtype == bool for c in FLAG_COLUMNS)
    assert isinstance(frame.index, pd.RangeIndex)


def test_every_field_and_capability_maps_to_its_documented_column() -> None:
    full = minstroem_address(ADDRESS_ID, "Home")
    full["supplierName"] = "Strøm A/S"
    partial = minstroem_address(
        OTHER,
        "Sommerhus",
        forecastPrices=False,
        solarExport=False,
        consumptionCosts=False,
        solarExportRevenue=False,
        prices=False,
        consumption=False,
    )
    partial["region"] = None
    partial["gridOperatorName"] = None
    partial["supplierName"] = None
    partial["timeZone"] = "Europe/Stockholm"
    service = MinStroemService([full, partial])

    with service.client() as c:
        frame = get_addresses(client=c)

    _check_shape(frame)
    assert len(frame) == 2
    assert frame.loc[0, "id"] == ADDRESS_ID
    assert frame.loc[0, "label"] == "Home"
    assert frame.loc[0, "formatted_address"] == "Testvej 1, 1000 Testby"
    assert frame.loc[0, "region"] == "DK1"
    assert frame.loc[0, "time_zone"] == "Europe/Copenhagen"
    assert frame.loc[0, "grid_operator_name"] == "Test Net"
    assert frame.loc[0, "supplier_name"] == "Strøm A/S"
    assert frame[FLAG_COLUMNS].to_numpy().tolist()[0] == [True] * 6
    assert frame.loc[1, "id"] == OTHER
    assert frame.loc[1, "time_zone"] == "Europe/Stockholm"
    assert frame[FLAG_COLUMNS].to_numpy().tolist()[1] == [False] * 6


def test_each_capability_flag_lands_in_its_own_column() -> None:
    names = {
        "consumption": "consumption",
        "solarExport": "solar_export",
        "consumptionCosts": "consumption_costs",
        "solarExportRevenue": "solar_export_revenue",
        "prices": "prices",
        "forecastPrices": "forecast_prices",
    }
    addresses_ = []
    for position, api in enumerate(names):
        address = minstroem_address(
            f"00000000-0000-4000-8000-00000000000{position}", api
        )
        address["capabilities"][api] = False
        addresses_.append(address)
    service = MinStroemService(addresses_)

    with service.client() as c:
        frame = get_addresses(client=c)

    for position, (api, column) in enumerate(names.items()):
        row = frame.iloc[position]
        assert row["label"] == api
        assert not row[column]
        assert all(row[other] for other in FLAG_COLUMNS if other != column)


def test_null_fields_stay_none_not_nan() -> None:
    address = minstroem_address()
    address["region"] = None
    address["gridOperatorName"] = None
    service = MinStroemService([address])

    with service.client() as c:
        frame = get_addresses(client=c)

    assert frame.loc[0, "region"] is None
    assert frame.loc[0, "grid_operator_name"] is None
    assert frame.loc[0, "supplier_name"] is None


def test_text_is_kept_exactly_as_sent() -> None:
    address = minstroem_address(label="  Sommerhus Ærø  ")
    address["formattedAddress"] = "Ærøvej 3, 5970 Ærøskøbing"
    service = MinStroemService([address])

    with service.client() as c:
        frame = get_addresses(client=c)

    assert frame.loc[0, "label"] == "  Sommerhus Ærø  "
    assert frame.loc[0, "formatted_address"] == "Ærøvej 3, 5970 Ærøskøbing"


def test_addresses_keep_the_order_the_api_sent_them_in() -> None:
    ids = [
        "00000000-0000-4000-8000-000000000003",
        "00000000-0000-4000-8000-000000000001",
        "00000000-0000-4000-8000-000000000002",
    ]
    service = MinStroemService([minstroem_address(i, f"label {i[-1]}") for i in ids])

    with service.client() as c:
        frame = get_addresses(client=c)

    assert frame["id"].tolist() == ids
    assert list(frame.index) == [0, 1, 2]


def test_no_addresses_is_an_empty_frame_with_the_same_columns_and_dtypes() -> None:
    service = MinStroemService(addresses=[])

    with service.client() as c:
        frame = get_addresses(client=c)

    assert len(frame) == 0
    _check_shape(frame)


def test_extra_fields_and_capabilities_are_ignored() -> None:
    address = minstroem_address()
    address["capabilities"]["evCharging"] = True
    address["somethingNew"] = {"nested": [1, 2]}
    service = MinStroemService([address])

    with service.client() as c:
        frame = get_addresses(client=c)

    _check_shape(frame)
    assert len(frame) == 1


def _drop(key: str) -> Mutation:
    return lambda address: address.pop(key)


def _set(key: str, value: object) -> Mutation:
    return lambda address: address.__setitem__(key, value)


def _drop_capability(key: str) -> Mutation:
    return lambda address: address["capabilities"].pop(key)


def _set_capability(key: str, value: object) -> Mutation:
    return lambda address: address["capabilities"].__setitem__(key, value)


MALFORMED: list[tuple[str, Mutation]] = [
    ("id missing", _drop("id")),
    ("id number", _set("id", 7)),
    ("id null", _set("id", None)),
    ("label missing", _drop("label")),
    ("label null", _set("label", None)),
    ("label number", _set("label", 5)),
    ("formattedAddress missing", _drop("formattedAddress")),
    ("formattedAddress null", _set("formattedAddress", None)),
    ("timeZone missing", _drop("timeZone")),
    ("timeZone null", _set("timeZone", None)),
    # Nullable, but still required to be present.
    ("region missing", _drop("region")),
    ("region number", _set("region", 1)),
    ("gridOperatorName missing", _drop("gridOperatorName")),
    ("gridOperatorName list", _set("gridOperatorName", ["x"])),
    ("supplierName missing", _drop("supplierName")),
    ("capabilities missing", _drop("capabilities")),
    ("capabilities null", _set("capabilities", None)),
    ("capabilities list", _set("capabilities", [])),
    ("capabilities text", _set("capabilities", "all")),
    ("forecastPrices text", _set_capability("forecastPrices", "true")),
    ("forecastPrices number", _set_capability("forecastPrices", 1)),
    ("forecastPrices null", _set_capability("forecastPrices", None)),
    ("prices missing", _drop_capability("prices")),
    ("consumption missing", _drop_capability("consumption")),
    ("solarExportRevenue number", _set_capability("solarExportRevenue", 0)),
]


@pytest.mark.parametrize("mutate", [pytest.param(m, id=name) for name, m in MALFORMED])
def test_a_malformed_address_is_a_minstroem_error_without_status(
    mutate: Mutation,
) -> None:
    address = minstroem_address()
    mutate(address)
    service = MinStroemService([address])

    with service.client() as c, pytest.raises(MinStroemError) as caught:
        get_addresses(client=c)

    error = caught.value
    assert (error.status, error.error_code, error.reason, error.request_id) == (
        None,
        None,
        None,
        None,
    )
    assert "/me/addresses" in str(error)


def test_a_malformed_address_among_good_ones_fails_the_whole_call() -> None:
    bad = minstroem_address(OTHER, "bad")
    bad["capabilities"]["prices"] = "yes"
    service = MinStroemService([minstroem_address(), bad])

    with service.client() as c, pytest.raises(MinStroemError, match="address 1"):
        get_addresses(client=c)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"items": []},
        {"data": None},
        {"data": {}},
        {"data": "x"},
        {"data": ["x"]},
        {"data": [[]]},
        {"data": [None]},
    ],
    ids=repr,
)
def test_a_payload_that_is_not_a_list_of_address_objects_is_a_minstroem_error(
    body: dict[str, Any],
) -> None:
    service = MinStroemService()
    _serve(service, body)

    with service.client() as c, pytest.raises(MinStroemError) as caught:
        get_addresses(client=c)

    assert caught.value.status is None


def test_one_get_request_to_the_address_list_with_no_parameters() -> None:
    service = MinStroemService()

    with service.client() as c:
        get_addresses(client=c)

    (request,) = service.requests
    assert request.method == "GET"
    assert request.url.path == "/consumer/v1/me/addresses"
    assert dict(request.url.params) == {}
    assert request.headers["Authorization"] == "Bearer test-key"


def test_an_api_error_keeps_status_code_and_the_http_error_as_its_cause() -> None:
    service = MinStroemService()
    body = {"error": True, "reason": "Plus needed.", "errorCode": "plus_required"}
    service.overrides["addresses"] = lambda _r: httpx.Response(403, json=body)

    with service.client() as c, pytest.raises(MinStroemError) as caught:
        get_addresses(client=c)

    error = caught.value
    assert (error.status, error.error_code, error.reason) == (
        403,
        "plus_required",
        "Plus needed.",
    )
    assert isinstance(error.__cause__, httpx.HTTPStatusError)


def test_a_wrong_key_is_a_401() -> None:
    service = MinStroemService()

    with service.client("not-the-key") as c, pytest.raises(MinStroemError) as caught:
        get_addresses(client=c)

    assert caught.value.status == 401
    assert caught.value.error_code == "invalid_token"


def test_without_a_key_the_call_fails_naming_the_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(API_KEY_ENV, raising=False)

    with pytest.raises(MissingApiKeyError, match=API_KEY_ENV):
        get_addresses()


def test_a_passed_client_is_left_open_and_gives_equal_frames_twice() -> None:
    service = MinStroemService([minstroem_address(), minstroem_address(OTHER, "b")])

    with service.client() as c:
        first = get_addresses(client=c)
        second = get_addresses(client=c)
        assert c.closed is False

    pd.testing.assert_frame_equal(first, second)


def test_the_client_argument_is_keyword_only() -> None:
    service = MinStroemService()

    with service.client() as c, pytest.raises(TypeError):
        get_addresses(c)  # type: ignore[misc]  # proves client is keyword-only


def test_main_without_a_key_prints_one_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(API_KEY_ENV, raising=False)

    addresses.main()

    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    assert API_KEY_ENV in lines[0]


def test_main_with_a_key_shows_the_capability_columns_but_not_the_address_text(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    minstroem_service: MinStroemService,
) -> None:
    monkeypatch.setenv(API_KEY_ENV, "test-key")
    monkeypatch.setattr(_series, "MinStroemClient", minstroem_service.client)

    addresses.main()

    out = capsys.readouterr().out
    assert "1 address(es)" in out
    assert "forecast_prices" in out
    assert "DK1" in out
    assert "Testvej" not in out
