"""`get_metering_points` and `get_metering_point_details`."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import pytest
from conftest import (
    ELOVERBLIK_POINT_A,
    ELOVERBLIK_POINT_B,
    EloverblikService,
    eloverblik_details,
    eloverblik_point,
    eloverblik_point_id,
)

from energydata.eloverblik import (
    EloverblikError,
    get_metering_point_details,
    get_metering_points,
)
from energydata.eloverblik.meteringpoints import _POINT_COLUMNS, _snake

SPEC = json.loads(
    (Path(__file__).parent / "fixtures" / "eloverblik_openapi.json").read_text(
        encoding="utf-8"
    )
)
DETAIL_PROPS: dict[str, Any] = SPEC["components"]["schemas"][
    "MeteringPointDetailsCustomerDto"
]["properties"]
SCALARS = [
    n for n in DETAIL_PROPS if n not in ("contactAddresses", "childMeteringPoints")
]
DETAILS = "/meteringpoints/meteringpoint/getdetails"


def _answer(
    body: object, status: int = 200
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    return handler


# --- get_metering_points ---------------------------------------------------------


def test_metering_points_columns_dtypes_and_values() -> None:
    service = EloverblikService(
        [
            eloverblik_point(
                ELOVERBLIK_POINT_A,
                childMeteringPoints=[{"meteringPointId": ELOVERBLIK_POINT_B}],
            )
        ]
    )

    with service.client() as client:
        frame = get_metering_points(client=client)

    assert list(frame.columns) == list(_POINT_COLUMNS)
    assert len(_POINT_COLUMNS) == 20
    assert isinstance(frame.index, pd.RangeIndex)
    row = frame.iloc[0]
    assert row["metering_point_id"] == ELOVERBLIK_POINT_A
    assert row["type_of_mp"] == "E17"
    assert row["settlement_method"] == "D01"
    assert row["balance_supplier_name"] == "Test Supplier"
    assert row["child_metering_point_ids"] == (ELOVERBLIK_POINT_B,)
    assert frame["has_relation"].dtype == bool
    assert frame["is_moved_out"].dtype == bool
    assert bool(row["has_relation"]) is True
    assert bool(row["is_moved_out"]) is False
    for name in _POINT_COLUMNS:
        if name not in ("has_relation", "is_moved_out"):
            assert frame[name].dtype == object


def test_metering_points_use_include_all_false_by_default() -> None:
    service = EloverblikService()
    service.unlinked = [eloverblik_point(ELOVERBLIK_POINT_B)]

    with service.client() as client:
        frame = get_metering_points(client=client)

    assert service.calls("/meteringpoints")[0].url.params["includeAll"] == "false"
    assert list(frame["metering_point_id"]) == [ELOVERBLIK_POINT_A]


def test_metering_points_include_unlinked_sends_include_all_true() -> None:
    service = EloverblikService()
    service.unlinked = [eloverblik_point(ELOVERBLIK_POINT_B)]

    with service.client() as client:
        frame = get_metering_points(include_unlinked=True, client=client)

    assert service.calls("/meteringpoints")[0].url.params["includeAll"] == "true"
    assert list(frame["metering_point_id"]) == [ELOVERBLIK_POINT_A, ELOVERBLIK_POINT_B]


def test_metering_points_empty_account_keeps_columns_and_dtypes() -> None:
    service = EloverblikService([])

    with service.client() as client:
        frame = get_metering_points(client=client)

    assert len(frame) == 0
    assert list(frame.columns) == list(_POINT_COLUMNS)
    assert frame["has_relation"].dtype == bool
    assert frame["is_moved_out"].dtype == bool
    assert frame["postcode"].dtype == object


def test_metering_points_have_no_name_or_cpr_cvr_column() -> None:
    service = EloverblikService(
        [
            eloverblik_point(
                firstConsumerPartyName="Secret Name",
                consumerCVR="99887766",
                dataAccessCVR="87654321",
            )
        ]
    )

    with service.client() as client:
        frame = get_metering_points(client=client)

    allowed = {
        "street_name",
        "city_name",
        "city_sub_division_name",
        "balance_supplier_name",
    }
    for name in frame.columns:
        assert "cvr" not in name
        assert "consumer" not in name or name == "consumer_start_date"
        assert "name" not in name or name in allowed
    text = frame.to_string()
    assert "Secret Name" not in text
    assert "99887766" not in text
    assert "87654321" not in text


@pytest.mark.parametrize(
    ("children", "expected"),
    [
        ("absent", ()),
        (None, ()),
        ([], ()),
        ([{"meteringPointId": eloverblik_point_id(2)}], (eloverblik_point_id(2),)),
        (
            [
                {"meteringPointId": eloverblik_point_id(2)},
                {"meteringPointId": eloverblik_point_id(3)},
            ],
            (eloverblik_point_id(2), eloverblik_point_id(3)),
        ),
    ],
)
def test_metering_points_child_ids_are_tuples(
    children: object, expected: tuple[str, ...]
) -> None:
    point = eloverblik_point()
    if children == "absent":
        del point["childMeteringPoints"]
    else:
        point["childMeteringPoints"] = children
    service = EloverblikService([point])

    with service.client() as client:
        frame = get_metering_points(client=client)

    value = frame["child_metering_point_ids"].iloc[0]
    assert isinstance(value, tuple)
    assert value == expected


@pytest.mark.parametrize(
    "children",
    ["x", {"meteringPointId": "1"}, [{"meteringPointId": None}], ["text"], [{}]],
)
def test_metering_points_malformed_children_raise(children: object) -> None:
    service = EloverblikService([eloverblik_point(childMeteringPoints=children)])

    with service.client() as client, pytest.raises(EloverblikError):
        get_metering_points(client=client)


@pytest.mark.parametrize(
    "change",
    [
        {"hasRelation": None},
        {"hasRelation": "true"},
        {"hasRelation": 1},
        {"isMovedOut": None},
        {"isMovedOut": "false"},
        {"meteringPointId": None},
        {"meteringPointId": 571313100000000001},
        {"postcode": 8000},
        {"streetName": ["x"]},
    ],
)
def test_metering_points_wrong_types_raise_naming_the_field(
    change: dict[str, Any],
) -> None:
    service = EloverblikService([eloverblik_point(**change)])
    field = next(iter(change))

    with service.client() as client, pytest.raises(EloverblikError, match=field):
        get_metering_points(client=client)


@pytest.mark.parametrize("absent", ["hasRelation", "isMovedOut", "meteringPointId"])
def test_metering_points_missing_required_fields_raise(absent: str) -> None:
    point = eloverblik_point()
    del point[absent]
    service = EloverblikService([point])

    with service.client() as client, pytest.raises(EloverblikError, match=absent):
        get_metering_points(client=client)


def test_metering_points_absent_or_null_text_fields_are_none() -> None:
    point = eloverblik_point(consumerStartDate=None, floorId=None)
    del point["roomId"]
    service = EloverblikService([point])

    with service.client() as client:
        frame = get_metering_points(client=client)

    row = frame.iloc[0]
    assert row["consumer_start_date"] is None
    assert row["floor_id"] is None
    assert row["room_id"] is None


@pytest.mark.parametrize(
    "body", [{}, {"result": {}}, {"result": None}, {"result": "x"}]
)
def test_metering_points_result_that_is_not_a_list_raises(body: object) -> None:
    service = EloverblikService()
    service.overrides["meteringpoints"] = _answer(body)

    with service.client() as client, pytest.raises(EloverblikError, match="result"):
        get_metering_points(client=client)


def test_metering_points_item_that_is_not_an_object_raises() -> None:
    service = EloverblikService()
    service.overrides["meteringpoints"] = _answer({"result": ["x"]})

    with service.client() as client, pytest.raises(EloverblikError, match="not a JSON"):
        get_metering_points(client=client)


def test_metering_points_keep_listing_order_and_are_idempotent() -> None:
    service = EloverblikService(
        [eloverblik_point(ELOVERBLIK_POINT_B), eloverblik_point(ELOVERBLIK_POINT_A)]
    )

    with service.client() as client:
        first = get_metering_points(client=client)
        second = get_metering_points(client=client)

    assert list(first["metering_point_id"]) == [ELOVERBLIK_POINT_B, ELOVERBLIK_POINT_A]
    pd.testing.assert_frame_equal(first, second)
    assert service.token_calls == 1


def test_metering_points_rejects_positional_arguments() -> None:
    with pytest.raises(TypeError):
        get_metering_points(True)  # type: ignore[misc]  # keyword-only on purpose


# --- get_metering_point_details ----------------------------------------------------


def test_details_have_a_column_for_every_spec_scalar_in_spec_order() -> None:
    service = EloverblikService()

    with service.client() as client:
        frame = get_metering_point_details(client=client)

    assert len(SCALARS) == 57
    assert list(frame.columns) == [_snake(n) for n in SCALARS]
    assert len(set(frame.columns)) == 57
    assert "contact_addresses" not in frame.columns
    assert "child_metering_points" not in frame.columns


@pytest.mark.parametrize(
    ("api", "column"),
    [
        ("typeOfMP", "type_of_mp"),
        ("powerLimitKW", "power_limit_kw"),
        ("powerLimitKWDecimal", "power_limit_kw_decimal"),
        ("powerLimitA", "power_limit_a"),
        ("consumerCVR", "consumer_cvr"),
        ("dataAccessCVR", "data_access_cvr"),
        ("physicalStatusOfMP", "physical_status_of_mp"),
        ("mpRelationType", "mp_relation_type"),
        ("gridOperatorID", "grid_operator_id"),
        (
            "gridOperatorID_SchemeAgencyIdentifier",
            "grid_operator_id_scheme_agency_identifier",
        ),
        (
            "balanceSupplierId_SchemeAgencyIdentifier",
            "balance_supplier_id_scheme_agency_identifier",
        ),
        ("meteringPointId", "metering_point_id"),
        ("floorId", "floor_id"),
    ],
)
def test_snake_case_mapping(api: str, column: str) -> None:
    assert _snake(api) == column


def test_details_values_and_dtypes() -> None:
    service = EloverblikService()

    with service.client() as client:
        frame = get_metering_point_details(client=client)

    assert isinstance(frame.index, pd.RangeIndex)
    assert frame["power_limit_kw_decimal"].dtype == "float64"
    assert frame["power_limit_kw_decimal"].iloc[0] == 6.5
    assert frame["metering_point_id"].iloc[0] == ELOVERBLIK_POINT_A
    assert frame["type_of_mp"].iloc[0] == "typeOfMP-value"
    assert frame["parent_metering_point_id"].iloc[0] is None
    assert frame["balance_supplier_start_date"].iloc[0] is None
    for name in frame.columns:
        if name != "power_limit_kw_decimal":
            assert frame[name].dtype == object


@pytest.mark.parametrize(
    ("value", "expected"),
    [(11.5, 11.5), (11, 11.0), (None, None), ("absent", None), (0, 0.0), (-2.5, -2.5)],
)
def test_details_power_limit_decimal(value: object, expected: float | None) -> None:
    body = eloverblik_details()
    if value == "absent":
        del body["powerLimitKWDecimal"]
    else:
        body["powerLimitKWDecimal"] = value
    service = EloverblikService()
    service.details[ELOVERBLIK_POINT_A] = body

    with service.client() as client:
        frame = get_metering_point_details(client=client)

    got = frame["power_limit_kw_decimal"].iloc[0]
    assert frame["power_limit_kw_decimal"].dtype == "float64"
    if expected is None:
        assert pd.isna(got)
    else:
        assert got == expected


@pytest.mark.parametrize("value", ["11.5", True, [1], {"a": 1}])
def test_details_power_limit_decimal_wrong_type_raises(value: object) -> None:
    service = EloverblikService()
    service.details[ELOVERBLIK_POINT_A] = eloverblik_details(powerLimitKWDecimal=value)

    with (
        service.client() as client,
        pytest.raises(EloverblikError, match="powerLimitKWDecimal"),
    ):
        get_metering_point_details(client=client)


def test_details_power_limit_decimal_beyond_float_raises() -> None:
    result = json.dumps(eloverblik_details()).replace(
        '"powerLimitKWDecimal": 6.5', '"powerLimitKWDecimal": 1e999'
    )
    raw = f'{{"result": [{{"success": true, "id": "{ELOVERBLIK_POINT_A}", "result": {result}}}]}}'
    service = EloverblikService()
    service.overrides["getdetails"] = lambda _r: httpx.Response(
        200, content=raw.encode(), headers={"Content-Type": "application/json"}
    )

    with service.client() as client, pytest.raises(EloverblikError):
        get_metering_point_details(client=client)


def test_details_numeric_text_field_is_refused() -> None:
    service = EloverblikService()
    service.details[ELOVERBLIK_POINT_A] = eloverblik_details(meterCounterDigits=5)

    with (
        service.client() as client,
        pytest.raises(EloverblikError, match="meterCounterDigits"),
    ):
        get_metering_point_details(client=client)


def test_details_nested_fields_are_dropped_not_parsed() -> None:
    service = EloverblikService()
    service.details[ELOVERBLIK_POINT_A] = eloverblik_details(
        contactAddresses="garbage", childMeteringPoints=42
    )

    with service.client() as client:
        frame = get_metering_point_details(client=client)

    assert len(frame) == 1


def test_details_absent_scalar_counts_as_null() -> None:
    body = eloverblik_details()
    del body["meterNumber"]
    service = EloverblikService()
    service.details[ELOVERBLIK_POINT_A] = body

    with service.client() as client:
        frame = get_metering_point_details(client=client)

    assert frame["meter_number"].iloc[0] is None


def test_details_keep_request_order_even_when_the_response_is_reversed() -> None:
    service = EloverblikService(
        [eloverblik_point(ELOVERBLIK_POINT_A), eloverblik_point(ELOVERBLIK_POINT_B)]
    )
    service.out_of_order = True

    with service.client() as client:
        frame = get_metering_point_details(
            [ELOVERBLIK_POINT_B, ELOVERBLIK_POINT_A], client=client
        )

    assert list(frame["metering_point_id"]) == [ELOVERBLIK_POINT_B, ELOVERBLIK_POINT_A]


def test_details_none_uses_the_linked_points_in_listing_order() -> None:
    service = EloverblikService(
        [eloverblik_point(ELOVERBLIK_POINT_B), eloverblik_point(ELOVERBLIK_POINT_A)]
    )

    with service.client() as client:
        frame = get_metering_point_details(client=client)

    assert service.calls("/meteringpoints")[0].url.params["includeAll"] == "false"
    assert list(frame["metering_point_id"]) == [ELOVERBLIK_POINT_B, ELOVERBLIK_POINT_A]


def test_details_with_no_linked_point_is_a_value_error_naming_the_portal() -> None:
    service = EloverblikService([])

    with service.client() as client, pytest.raises(ValueError, match="portal"):
        get_metering_point_details(client=client)

    assert len(service.requests) == 2  # the token and the listing only


def test_details_per_point_error_raises_with_code_point_and_status() -> None:
    service = EloverblikService(
        [eloverblik_point(ELOVERBLIK_POINT_A), eloverblik_point(ELOVERBLIK_POINT_B)]
    )
    service.detail_codes = {ELOVERBLIK_POINT_B: 20008}

    with service.client() as client, pytest.raises(EloverblikError) as caught:
        get_metering_point_details(
            [ELOVERBLIK_POINT_A, ELOVERBLIK_POINT_B], client=client
        )

    assert caught.value.status == 200
    assert caught.value.error_code == 20008
    assert caught.value.error_text == "Metering point not found"
    assert caught.value.metering_point_id == ELOVERBLIK_POINT_B


def test_details_an_unknown_point_is_not_found() -> None:
    service = EloverblikService()

    with service.client() as client, pytest.raises(EloverblikError) as caught:
        get_metering_point_details(eloverblik_point_id(77), client=client)

    assert caught.value.error_code == 20008
    assert caught.value.metering_point_id == eloverblik_point_id(77)


def test_details_success_without_a_result_object_raises() -> None:
    service = EloverblikService()
    service.overrides["getdetails"] = _answer(
        {"result": [{"success": True, "id": ELOVERBLIK_POINT_A, "result": None}]}
    )

    with service.client() as client, pytest.raises(EloverblikError, match="result"):
        get_metering_point_details(client=client)


@pytest.mark.parametrize("success", ["true", 1, None, "absent"])
def test_details_only_a_literal_true_counts_as_success(success: object) -> None:
    item: dict[str, Any] = {"id": ELOVERBLIK_POINT_A, "result": eloverblik_details()}
    if success != "absent":
        item["success"] = success
    service = EloverblikService()
    service.overrides["getdetails"] = _answer({"result": [item]})

    with service.client() as client, pytest.raises(EloverblikError) as caught:
        get_metering_point_details(client=client)

    assert caught.value.status == 200
    assert caught.value.metering_point_id == ELOVERBLIK_POINT_A


def test_details_a_repeated_result_gives_a_row_each() -> None:
    item = {
        "success": True,
        "id": ELOVERBLIK_POINT_A,
        "result": eloverblik_details(),
    }
    service = EloverblikService()
    service.overrides["getdetails"] = _answer({"result": [item, item]})

    with service.client() as client:
        frame = get_metering_point_details(client=client)

    assert list(frame["metering_point_id"]) == [ELOVERBLIK_POINT_A] * 2


def test_details_do_not_mutate_a_passed_list_and_are_idempotent() -> None:
    service = EloverblikService()
    ids = [f"  {ELOVERBLIK_POINT_A} "]

    with service.client() as client:
        first = get_metering_point_details(ids, client=client)
        second = get_metering_point_details(ids, client=client)

    assert ids == [f"  {ELOVERBLIK_POINT_A} "]
    pd.testing.assert_frame_equal(first, second)


def test_details_request_body_names_the_stripped_id() -> None:
    service = EloverblikService()

    with service.client() as client:
        get_metering_point_details(f"  {ELOVERBLIK_POINT_A}\n", client=client)

    body = json.loads(service.calls("/getdetails")[0].content)
    assert body == {"meteringPoints": {"meteringPoint": [ELOVERBLIK_POINT_A]}}
    assert len(service.calls("/meteringpoints")) == 0
