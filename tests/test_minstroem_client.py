import asyncio
import traceback
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from conftest import ADDRESS_ID, MinStroemService, minstroem_address

from energydata.minstroem import (
    API_KEY_ENV,
    BASE_URL,
    MinStroemClient,
    MinStroemError,
    MissingApiKeyError,
    client,
    get_consumption,
    get_forecast_prices,
)
from energydata.utils import (
    HoldoffTooLongError,
    ParseError,
    RetriesExhaustedError,
    RetryPolicy,
)

OTHER = "2b4bf7a8-b5d0-45ab-8d42-3a5688bba167"
# Distinctive on purpose: a short key is a substring of almost any message.
SECRET = "SECRET-key-9f3a"
ERROR_BODY = {"error": True, "reason": "r", "errorCode": "forbidden"}

type Responder = Callable[[httpx.Request], httpx.Response]


def _chain(error: BaseException) -> list[BaseException]:
    """Every exception reachable from `error`, however the chain loops."""
    seen: dict[int, BaseException] = {}
    todo = [error]
    while todo:
        current = todo.pop()
        if id(current) in seen:
            continue
        seen[id(current)] = current
        todo.extend(e for e in (current.__cause__, current.__context__) if e)
        if isinstance(current, BaseExceptionGroup):
            todo.extend(current.exceptions)
    return list(seen.values())


def _texts(error: BaseException) -> list[str]:
    """Everything a person could read off `error`: str, args and the traceback."""
    texts = [f"{e}{e.args!r}" for e in _chain(error)]
    texts.append("".join(traceback.format_exception(error)))
    return texts


def _reply(status: int, body: object = ERROR_BODY, **headers: str) -> Responder:
    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body, headers=headers)

    return respond


def _raw(status: int, content: bytes) -> Responder:
    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=content)

    return respond


# --- MinStroemError --------------------------------------------------------


def test_error_str_is_the_message_only() -> None:
    error = MinStroemError(
        "boom",
        status=404,
        error_code="resource_not_found",
        reason="r",
        request_id="req-1",
    )

    assert str(error) == "boom"
    assert error.args == ("boom",)
    assert (error.status, error.error_code, error.reason, error.request_id) == (
        404,
        "resource_not_found",
        "r",
        "req-1",
    )


def test_error_attributes_default_to_none() -> None:
    error = MinStroemError("m")

    assert (error.status, error.error_code, error.reason, error.request_id) == (
        None,
        None,
        None,
        None,
    )


def test_error_attributes_are_keyword_only() -> None:
    with pytest.raises(TypeError):
        MinStroemError("m", 404)  # type: ignore[misc]  # proves status is keyword-only


def test_missing_key_error_is_a_minstroem_error() -> None:
    error = MissingApiKeyError("no key")

    assert isinstance(error, MinStroemError)
    assert (error.status, error.error_code, error.reason, error.request_id) == (
        None,
        None,
        None,
        None,
    )


# --- constructing and the key ---------------------------------------------


def test_base_url_is_the_consumer_api() -> None:
    assert BASE_URL == "https://api.minstroem.app/consumer/v1"
    assert API_KEY_ENV == "MINSTROEM_API_KEY"
    with MinStroemClient("k") as c:
        assert c.base_url == BASE_URL


@pytest.mark.parametrize(
    ("api_key", "env", "expected"),
    [
        ("test-key", None, True),
        ("  test-key\n", None, True),
        ("!", None, True),
        ("~", None, True),
        ("\x20x", None, True),
        ("", None, False),
        ("   ", None, False),
        ("a b", None, False),
        ("\x7f", None, False),
        ("nøgle", None, False),
        (None, "test-key", True),
        (None, "  test-key\r\n", True),
        (None, "   ", False),
        (None, None, False),
        ("", "test-key", False),
        ("explicit", "from-env", True),
    ],
)
def test_has_api_key_and_repr_follow_the_usable_key(
    monkeypatch: pytest.MonkeyPatch,
    api_key: str | None,
    env: str | None,
    expected: bool,
) -> None:
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    if env is not None:
        monkeypatch.setenv(API_KEY_ENV, env)

    with MinStroemClient(api_key) as c:
        assert c.has_api_key is expected
        assert repr(c) == f"MinStroemClient(key={'set' if expected else 'missing'})"


def test_explicit_key_wins_over_the_environment(
    monkeypatch: pytest.MonkeyPatch, minstroem_service: MinStroemService
) -> None:
    monkeypatch.setenv(API_KEY_ENV, "wrong-from-env")
    with minstroem_service.client("test-key") as c:
        c.get_json("/me/addresses")

    assert minstroem_service.requests[0].headers["Authorization"] == "Bearer test-key"


def test_key_is_read_from_the_environment_once_at_construction_and_stripped(
    monkeypatch: pytest.MonkeyPatch, minstroem_service: MinStroemService
) -> None:
    monkeypatch.setenv(API_KEY_ENV, " test-key\r\n")
    with minstroem_service.client(None) as c:
        monkeypatch.delenv(API_KEY_ENV)
        payload = c.get_json("/me/addresses")

    assert "data" in payload
    assert minstroem_service.requests[0].headers["Authorization"] == "Bearer test-key"


def test_key_with_a_trailing_newline_is_stripped_and_works(
    minstroem_service: MinStroemService,
) -> None:
    with minstroem_service.client("test-key\n") as c:
        c.get_json("/me/addresses")

    assert minstroem_service.requests[0].headers["Authorization"] == "Bearer test-key"


def test_request_is_a_get_with_json_accept_under_the_base_url(
    minstroem_service: MinStroemService,
) -> None:
    with minstroem_service.client() as c:
        c.get_json("/me/addresses")

    request = minstroem_service.requests[0]
    assert request.method == "GET"
    assert str(request.url) == f"{BASE_URL}/me/addresses"
    assert request.headers["Accept"] == "application/json"


def test_params_become_the_query_string(minstroem_service: MinStroemService) -> None:
    params = {"from": "2026-01-01T00:00:00Z", "aggregation": "hour"}

    with minstroem_service.client() as c, pytest.raises(MinStroemError):
        c.get_json(f"/me/addresses/{OTHER}/consumption", params)

    assert minstroem_service.calls("/consumption") == [params]


@pytest.mark.parametrize("key", [None, "", "   "])
def test_missing_key_constructs_and_fails_only_at_the_first_request(
    monkeypatch: pytest.MonkeyPatch,
    minstroem_service: MinStroemService,
    key: str | None,
) -> None:
    monkeypatch.delenv(API_KEY_ENV, raising=False)

    with minstroem_service.client(key) as c:
        assert not c.has_api_key
        with pytest.raises(MissingApiKeyError, match=API_KEY_ENV) as caught:
            c.get_json("/me/addresses")

    assert minstroem_service.requests == []
    error = caught.value
    assert (error.status, error.error_code, error.reason, error.request_id) == (
        None,
        None,
        None,
        None,
    )


def test_blank_explicit_key_does_not_fall_back_to_the_environment(
    monkeypatch: pytest.MonkeyPatch, minstroem_service: MinStroemService
) -> None:
    # Only `None` reads the environment; a blank key is a deliberate "no key".
    monkeypatch.setenv(API_KEY_ENV, "test-key")

    with minstroem_service.client("") as c:
        assert not c.has_api_key
        with pytest.raises(MissingApiKeyError):
            c.get_json("/me/addresses")


@pytest.mark.parametrize(
    "key",
    [
        "SECRET ø",
        "ab\tc",
        "nøgle-SECRET",
        "key\x00SECRET",
        "SECRET\x7f",
        "Bearer SECRET key",
        "SECRET key",
    ],
)
def test_unusable_key_is_refused_at_the_first_request_and_never_echoed(
    minstroem_service: MinStroemService, key: str
) -> None:
    with minstroem_service.client(key) as c:
        assert not c.has_api_key
        assert repr(c) == "MinStroemClient(key=missing)"
        with pytest.raises(MinStroemError, match="HTTP header cannot carry") as caught:
            c.get_json("/me/addresses")

    error = caught.value
    assert type(error) is MinStroemError
    assert (error.status, error.error_code, error.reason, error.request_id) == (
        None,
        None,
        None,
        None,
    )
    assert minstroem_service.requests == []
    for text in _texts(error):
        assert "SECRET" not in text
        assert key not in text
        assert key.strip() not in text


def test_unusable_key_is_refused_before_any_series_request(
    minstroem_service: MinStroemService,
) -> None:
    with minstroem_service.client("bad key") as c, pytest.raises(MinStroemError):
        get_consumption("2026-11-02", address_id=ADDRESS_ID, client=c)

    assert minstroem_service.requests == []


@pytest.mark.parametrize("max_concurrency", [0, -1])
def test_max_concurrency_below_one_is_a_value_error(max_concurrency: int) -> None:
    with pytest.raises(ValueError, match=str(max_concurrency)):
        MinStroemClient("k", max_concurrency=max_concurrency)


def test_the_key_is_not_in_the_repr_of_a_working_client() -> None:
    with MinStroemClient(SECRET) as c:
        assert SECRET not in repr(c)
        assert SECRET not in str(c)
        assert repr(c) == "MinStroemClient(key=set)"


def test_wrong_key_is_a_401_that_never_echoes_either_key(
    minstroem_service: MinStroemService, capsys: pytest.CaptureFixture[str]
) -> None:
    with minstroem_service.client(SECRET) as c:
        with pytest.raises(MinStroemError) as direct:
            c.get_json("/me/addresses")
        with pytest.raises(MinStroemError) as windowed:
            get_consumption("2026-11-02", address_id=ADDRESS_ID, client=c)

    for error in (direct.value, windowed.value):
        assert error.status == 401
        assert error.error_code == "invalid_token"
        for text in _texts(error):
            assert SECRET not in text
            assert "test-key" not in text
    output = capsys.readouterr()
    assert SECRET not in output.out + output.err


# --- fetch_json: error mapping --------------------------------------------


def test_error_response_maps_to_all_four_attributes_and_chains_the_http_error(
    minstroem_service: MinStroemService,
) -> None:
    path = f"/me/addresses/{OTHER}/consumption"

    with minstroem_service.client() as c, pytest.raises(MinStroemError) as caught:
        c.get_json(path)

    error = caught.value
    assert str(error) == (
        f"Min Strøm API: HTTP 404 resource_not_found on {path}: No such address. "
        "(request id req-1)"
    )
    assert error.status == 404
    assert error.error_code == "resource_not_found"
    assert error.reason == "No such address."
    assert error.request_id == "req-1"
    assert isinstance(error.__cause__, httpx.HTTPStatusError)
    assert len(minstroem_service.requests) == 1


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, "invalid_parameter"),
        (400, "invalid_range"),
        (400, "range_too_large"),
        (401, "invalid_token"),
        (401, "credential_expired"),
        (403, "plus_required"),
        (403, "forbidden"),
        (404, "resource_not_found"),
        (422, "feature_unavailable"),
    ],
)
def test_each_documented_client_error_maps_and_is_not_retried(
    minstroem_service: MinStroemService, status: int, code: str
) -> None:
    body = {"error": True, "reason": "because æøå", "errorCode": code}
    minstroem_service.overrides["addresses"] = _reply(status, body)

    with minstroem_service.client() as c, pytest.raises(MinStroemError) as caught:
        c.get_json("/me/addresses")

    error = caught.value
    assert (error.status, error.error_code) == (status, code)
    assert error.reason == "because æøå"
    assert error.request_id == "req-1"
    assert "because æøå" in str(error)
    assert len(minstroem_service.requests) == 1


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (500, "internal_error"),
        (502, "upstream_unavailable"),
        (503, "upstream_unavailable"),
        (503, "rate_limiter_unavailable"),
        (504, "upstream_unavailable"),
    ],
)
def test_exhausted_retries_carry_the_last_response(
    minstroem_service: MinStroemService, status: int, code: str
) -> None:
    body = {"error": True, "reason": "down", "errorCode": code}
    minstroem_service.overrides["addresses"] = _reply(status, body)

    with minstroem_service.client() as c, pytest.raises(MinStroemError) as caught:
        c.get_json("/me/addresses")

    error = caught.value
    assert (error.status, error.error_code, error.reason) == (status, code, "down")
    assert error.request_id == "req-5"
    assert len(minstroem_service.requests) == 5
    assert isinstance(error.__cause__, RetriesExhaustedError)


def test_a_retry_after_beyond_the_holdoff_cap_is_an_error_after_one_request(
    minstroem_service: MinStroemService,
) -> None:
    body = {"error": True, "reason": "slow down", "errorCode": "rate_limited"}
    minstroem_service.overrides["addresses"] = _reply(
        429, body, **{"Retry-After": "3600"}
    )

    with minstroem_service.client() as c, pytest.raises(MinStroemError) as caught:
        c.get_json("/me/addresses")

    error = caught.value
    assert (error.status, error.error_code) == (429, "rate_limited")
    assert error.request_id == "req-1"
    assert len(minstroem_service.requests) == 1
    assert isinstance(error.__cause__, HoldoffTooLongError)


def test_a_429_with_a_short_retry_after_is_retried_and_recovers(
    minstroem_service: MinStroemService,
) -> None:
    calls = 0

    def respond(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, json=ERROR_BODY, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"data": []})

    minstroem_service.overrides["addresses"] = respond

    with minstroem_service.client() as c:
        assert c.get_json("/me/addresses") == {"data": []}

    assert calls == 2


@pytest.mark.parametrize("entry", ["get_json", "get_forecast_prices"])
def test_a_transport_failure_stays_retries_exhausted_not_a_minstroem_error(
    minstroem_service: MinStroemService, entry: str
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    minstroem_service.overrides["addresses"] = refuse
    minstroem_service.overrides["forecast-prices"] = refuse

    with (
        minstroem_service.client() as c,
        pytest.raises(RetriesExhaustedError) as caught,
    ):
        if entry == "get_json":
            c.get_json("/me/addresses")
        else:
            get_forecast_prices(address_id=ADDRESS_ID, client=c)

    assert not isinstance(caught.value, MinStroemError)
    assert caught.value.response is None
    assert len(minstroem_service.requests) == 5


@pytest.mark.parametrize(
    "content",
    [
        b"<html>Bad</html>",
        b"",
        b'{"errorCode": "x", "reason": "y"}',
        b'{"error": "true", "errorCode": "x", "reason": "y"}',
        b'{"error": true, "errorCode": 7, "reason": null}',
        b'["error"]',
    ],
)
def test_an_undocumented_error_body_leaves_code_and_reason_none(
    minstroem_service: MinStroemService, content: bytes
) -> None:
    minstroem_service.overrides["addresses"] = _raw(400, content)

    with minstroem_service.client() as c, pytest.raises(MinStroemError) as caught:
        c.get_json("/me/addresses")

    error = caught.value
    assert error.status == 400
    assert error.error_code is None
    assert error.reason is None
    assert error.request_id == "req-1"
    assert str(error) == "Min Strøm API: HTTP 400 on /me/addresses (request id req-1)"


def test_a_non_json_body_after_exhausted_retries_keeps_status_and_request_id(
    minstroem_service: MinStroemService,
) -> None:
    minstroem_service.overrides["addresses"] = _raw(502, b"<html>Bad gateway</html>")

    with minstroem_service.client() as c, pytest.raises(MinStroemError) as caught:
        c.get_json("/me/addresses")

    error = caught.value
    assert (error.status, error.error_code, error.reason) == (502, None, None)
    assert error.request_id == "req-5"


def test_error_without_a_request_id_header_has_none_and_no_id_in_the_message() -> None:
    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json=ERROR_BODY)

    transport = httpx.MockTransport(respond)
    with (
        MinStroemClient("k", transport=transport) as c,
        pytest.raises(MinStroemError) as caught,
    ):
        c.get_json("/me/addresses")

    assert caught.value.request_id is None
    assert "request id" not in str(caught.value)
    assert caught.value.error_code == "forbidden"


@pytest.mark.parametrize(
    ("suffix", "capability"),
    [
        ("consumption", "consumption"),
        ("solar-export", "solarExport"),
        ("consumption-costs", "consumptionCosts"),
        ("solar-export-revenue", "solarExportRevenue"),
        ("prices", "prices"),
        ("forecast-prices", "forecastPrices"),
    ],
)
@pytest.mark.parametrize("trailing", ["", "/"])
def test_feature_unavailable_names_the_capability_of_the_path(
    minstroem_service: MinStroemService, suffix: str, capability: str, trailing: str
) -> None:
    body = {
        "error": True,
        "reason": "Not available.",
        "errorCode": "feature_unavailable",
    }
    minstroem_service.overrides[suffix] = _reply(422, body)

    with minstroem_service.client() as c, pytest.raises(MinStroemError) as caught:
        c.get_json(f"/me/addresses/{ADDRESS_ID}/{suffix}{trailing}")

    assert str(caught.value).endswith(
        f"; the address lacks the '{capability}' capability"
    )
    assert caught.value.error_code == "feature_unavailable"


def test_feature_unavailable_on_the_address_list_names_no_capability(
    minstroem_service: MinStroemService,
) -> None:
    body = {
        "error": True,
        "reason": "Not available.",
        "errorCode": "feature_unavailable",
    }
    minstroem_service.overrides["addresses"] = _reply(422, body)

    with minstroem_service.client() as c, pytest.raises(MinStroemError) as caught:
        c.get_json("/me/addresses")

    assert "capability" not in str(caught.value)


def test_a_capability_is_only_named_for_feature_unavailable(
    minstroem_service: MinStroemService,
) -> None:
    minstroem_service.overrides["prices"] = _reply(403, ERROR_BODY)

    with minstroem_service.client() as c, pytest.raises(MinStroemError) as caught:
        c.get_json(f"/me/addresses/{ADDRESS_ID}/prices")

    assert "capability" not in str(caught.value)


def test_the_error_message_never_holds_a_header(
    minstroem_service: MinStroemService,
) -> None:
    with minstroem_service.client() as c, pytest.raises(MinStroemError) as caught:
        c.get_json(f"/me/addresses/{OTHER}/consumption")

    assert "Bearer" not in str(caught.value)
    assert "test-key" not in str(caught.value)
    assert "Authorization" not in str(caught.value)


# --- fetch_json: 2xx bodies ------------------------------------------------


@pytest.mark.parametrize(
    "content", [b"not json", b"", b"<html>ok</html>", b'{"data": ']
)
def test_a_2xx_body_that_is_not_json_is_an_error_with_status_200(
    minstroem_service: MinStroemService, content: bytes
) -> None:
    minstroem_service.overrides["addresses"] = _raw(200, content)

    with minstroem_service.client() as c, pytest.raises(MinStroemError) as caught:
        c.get_json("/me/addresses")

    error = caught.value
    assert error.status == 200
    assert (error.error_code, error.reason, error.request_id) == (None, None, None)
    assert "not valid JSON" in str(error)
    assert isinstance(error.__cause__, ParseError)


@pytest.mark.parametrize("content", [b"[]", b"null", b'"text"', b"42", b"true"])
def test_a_2xx_body_that_is_not_an_object_is_an_error_with_status_200(
    minstroem_service: MinStroemService, content: bytes
) -> None:
    minstroem_service.overrides["addresses"] = _raw(200, content)

    with minstroem_service.client() as c, pytest.raises(MinStroemError) as caught:
        c.get_json("/me/addresses")

    error = caught.value
    assert error.status == 200
    assert error.error_code is None
    assert "not a JSON object" in str(error)


def test_a_2xx_json_object_is_returned_as_is(
    minstroem_service: MinStroemService,
) -> None:
    minstroem_service.overrides["addresses"] = _reply(200, {"a": 1, "data": [None]})

    with minstroem_service.client() as c:
        assert c.get_json("/me/addresses") == {"a": 1, "data": [None]}


# --- fetch_json: loop and lifecycle ----------------------------------------


def test_fetch_json_awaited_off_the_clients_loop_is_a_runtime_error(
    minstroem_service: MinStroemService,
) -> None:
    with minstroem_service.client() as c, pytest.raises(RuntimeError):
        asyncio.run(c.fetch_json("/me/addresses"))

    assert minstroem_service.requests == []


def test_a_missing_key_is_reported_before_the_loop_check(
    monkeypatch: pytest.MonkeyPatch, minstroem_service: MinStroemService
) -> None:
    monkeypatch.delenv(API_KEY_ENV, raising=False)

    with minstroem_service.client(None) as c, pytest.raises(MissingApiKeyError):
        asyncio.run(c.fetch_json("/me/addresses"))


def test_get_json_on_a_closed_client_is_a_runtime_error(
    minstroem_service: MinStroemService,
) -> None:
    c = minstroem_service.client()
    c.close()

    with pytest.raises(RuntimeError):
        c.get_json("/me/addresses")

    assert minstroem_service.requests == []


def test_a_client_is_reusable_between_calls(
    minstroem_service: MinStroemService,
) -> None:
    with minstroem_service.client() as c:
        first = c.get_json("/me/addresses")
        second = c.get_json("/me/addresses")

    assert first == second
    assert len(minstroem_service.requests) == 2


def test_the_policy_argument_controls_the_number_of_attempts(
    minstroem_service: MinStroemService,
) -> None:
    minstroem_service.overrides["addresses"] = _reply(
        503, {"error": True, "reason": "d", "errorCode": "upstream_unavailable"}
    )
    policy = RetryPolicy(max_attempts=2, base_delay=0.0, max_delay=0.0)

    with minstroem_service.client(policy=policy) as c, pytest.raises(MinStroemError):
        c.get_json("/me/addresses")

    assert len(minstroem_service.requests) == 2


# --- showcase ---------------------------------------------------------------


def test_main_without_a_key_prints_one_line_and_returns(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(API_KEY_ENV, raising=False)

    client.main()

    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    assert API_KEY_ENV in lines[0]


def test_main_with_a_key_lists_regions_and_capabilities_but_not_the_address(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    minstroem_service: MinStroemService,
) -> None:
    monkeypatch.setenv(API_KEY_ENV, "test-key")
    monkeypatch.setattr(client, "MinStroemClient", minstroem_service.client)

    client.main()

    out = capsys.readouterr().out
    assert "1 address(es)" in out
    assert "DK1" in out
    assert "forecastPrices" in out
    assert "Testvej" not in out
    assert "test-key" not in out


def test_main_survives_an_address_without_a_region(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    address: dict[str, Any] = minstroem_address()
    address["region"] = None
    service = MinStroemService([address])
    monkeypatch.setenv(API_KEY_ENV, "test-key")
    monkeypatch.setattr(client, "MinStroemClient", service.client)

    client.main()

    assert "region None" in capsys.readouterr().out
