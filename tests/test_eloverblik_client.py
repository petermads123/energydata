"""The Eloverblik client: tokens, error mapping, retries, and `ApiClient` changes."""

import asyncio
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from conftest import ELOVERBLIK_REFRESH_TOKEN, EloverblikService

from energydata.eloverblik import (
    BASE_URL,
    DEFAULT_POLICY,
    REFRESH_TOKEN_ENV,
    EloverblikClient,
    EloverblikError,
    MissingTokenError,
)
from energydata.eloverblik.client import _api_error
from energydata.utils import RetriesExhaustedError, RetryPolicy

LISTING = "/meteringpoints/meteringpoints"
INSTANT = RetryPolicy(max_attempts=2, base_delay=0.0, max_delay=0.0)


@pytest.fixture(autouse=True)
def _no_env_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(REFRESH_TOKEN_ENV, raising=False)


def _respond(
    status: int, body: object = None, *, content: bytes | None = None, **headers: str
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(_request: httpx.Request) -> httpx.Response:
        if content is not None:
            return httpx.Response(status, content=content, headers=headers)
        return httpx.Response(status, json=body, headers=headers)

    return handler


# --- constants and construction ------------------------------------------------


def test_constants_have_the_documented_values() -> None:
    assert BASE_URL == "https://api.eloverblik.dk/customerapi/api"
    assert REFRESH_TOKEN_ENV == "ELOVERBLIK_REFRESH_TOKEN"
    assert (
        RetryPolicy(max_attempts=4, base_delay=15.0, max_delay=60.0) == DEFAULT_POLICY
    )


def test_error_str_is_the_message_only_and_keeps_every_attribute() -> None:
    error = EloverblikError(
        "m",
        status=1,
        error_code=2,
        error_text="t",
        metering_point_id="p",
        correlation_id="c",
    )

    assert str(error) == "m"
    assert (error.status, error.error_code, error.error_text) == (1, 2, "t")
    assert (error.metering_point_id, error.correlation_id) == ("p", "c")


def test_error_defaults_are_none() -> None:
    error = EloverblikError("m")

    assert (error.status, error.error_code, error.error_text) == (None,) * 3
    assert (error.metering_point_id, error.correlation_id) == (None, None)


def test_missing_token_error_is_an_eloverblik_error() -> None:
    assert issubclass(MissingTokenError, EloverblikError)


def test_construction_never_fails_without_a_token() -> None:
    with EloverblikClient() as client:
        assert not client.has_refresh_token
        assert repr(client) == "EloverblikClient(token=missing)"


def test_client_base_url_is_the_customer_api() -> None:
    with EloverblikClient("abc") as client:
        assert client.base_url == BASE_URL


@pytest.mark.parametrize("bad", [0, -1])
def test_client_rejects_a_concurrency_below_one(bad: int) -> None:
    with pytest.raises(ValueError, match="max_concurrency"):
        EloverblikClient("abc", max_concurrency=bad)


@pytest.mark.parametrize(
    ("token", "usable", "shown"),
    [
        ("abc", True, "set"),
        ("  abc\n", True, "set"),
        ("", False, "missing"),
        ("   ", False, "missing"),
        ("\t\n", False, "missing"),
        ("ab c", False, "missing"),
        ("tök", False, "missing"),
        ("æøå", False, "missing"),
        ("﻿abc", False, "missing"),
        ("a\tb", False, "missing"),
        ("x\x00y", False, "missing"),
    ],
)
def test_token_classification(token: str, usable: bool, shown: str) -> None:
    with EloverblikClient(token) as client:
        assert client.has_refresh_token is usable
        assert repr(client) == f"EloverblikClient(token={shown})"


def test_the_argument_beats_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    service = EloverblikService()
    monkeypatch.setenv(REFRESH_TOKEN_ENV, "from-the-environment")
    client = service.client("test-refresh-token")

    with client:
        client.get_json("GET", LISTING)

    assert service.token_calls == 1


def test_the_environment_is_used_when_no_token_is_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = EloverblikService(refresh_token="env-token-value")
    monkeypatch.setenv(REFRESH_TOKEN_ENV, "  env-token-value\n")

    with service.client(None) as client:
        assert client.has_refresh_token
        client.get_json("GET", LISTING)

    assert service.token_calls == 1


@pytest.mark.parametrize("blank", ["", "   "])
def test_an_explicit_blank_token_does_not_fall_back_to_the_environment(
    blank: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(REFRESH_TOKEN_ENV, "good")

    with EloverblikClient(blank) as client:
        assert not client.has_refresh_token
        assert repr(client) == "EloverblikClient(token=missing)"
        with pytest.raises(MissingTokenError):
            client.get_json("GET", LISTING)


# --- missing and unusable tokens -------------------------------------------------


@pytest.mark.parametrize("token", [None, "", "\t\n"])
def test_a_missing_token_fails_at_the_first_request_naming_the_variable(
    token: str | None,
) -> None:
    service = EloverblikService()
    client = service.client(token)

    with client, pytest.raises(MissingTokenError, match=REFRESH_TOKEN_ENV):
        client.get_json("GET", LISTING)

    assert service.requests == []


@pytest.mark.parametrize("token", ["tök", "a b", "x\x00y", "﻿abc"])
def test_an_unusable_token_is_refused_unechoed_before_any_request(token: str) -> None:
    service = EloverblikService()
    client = service.client(token)

    with client, pytest.raises(EloverblikError) as caught:
        client.get_json("GET", LISTING)

    assert not isinstance(caught.value, MissingTokenError)
    assert token.strip() not in str(caught.value)
    assert token.strip() not in repr(caught.value)
    assert service.requests == []


# --- the token flow --------------------------------------------------------------


def test_the_refresh_token_is_traded_once_and_the_access_token_reused() -> None:
    service = EloverblikService()

    with service.client() as client:
        client.get_json("GET", LISTING)
        client.get_json("GET", LISTING)
        client.get_json("GET", LISTING)

    assert service.token_calls == 1
    token_request = service.calls("/token")[0]
    assert (
        token_request.headers["Authorization"] == f"Bearer {ELOVERBLIK_REFRESH_TOKEN}"
    )
    listing = service.calls("/meteringpoints")[0]
    assert listing.headers["Authorization"] == "Bearer access-1"


def test_a_padded_token_is_sent_stripped() -> None:
    service = EloverblikService()

    with service.client(f"  {ELOVERBLIK_REFRESH_TOKEN}\n") as client:
        client.get_json("GET", LISTING)

    assert service.token_calls == 1


def test_concurrent_first_calls_share_one_token_call() -> None:
    service = EloverblikService()
    client = service.client(max_concurrency=3)

    async def three() -> list[dict[str, Any]]:
        return list(
            await asyncio.gather(*(client.fetch_json("GET", LISTING) for _ in range(3)))
        )

    with client:
        results = client.run(three)

    assert len(results) == 3
    assert service.token_calls == 1


def test_a_401_refreshes_once_and_retries_once() -> None:
    service = EloverblikService()

    with service.client() as client:
        client.get_json("GET", LISTING)
        service.expire_access()
        payload = client.get_json("GET", LISTING)

    assert isinstance(payload["result"], list)
    assert service.token_calls == 2
    assert len(service.calls("/meteringpoints")) == 3


def test_concurrent_401s_cost_one_token_call() -> None:
    service = EloverblikService()
    client = service.client(max_concurrency=2)

    async def two() -> None:
        await asyncio.gather(
            client.fetch_json("GET", LISTING), client.fetch_json("GET", LISTING)
        )

    with client:
        client.get_json("GET", LISTING)
        service.expire_access()
        client.run(two)

    assert service.token_calls == 2


def test_a_second_401_raises_after_exactly_one_refresh() -> None:
    service = EloverblikService()
    service.overrides["meteringpoints"] = _respond(401, {"title": "Unauthorized"})

    with service.client() as client, pytest.raises(EloverblikError) as caught:
        client.get_json("GET", LISTING)

    assert caught.value.status == 401
    assert service.token_calls == 2  # the first fetch and exactly one refresh
    assert len(service.calls("/meteringpoints")) == 2


def test_a_refresh_that_returns_the_same_token_still_raises_after_one_retry() -> None:
    service = EloverblikService()
    service.overrides["token"] = _respond(200, {"result": "stuck"})

    with service.client() as client, pytest.raises(EloverblikError) as caught:
        client.get_json("GET", LISTING)

    assert caught.value.status == 401
    assert len(service.calls("/meteringpoints")) == 2


def test_a_rejected_refresh_token_says_so_and_is_not_retried() -> None:
    service = EloverblikService()

    with service.client("wrong-token") as client, pytest.raises(EloverblikError) as e:
        client.get_json("GET", LISTING)

    assert e.value.status == 401
    assert "refresh token was rejected" in str(e.value)
    assert service.token_calls == 0
    assert len(service.calls("/token")) == 1


def test_a_403_from_the_token_endpoint_says_the_refresh_token_was_rejected() -> None:
    service = EloverblikService()
    service.overrides["token"] = _respond(403, {"title": "Forbidden"})

    with service.client() as client, pytest.raises(EloverblikError) as caught:
        client.get_json("GET", LISTING)

    assert caught.value.status == 403
    assert "refresh token was rejected" in str(caught.value)
    assert len(service.calls("/token")) == 1


def test_a_403_on_a_data_endpoint_is_not_refreshed_or_retried() -> None:
    service = EloverblikService()
    service.overrides["meteringpoints"] = _respond(403, {"title": "Forbidden"})

    with service.client() as client, pytest.raises(EloverblikError) as caught:
        client.get_json("GET", LISTING)

    assert caught.value.status == 403
    assert service.token_calls == 1
    assert len(service.calls("/meteringpoints")) == 1


@pytest.mark.parametrize(
    "body", [{}, {"result": ""}, {"result": "   "}, {"result": None}, {"result": 5}]
)
def test_a_token_response_without_a_usable_result_is_an_error(body: object) -> None:
    service = EloverblikService()
    service.overrides["token"] = _respond(200, body)

    with service.client() as client, pytest.raises(EloverblikError) as caught:
        client.get_json("GET", LISTING)

    assert caught.value.status == 200
    assert "/token" in str(caught.value)


def test_a_token_response_that_is_not_an_object_is_an_error() -> None:
    service = EloverblikService()
    service.overrides["token"] = _respond(200, [])

    with service.client() as client, pytest.raises(EloverblikError) as caught:
        client.get_json("GET", LISTING)

    assert caught.value.status == 200


def test_an_access_token_is_stripped_before_it_is_sent() -> None:
    service = EloverblikService()
    service.overrides["token"] = _respond(200, {"result": "  jwt-value  "})
    service.overrides["meteringpoints"] = _respond(200, {"result": []})

    with service.client() as client:
        client.get_json("GET", LISTING)

    seen = service.calls("/meteringpoints")[0].headers["Authorization"]
    assert seen == "Bearer jwt-value"


# --- no token in any message ------------------------------------------------------


def test_a_server_text_echoing_the_refresh_token_is_redacted() -> None:
    token = "refresh-token-secret-value"
    service = EloverblikService(refresh_token="something-else")
    service.overrides["token"] = _respond(401, {"detail": f"bad token {token}"})

    with service.client(token) as client, pytest.raises(EloverblikError) as caught:
        client.get_json("GET", LISTING)

    assert token not in str(caught.value)
    assert token not in repr(caught.value)
    assert caught.value.error_text is not None
    assert token not in caught.value.error_text
    assert "<token>" in caught.value.error_text


def test_a_server_text_echoing_the_access_token_is_redacted() -> None:
    service = EloverblikService()
    service.overrides["meteringpoints"] = _respond(
        400, {"errorCode": 20013, "errorText": "bad request, token access-1 refused"}
    )

    with service.client() as client, pytest.raises(EloverblikError) as caught:
        client.get_json("GET", LISTING)

    assert "access-1" not in str(caught.value)
    assert caught.value.error_text == "bad request, token <token> refused"
    assert caught.value.error_code == 20013


def test_neither_token_appears_in_the_client_repr_after_use() -> None:
    service = EloverblikService()

    with service.client() as client:
        client.get_json("GET", LISTING)
        shown = repr(client)

    assert ELOVERBLIK_REFRESH_TOKEN not in shown
    assert "access-1" not in shown


@pytest.mark.parametrize(
    "body",
    [[], "text", None],
)
def test_a_2xx_body_that_is_not_an_object_has_status_200(body: object) -> None:
    service = EloverblikService()
    service.overrides["meteringpoints"] = _respond(200, body)

    with service.client() as client, pytest.raises(EloverblikError) as caught:
        client.get_json("GET", LISTING)

    assert caught.value.status == 200
    assert "access-1" not in str(caught.value)
    assert ELOVERBLIK_REFRESH_TOKEN not in str(caught.value)


@pytest.mark.parametrize("content", [b"{", b"not json", b""])
def test_a_2xx_body_that_is_not_json_has_status_200(content: bytes) -> None:
    service = EloverblikService()
    service.overrides["meteringpoints"] = _respond(
        200, content=content, **{"Content-Type": "application/json"}
    )

    with service.client() as client, pytest.raises(EloverblikError) as caught:
        client.get_json("GET", LISTING)

    assert caught.value.status == 200


# --- error mapping -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "code", "text"),
    [
        ({"errorCode": 30017, "errorText": "x"}, 30017, "x"),
        ({"errorCode": "30017"}, None, None),
        ({"errorCode": True}, None, None),
        ({"errorCode": 30014, "errorText": "t", "detail": "d"}, 30014, "t"),
        ({"detail": "d", "title": "t"}, None, "d"),
        ({"errorText": "", "title": "t"}, None, "t"),
        ({"title": "only a title"}, None, "only a title"),
        ({"errorText": 5, "title": "t"}, None, "t"),
        ({}, None, None),
    ],
)
def test_problem_details_bodies_are_mapped(
    body: dict[str, Any], code: int | None, text: str | None
) -> None:
    service = EloverblikService()
    service.overrides["meteringpoints"] = _respond(400, body)

    with service.client() as client, pytest.raises(EloverblikError) as caught:
        client.get_json("GET", LISTING)

    error = caught.value
    assert error.status == 400
    assert (error.error_code, error.error_text) == (code, text)
    assert error.correlation_id is not None
    assert "400" in str(error)
    assert LISTING in str(error)
    if code is not None:
        assert str(code) in str(error)
    if text:
        assert text in str(error)


@pytest.mark.parametrize("content", [b"<html>boom</html>", b"", b"[1, 2]"])
def test_a_non_object_error_body_gives_no_code_or_text(content: bytes) -> None:
    service = EloverblikService()
    service.overrides["meteringpoints"] = _respond(500, content=content)
    service_client = service.client(policy=INSTANT)

    with service_client, pytest.raises(EloverblikError) as caught:
        service_client.get_json("GET", LISTING)

    assert caught.value.status == 500
    assert caught.value.error_code is None
    assert caught.value.error_text is None


def _correlation(number: int) -> str:
    """The `X-Correlation-ID` the mock gives its `number`th request."""
    return str(uuid.UUID(int=number, version=4))


def test_the_correlation_id_is_carried_from_the_header() -> None:
    service = EloverblikService()
    service.overrides["meteringpoints"] = _respond(400, {"errorCode": 20013})

    with service.client() as client, pytest.raises(EloverblikError) as caught:
        client.get_json("GET", LISTING)

    assert caught.value.correlation_id == _correlation(len(service.requests))
    assert caught.value.correlation_id in str(caught.value)


def test_an_error_response_without_a_correlation_header_has_none() -> None:
    response = httpx.Response(400, json={"errorCode": 20013})

    error = _api_error(LISTING, response)

    assert error.correlation_id is None
    assert "correlation" not in str(error)


@pytest.mark.parametrize("status", [429, 503])
def test_429_and_503_are_retried_then_mapped_with_their_headers(status: int) -> None:
    service = EloverblikService()
    service.overrides["meteringpoints"] = _respond(status, {"title": "slow down"})

    with service.client(policy=INSTANT) as client, pytest.raises(EloverblikError) as e:
        client.get_json("GET", LISTING)

    assert e.value.status == status
    assert e.value.correlation_id == _correlation(len(service.requests))
    assert e.value.error_text == "slow down"
    assert len(service.calls("/meteringpoints")) == 2


def test_a_503_that_clears_is_retried_to_success() -> None:
    service = EloverblikService()
    state = {"calls": 0}

    def flaky(_request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        if state["calls"] == 1:
            return httpx.Response(503, json={"title": "busy"})
        return httpx.Response(200, json={"result": []})

    service.overrides["meteringpoints"] = flaky

    with service.client(policy=INSTANT) as client:
        payload = client.get_json("GET", LISTING)

    assert payload == {"result": []}
    assert state["calls"] == 2


def test_a_transport_failure_stays_retries_exhausted() -> None:
    service = EloverblikService()

    def boom(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    service.overrides["meteringpoints"] = boom

    with (
        service.client(policy=INSTANT) as client,
        pytest.raises(RetriesExhaustedError) as caught,
    ):
        client.get_json("GET", LISTING)

    assert not isinstance(caught.value, EloverblikError)
    assert caught.value.response is None


def test_a_post_sends_the_body_and_a_get_with_params_sends_the_query() -> None:
    service = EloverblikService()

    with service.client() as client:
        client.get_json("GET", LISTING, params={"includeAll": "true"})
        client.get_json(
            "POST",
            "/meteringpoints/meteringpoint/getdetails",
            body={"meteringPoints": {"meteringPoint": ["571313100000000001"]}},
        )

    assert service.calls("/meteringpoints")[0].url.params["includeAll"] == "true"
    details = service.calls("/getdetails")[0]
    assert details.method == "POST"
    assert details.headers["content-type"] == "application/json"
    assert b"571313100000000001" in details.content


def test_the_accept_header_is_json() -> None:
    service = EloverblikService()

    with service.client() as client:
        client.get_json("GET", LISTING)

    assert service.requests[0].headers["Accept"] == "application/json"


def test_get_json_on_a_closed_client_raises_runtime_error() -> None:
    service = EloverblikService()
    client = service.client()
    client.close()

    with pytest.raises(RuntimeError):
        client.get_json("GET", LISTING)
