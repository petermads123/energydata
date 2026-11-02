"""The shared path of the point-scoped Eloverblik functions, driven through them."""

import json
from typing import Any

import httpx
import pandas as pd
import pytest
from conftest import (
    ELOVERBLIK_POINT_A,
    ELOVERBLIK_POINT_B,
    ELOVERBLIK_REFRESH_TOKEN,
    EloverblikService,
    eloverblik_details,
    eloverblik_point,
    eloverblik_point_id,
)

from energydata.eloverblik import (
    EloverblikClient,
    EloverblikError,
    get_charges,
    get_meter_data,
    get_metering_point_details,
)
from energydata.eloverblik import _common as common
from energydata.eloverblik._common import (
    BATCH_SIZE,
    _batches,
    _normalise_ids,
    _number,
    _unpack,
)

CHARGES = "/meteringpoints/meteringpoint/getcharges"


def _bodies(service: EloverblikService, suffix: str) -> list[list[str]]:
    return [
        json.loads(r.content)["meteringPoints"]["meteringPoint"]
        for r in service.calls(suffix)
    ]


def _many(count: int) -> EloverblikService:
    return EloverblikService(
        [eloverblik_point(eloverblik_point_id(n)) for n in range(1, count + 1)]
    )


# --- id normalisation -------------------------------------------------------------


def test_a_string_is_one_stripped_id() -> None:
    assert _normalise_ids(f"  {ELOVERBLIK_POINT_A}\n") == [ELOVERBLIK_POINT_A]


def test_a_sequence_keeps_its_order_and_strips_each_id() -> None:
    ids = (f" {ELOVERBLIK_POINT_B}", ELOVERBLIK_POINT_A)

    assert _normalise_ids(ids) == [ELOVERBLIK_POINT_B, ELOVERBLIK_POINT_A]


def test_normalising_does_not_mutate_its_argument() -> None:
    ids = [f" {ELOVERBLIK_POINT_B}", ELOVERBLIK_POINT_A]

    _normalise_ids(ids)

    assert ids == [f" {ELOVERBLIK_POINT_B}", ELOVERBLIK_POINT_A]


@pytest.mark.parametrize(
    "bad",
    [
        "57131310000000001",  # 17 digits
        "5713131000000000010",  # 19 digits
        "57131310000000000a",
        "",
        "   ",
        "5713 1310000000001",
        "-71313100000000001",
        "５７１３１３１００００００００００１",  # full-width digits
        "٥٧١٣١٣١٠٠٠٠٠٠٠٠٠٠١",  # Arabic-Indic digits
        "57131310000000000１",  # one full-width digit
        "5713131000000000\n01",
    ],
)
def test_a_malformed_id_is_a_value_error_naming_it_before_any_request(bad: str) -> None:
    service = EloverblikService()
    with service.client() as client, pytest.raises(ValueError, match="18 digits") as e:
        get_charges(bad, client=client)

    assert repr(bad.strip()) in str(e.value) or repr(bad) in str(e.value)
    assert service.requests == []


@pytest.mark.parametrize("bad", [571313100000000001, None, 1.5, b"x"])
def test_a_non_text_id_in_a_list_is_a_value_error(bad: object) -> None:
    service = EloverblikService()

    with service.client() as client, pytest.raises(ValueError):
        get_charges([bad], client=client)  # type: ignore[list-item]  # malformed on purpose

    assert service.requests == []


def test_an_empty_sequence_is_a_value_error() -> None:
    service = EloverblikService()

    with service.client() as client, pytest.raises(ValueError, match="empty"):
        get_charges([], client=client)

    assert service.requests == []


def test_a_duplicate_id_is_a_value_error_naming_it() -> None:
    service = EloverblikService()

    with service.client() as client, pytest.raises(ValueError, match="twice") as e:
        get_charges([ELOVERBLIK_POINT_A, ELOVERBLIK_POINT_A], client=client)

    assert ELOVERBLIK_POINT_A in str(e.value)
    assert service.requests == []


def test_a_duplicate_after_stripping_is_still_a_duplicate() -> None:
    with pytest.raises(ValueError, match="twice"):
        _normalise_ids([ELOVERBLIK_POINT_A, f" {ELOVERBLIK_POINT_A}\t"])


def test_bytes_are_refused_by_name_not_as_integers() -> None:
    with pytest.raises(ValueError, match="bytes") as e:
        _normalise_ids(b"571313100000000001")  # type: ignore[arg-type]  # malformed on purpose

    assert "571313100000000001" in str(e.value)


# --- the linked-point default ----------------------------------------------------------


def test_none_lists_the_linked_points_only_and_uses_them_in_listing_order() -> None:
    service = EloverblikService(
        [eloverblik_point(ELOVERBLIK_POINT_B), eloverblik_point(ELOVERBLIK_POINT_A)]
    )
    service.unlinked = [eloverblik_point(eloverblik_point_id(9))]

    with service.client() as client:
        frame = get_charges(client=client)

    assert service.calls("/meteringpoints")[0].url.params["includeAll"] == "false"
    assert list(dict.fromkeys(frame["metering_point_id"])) == [
        ELOVERBLIK_POINT_B,
        ELOVERBLIK_POINT_A,
    ]


def test_no_linked_point_is_a_value_error_pointing_at_the_portal() -> None:
    service = EloverblikService([])

    with service.client() as client, pytest.raises(ValueError, match="portal"):
        get_charges(client=client)

    assert not service.calls(CHARGES)


def test_a_point_listed_twice_is_used_once() -> None:
    service = EloverblikService(
        [eloverblik_point(ELOVERBLIK_POINT_A), eloverblik_point(ELOVERBLIK_POINT_A)]
    )

    with service.client() as client:
        details = get_metering_point_details(client=client)
        frame = get_charges(client=client)

    assert len(details) == 1
    assert list(frame["metering_point_id"].unique()) == [ELOVERBLIK_POINT_A]
    assert _bodies(service, "/getcharges") == [[ELOVERBLIK_POINT_A]]


def test_a_listing_without_a_result_list_is_an_error() -> None:
    service = EloverblikService()
    service.overrides["meteringpoints"] = lambda _r: httpx.Response(200, json={})

    with service.client() as client, pytest.raises(EloverblikError, match="result"):
        get_charges(client=client)


# --- batching ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("count", "sizes"),
    [(1, [1]), (9, [9]), (10, [10]), (11, [10, 1]), (20, [10, 10]), (23, [10, 10, 3])],
)
def test_ids_are_batched_at_most_ten_in_order(count: int, sizes: list[int]) -> None:
    ids = [eloverblik_point_id(n) for n in range(1, count + 1)]

    batches = _batches(ids)

    assert BATCH_SIZE == 10
    assert [len(b) for b in batches] == sizes
    assert [i for b in batches for i in b] == ids


def test_no_ids_make_no_batch() -> None:
    assert _batches([]) == []


def test_23_linked_points_go_out_as_three_requests_of_10_10_3() -> None:
    service = _many(23)

    with service.client() as client:
        frame = get_charges(client=client)

    sizes = sorted(len(b) for b in _bodies(service, "/getcharges"))
    assert sizes == [3, 10, 10]
    sent = [i for b in _bodies(service, "/getcharges") for i in b]
    assert sorted(sent) == sorted(service.point_ids)
    assert list(dict.fromkeys(frame["metering_point_id"])) == service.point_ids


# --- result matching -------------------------------------------------------------------


def test_results_returned_out_of_order_are_matched_by_id() -> None:
    service = EloverblikService(
        [eloverblik_point(ELOVERBLIK_POINT_A), eloverblik_point(ELOVERBLIK_POINT_B)]
    )
    service.out_of_order = True

    with service.client() as client:
        frame = get_charges([ELOVERBLIK_POINT_A, ELOVERBLIK_POINT_B], client=client)

    assert list(dict.fromkeys(frame["metering_point_id"])) == [
        ELOVERBLIK_POINT_A,
        ELOVERBLIK_POINT_B,
    ]


def test_a_requested_id_without_a_result_raises_naming_it() -> None:
    service = EloverblikService(
        [eloverblik_point(ELOVERBLIK_POINT_A), eloverblik_point(ELOVERBLIK_POINT_B)]
    )
    service.overrides["getcharges"] = lambda _r: httpx.Response(
        200,
        json={
            "result": [
                {
                    "success": True,
                    "id": ELOVERBLIK_POINT_A,
                    "result": {"subscriptions": [], "tariffs": [], "fees": []},
                }
            ]
        },
    )

    with service.client() as client, pytest.raises(EloverblikError) as caught:
        get_charges(client=client)

    assert caught.value.metering_point_id == ELOVERBLIK_POINT_B
    assert ELOVERBLIK_POINT_B in str(caught.value)


def test_a_result_for_an_id_that_was_not_requested_raises_naming_it() -> None:
    service = EloverblikService()
    service.overrides["getcharges"] = lambda _r: httpx.Response(
        200,
        json={
            "result": [
                {"success": True, "id": ELOVERBLIK_POINT_A, "result": {}},
                {"success": True, "id": eloverblik_point_id(99), "result": {}},
            ]
        },
    )

    with service.client() as client, pytest.raises(EloverblikError) as caught:
        get_charges(client=client)

    assert caught.value.metering_point_id == eloverblik_point_id(99)


@pytest.mark.parametrize("bad_id", [571313100000000001, None, ["x"]])
def test_a_result_whose_id_is_not_text_raises(bad_id: object) -> None:
    service = EloverblikService()
    service.overrides["getcharges"] = lambda _r: httpx.Response(
        200, json={"result": [{"success": True, "id": bad_id, "result": {}}]}
    )

    with (
        service.client() as client,
        pytest.raises(EloverblikError, match="not requested"),
    ):
        get_charges(client=client)


@pytest.mark.parametrize(
    "body", [{"result": ["text"]}, {"result": {}}, {}, {"result": None}]
)
def test_a_malformed_response_body_raises(body: dict[str, Any]) -> None:
    service = EloverblikService()
    service.overrides["getcharges"] = lambda _r: httpx.Response(200, json=body)

    with service.client() as client, pytest.raises(EloverblikError):
        get_charges(client=client)


def test_the_lowest_batch_failure_is_raised_not_a_group() -> None:
    service = _many(23)
    state = {"n": 0}

    def answer(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        first = json.loads(request.content)["meteringPoints"]["meteringPoint"][0]
        if first == eloverblik_point_id(1):
            return httpx.Response(400, json={"errorCode": 20013, "errorText": "first"})
        return httpx.Response(400, json={"errorCode": 30000, "errorText": "later"})

    service.overrides["getcharges"] = answer

    with service.client() as client, pytest.raises(EloverblikError) as caught:
        get_charges(client=client)

    assert not isinstance(caught.value, ExceptionGroup)
    assert caught.value.error_text == "first"


def test_per_point_errors_are_raised_in_request_order() -> None:
    service = _many(23)
    service.charge_codes = {
        eloverblik_point_id(15): 20008,
        eloverblik_point_id(3): 20012,
    }

    with service.client() as client, pytest.raises(EloverblikError) as caught:
        get_charges(client=client)

    assert caught.value.metering_point_id == eloverblik_point_id(3)
    assert caught.value.error_code == 20012


# --- _unpack and the payload helpers ---------------------------------------------------------


def test_unpack_success_is_true() -> None:
    assert _unpack({"success": True, "id": "x"}, CHARGES) is True


def test_unpack_tolerated_code_is_false() -> None:
    result: dict[str, Any] = {"success": False, "errorCode": 30015, "id": "x"}

    assert _unpack(result, CHARGES, {30015}) is False


def test_unpack_untolerated_code_raises_with_everything() -> None:
    result: dict[str, Any] = {
        "success": False,
        "errorCode": 20008,
        "errorText": "gone",
        "id": ELOVERBLIK_POINT_A,
    }

    with pytest.raises(EloverblikError) as caught:
        _unpack(result, CHARGES, {30015})

    error = caught.value
    assert (error.status, error.error_code, error.error_text) == (200, 20008, "gone")
    assert error.metering_point_id == ELOVERBLIK_POINT_A
    assert "20008" in str(error)
    assert "gone" in str(error)


@pytest.mark.parametrize("code", ["30015", 30015.0, True, None])
def test_unpack_a_code_that_is_not_an_integer_is_not_tolerated(code: object) -> None:
    result: dict[str, Any] = {"success": False, "errorCode": code, "id": "x"}

    with pytest.raises(EloverblikError) as caught:
        _unpack(result, CHARGES, {30015, 1})

    assert caught.value.error_code is None


def test_number_accepts_ints_and_floats_and_nullable_none() -> None:
    assert _number(3, "k", "l") == 3.0
    assert _number(-2.5, "k", "l") == -2.5
    assert str(_number(None, "k", "l", nullable=True)) == "nan"


@pytest.mark.parametrize(
    "value", [True, "1", None, [1], float("nan"), float("inf"), 10**400]
)
def test_number_refuses_everything_else_naming_the_field(value: object) -> None:
    with pytest.raises(EloverblikError, match="price"):
        _number(value, "price", "label")


# --- the client scope ---------------------------------------------------------------------


class _Spy(EloverblikClient):
    instances: list["_Spy"] = []  # noqa: RUF012 - class-level registry for the test

    def __init__(self) -> None:
        super().__init__(
            ELOVERBLIK_REFRESH_TOKEN,
            transport=httpx.MockTransport(self.service),
        )
        _Spy.instances.append(self)

    service: EloverblikService = EloverblikService()


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> type[_Spy]:
    _Spy.instances = []
    _Spy.service = EloverblikService()
    monkeypatch.setattr(common, "EloverblikClient", _Spy)
    return _Spy


def test_a_created_client_is_closed_after_success(spy: type[_Spy]) -> None:
    get_charges()

    assert len(spy.instances) == 1
    assert spy.instances[0].closed


def test_a_created_client_is_closed_after_an_error(spy: type[_Spy]) -> None:
    spy.service.charge_codes = {ELOVERBLIK_POINT_A: 20012}

    with pytest.raises(EloverblikError):
        get_charges()

    assert spy.instances[0].closed


def test_a_created_client_is_closed_after_a_value_error(spy: type[_Spy]) -> None:
    with pytest.raises(ValueError):
        get_charges("bad")

    assert spy.instances[0].closed


def test_a_passed_client_is_left_open() -> None:
    service = EloverblikService()
    client = service.client()

    get_charges(client=client)
    get_charges(client=client)

    assert not client.closed
    assert service.token_calls == 1
    client.close()


def test_a_closed_passed_client_raises_runtime_error() -> None:
    service = EloverblikService()
    client = service.client()
    client.close()

    with pytest.raises(RuntimeError):
        get_charges(ELOVERBLIK_POINT_A, client=client)


def test_a_missing_token_without_a_passed_client_fails_at_the_first_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ELOVERBLIK_REFRESH_TOKEN", raising=False)

    with pytest.raises(EloverblikError, match="ELOVERBLIK_REFRESH_TOKEN"):
        get_charges(ELOVERBLIK_POINT_A)


def test_meter_data_without_a_token_makes_no_request_when_nothing_is_wanted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A period wholly after tomorrow needs no request, so no token is needed."""
    monkeypatch.delenv("ELOVERBLIK_REFRESH_TOKEN", raising=False)
    monkeypatch.setattr(
        pd.Timestamp,
        "now",
        classmethod(lambda _c, tz=None: pd.Timestamp("2026-11-02 12:00", tz=tz)),
    )
    client = EloverblikService().client(None)

    with client:
        frame = get_meter_data(
            "2026-11-05",
            "2026-11-06",
            aggregation="hour",
            metering_point_ids=ELOVERBLIK_POINT_A,
            client=client,
        )

    assert len(frame) == 24
    assert frame.isna().all().all()


def _correlation_of_call(service: EloverblikService, suffix: str, point: str) -> str:
    """The correlation id the mock gave the request for `suffix` naming `point`."""
    import uuid

    for number, request in enumerate(service.requests, start=1):
        if request.url.path.endswith(suffix) and point.encode() in request.content:
            return str(uuid.UUID(int=number, version=4))
    raise AssertionError(f"no request for {point}")


def test_a_per_point_meter_data_error_carries_the_responses_correlation_id() -> None:
    service = EloverblikService()
    service.codes = {ELOVERBLIK_POINT_B: {None: 30011}}

    with service.client() as client, pytest.raises(EloverblikError) as caught:
        get_meter_data(
            "2026-10-01",
            "2026-10-02",
            aggregation="day",
            metering_point_ids=[ELOVERBLIK_POINT_A, ELOVERBLIK_POINT_B],
            client=client,
        )

    expected = _correlation_of_call(service, "/Day", ELOVERBLIK_POINT_B)
    assert caught.value.correlation_id == expected


def test_a_per_point_details_error_carries_the_responses_correlation_id() -> None:
    service = EloverblikService()
    service.detail_codes = {ELOVERBLIK_POINT_B: 20012}

    with service.client() as client, pytest.raises(EloverblikError) as caught:
        get_metering_point_details(
            [ELOVERBLIK_POINT_A, ELOVERBLIK_POINT_B], client=client
        )

    expected = _correlation_of_call(service, "getdetails", ELOVERBLIK_POINT_B)
    assert caught.value.error_code == 20012
    assert caught.value.correlation_id == expected


def test_a_per_point_charges_error_carries_the_responses_correlation_id() -> None:
    service = EloverblikService()
    service.charge_codes = {ELOVERBLIK_POINT_B: 20012}

    with service.client() as client, pytest.raises(EloverblikError) as caught:
        get_charges([ELOVERBLIK_POINT_A, ELOVERBLIK_POINT_B], client=client)

    expected = _correlation_of_call(service, "getcharges", ELOVERBLIK_POINT_B)
    assert caught.value.error_code == 20012
    assert caught.value.correlation_id == expected


def test_a_payload_error_while_parsing_a_point_carries_the_correlation_id() -> None:
    service = EloverblikService()
    service.details[ELOVERBLIK_POINT_A] = eloverblik_details(
        ELOVERBLIK_POINT_A, typeOfMP=5
    )

    with service.client() as client, pytest.raises(EloverblikError) as caught:
        get_metering_point_details(ELOVERBLIK_POINT_A, client=client)

    assert caught.value.error_code is None
    assert caught.value.correlation_id == _correlation_of_call(
        service, "getdetails", ELOVERBLIK_POINT_A
    )


def test_the_correlation_id_is_that_of_the_batch_that_failed() -> None:
    ids = [eloverblik_point_id(n) for n in range(1, 13)]
    service = EloverblikService([eloverblik_point(i) for i in ids])
    service.charge_codes = {ids[11]: 20012}

    with service.client() as client, pytest.raises(EloverblikError) as caught:
        get_charges(ids, client=client)

    assert caught.value.metering_point_id == ids[11]
    assert len(service.calls("getcharges")) == 2
    expected = _correlation_of_call(service, "getcharges", ids[11])
    assert caught.value.correlation_id == expected
    first = _correlation_of_call(service, "getcharges", ids[0])
    assert expected != first
