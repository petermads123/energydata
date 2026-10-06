import asyncio
import json
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from energydata.energidataservice import (
    EnergiDataServiceClient,
    EnergiDataServiceError,
)
from energydata.energidataservice.client import BASE_URL, main
from energydata.utils import RetryPolicy

CPH = ZoneInfo("Europe/Copenhagen")
FAST = RetryPolicy(base_delay=0.0, max_delay=0.0)
START = datetime(2025, 1, 15, tzinfo=CPH)
DAY = timedelta(days=1)

type Handler = Callable[[httpx.Request], httpx.Response]
type AsyncHandler = Callable[[httpx.Request], Coroutine[Any, Any, httpx.Response]]


def _client(
    handler: Handler | AsyncHandler,
    *,
    max_span: timedelta = timedelta(days=31),
    max_concurrency: int = 4,
) -> EnergiDataServiceClient:
    return EnergiDataServiceClient(
        transport=httpx.MockTransport(handler),
        policy=FAST,
        max_span=max_span,
        max_concurrency=max_concurrency,
    )


def _empty(request: httpx.Request) -> httpx.Response:  # noqa: ARG001 - handler signature
    return httpx.Response(200, json={"total": 0, "records": []})


def _recorder() -> tuple[list[httpx.Request], Handler]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"total": 1, "records": [{"n": len(seen)}]})

    return seen, handler


# --- request parameters -------------------------------------------------------


def test_client_sends_the_exact_params_to_the_dataset_path() -> None:
    seen, handler = _recorder()
    with _client(handler) as client:
        client.get_dataset(
            "Elspotprices",
            START,
            START + DAY,
            filters={"PriceArea": ["DK1", "DK2"]},
            columns=["HourUTC", "PriceArea", "SpotPriceEUR"],
            sort_by="HourUTC",
        )

    (request,) = seen
    assert request.url.host == "api.energidataservice.dk"
    assert request.url.path == "/dataset/Elspotprices"
    assert dict(request.url.params) == {
        "start": "2025-01-14T23:00",
        "end": "2025-01-15T23:00",
        "timezone": "UTC",
        "limit": "0",
        "filter": '{"PriceArea":["DK1","DK2"]}',
        "columns": "HourUTC,PriceArea,SpotPriceEUR",
        "sort": "HourUTC asc",
    }


def test_client_omits_filter_columns_and_sort_when_not_given() -> None:
    seen, handler = _recorder()
    with _client(handler) as client:
        client.get_dataset("X", START, START + DAY)

    assert set(seen[0].url.params) == {"start", "end", "timezone", "limit"}


def test_client_sends_utc_for_a_bound_in_any_zone() -> None:
    seen, handler = _recorder()
    with _client(handler) as client:
        client.get_dataset(
            "X",
            datetime(2025, 7, 1, 2, tzinfo=CPH),
            datetime(2025, 7, 1, 3, tzinfo=UTC),
        )

    assert seen[0].url.params["start"] == "2025-07-01T00:00"
    assert seen[0].url.params["end"] == "2025-07-01T03:00"


def test_client_bare_string_filter_value_and_columns_are_not_split() -> None:
    seen, handler = _recorder()
    with _client(handler) as client:
        client.get_dataset(
            "X",
            START,
            START + DAY,
            filters={"PriceArea": "DK1"},  # type: ignore[dict-item]  # a bare string is a plausible slip
            columns="HourUTC",
        )

    params = seen[0].url.params
    assert params["filter"] == '{"PriceArea":["DK1"]}'
    assert params["columns"] == "HourUTC"


def test_client_does_not_mutate_its_filters() -> None:
    _, handler = _recorder()
    filters = {"PriceArea": ["DK2", "DK1"]}
    columns = ["a", "b"]
    with _client(handler) as client:
        client.get_dataset("X", START, START + DAY, filters=filters, columns=columns)

    assert filters == {"PriceArea": ["DK2", "DK1"]}
    assert columns == ["a", "b"]


def test_client_filter_is_compact_json_and_keeps_non_ascii_values_parseable() -> None:
    seen, handler = _recorder()
    with _client(handler) as client:
        client.get_dataset("X", START, START + DAY, filters={"Name": ["Ærø", "a b"]})

    assert json.loads(seen[0].url.params["filter"]) == {"Name": ["Ærø", "a b"]}
    assert " " not in seen[0].url.params["filter"].replace("a b", "")


def test_client_is_idempotent() -> None:
    _, handler = _recorder()
    with _client(handler) as client:
        first = client.get_dataset("X", START, START + DAY)
        second = client.get_dataset("X", START, START + DAY)

    assert [r["n"] for r in first] == [1]
    assert [r["n"] for r in second] == [2]  # the handler counts calls, not the client


# --- windows ------------------------------------------------------------------


def test_client_splits_a_long_period_and_concatenates_in_window_order() -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        start = request.url.params["start"]
        await asyncio.sleep(
            {"2025-01-14T23:00": 0.05}.get(start, 0.0)
        )  # first is slowest
        return httpx.Response(200, json={"total": 1, "records": [{"start": start}]})

    with _client(handler, max_span=DAY) as client:
        records = client.get_dataset("X", START, START + 3 * DAY)

    assert [r["start"] for r in records] == [
        "2025-01-14T23:00",
        "2025-01-15T23:00",
        "2025-01-16T23:00",
    ]
    ends = sorted((r.url.params["start"], r.url.params["end"]) for r in seen)
    assert ends[0][1] == ends[1][0] and ends[1][1] == ends[2][0]  # contiguous


def test_client_short_period_is_one_request() -> None:
    seen, handler = _recorder()
    with _client(handler, max_span=DAY) as client:
        client.get_dataset("X", START, START + DAY)

    assert len(seen) == 1


def test_client_windows_are_fetched_concurrently_but_capped() -> None:
    state = {"now": 0, "peak": 0}

    async def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        state["now"] += 1
        state["peak"] = max(state["peak"], state["now"])
        await asyncio.sleep(0.02)
        state["now"] -= 1
        return httpx.Response(200, json={"records": []})

    with _client(handler, max_span=DAY, max_concurrency=2) as client:
        client.get_dataset("X", START, START + 6 * DAY)

    assert state["peak"] == 2


# --- max_span override ----------------------------------------------------------


def test_max_span_overrides_the_client_window_for_one_call() -> None:
    seen, handler = _recorder()
    with _client(handler, max_span=DAY) as client:
        client.get_dataset("X", START, START + 10 * DAY, max_span=10 * DAY)

    assert len(seen) == 1
    assert seen[0].url.params["end"] == "2025-01-24T23:00"


def test_max_span_none_keeps_the_client_window() -> None:
    seen, handler = _recorder()
    with _client(handler, max_span=DAY) as client:
        client.get_dataset("X", START, START + 10 * DAY, max_span=None)

    assert len(seen) == 10


def test_max_span_can_be_smaller_than_the_client_window() -> None:
    seen, handler = _recorder()
    with _client(handler, max_span=10 * DAY) as client:
        client.get_dataset("X", START, START + 4 * DAY, max_span=DAY)

    assert len(seen) == 4


def test_max_span_does_not_change_the_client_window_afterwards() -> None:
    seen, handler = _recorder()
    with _client(handler, max_span=DAY) as client:
        client.get_dataset("X", START, START + 3 * DAY, max_span=3 * DAY)
        client.get_dataset("X", START, START + 3 * DAY)

    assert len(seen) == 1 + 3


def test_max_span_equal_to_the_period_is_one_window_across_a_dst_change() -> None:
    seen, handler = _recorder()
    start = datetime(2026, 3, 1, tzinfo=CPH)
    end = datetime(2026, 4, 1, tzinfo=CPH)  # wall-clock span, one hour short in UTC
    with _client(handler, max_span=DAY) as client:
        client.get_dataset("X", start, end, max_span=end - start)

    assert len(seen) == 1


def test_max_span_of_one_minute_is_valid() -> None:
    seen, handler = _recorder()
    with _client(handler) as client:
        client.get_dataset(
            "X", START, START + timedelta(minutes=3), max_span=timedelta(minutes=1)
        )

    assert len(seen) == 3


@pytest.mark.parametrize(
    "span",
    [
        timedelta(0),
        timedelta(days=-1),
        timedelta(seconds=30),
        timedelta(minutes=1, seconds=1),
    ],
)
def test_max_span_that_is_not_positive_whole_minutes_raises_before_any_request(
    span: timedelta,
) -> None:
    seen, handler = _recorder()
    with _client(handler) as client, pytest.raises(ValueError, match="max_span"):
        client.get_dataset("X", START, START + DAY, max_span=span)

    assert seen == []


def test_max_span_error_names_the_offending_span() -> None:
    with _client(_empty) as client, pytest.raises(ValueError, match="seconds=30"):
        client.get_dataset("X", START, START + DAY, max_span=timedelta(seconds=30))


def test_fetch_dataset_max_span_works_on_the_client_loop() -> None:
    seen, handler = _recorder()

    async def main_() -> list[Any]:
        return await client.fetch_dataset("X", START, START + 5 * DAY, max_span=5 * DAY)

    with _client(handler, max_span=DAY) as client:
        records = client.run(main_)

    assert len(seen) == 1
    assert len(records) == 1


# --- payload checks -----------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"total": 3, "records": [{}, {}]},
        {"total": 1},
        {"records": {}},
        {"records": None},
        [],
        "text",
        {"records": [1]},
        {"records": [{}, None]},
    ],
)
def test_client_rejects_a_payload_that_is_not_what_the_api_promises(
    payload: object,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(200, json=payload)

    with _client(handler) as client, pytest.raises(EnergiDataServiceError, match="X"):
        client.get_dataset("X", START, START + DAY)


@pytest.mark.parametrize(
    "payload",
    [
        {"total": 2, "records": [{}, {}]},
        {"total": 1, "records": [{}, {}]},  # only a shortfall is an error
        {"records": []},
        {"total": True, "records": []},  # a bool is not a count
        {"total": 5.0, "records": [{}]},  # nor is a float
        {"total": None, "records": []},
    ],
)
def test_client_accepts_a_total_that_is_not_a_shortfall(
    payload: dict[str, Any],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(200, json=payload)

    with _client(handler) as client:
        records = client.get_dataset("X", START, START + DAY)

    assert records == payload["records"]


def test_client_error_names_the_dataset_and_the_shortfall() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(200, json={"total": 9, "records": [{}]})

    with _client(handler) as client, pytest.raises(EnergiDataServiceError) as caught:
        client.get_dataset("Elspotprices", START, START + DAY)

    assert "Elspotprices" in str(caught.value)
    assert "total=9" in str(caught.value)


def test_client_empty_records_with_zero_total_is_an_empty_list() -> None:
    with _client(_empty) as client:
        assert client.get_dataset("X", START, START + DAY) == []


def test_client_unicode_records_survive() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(200, json={"records": [{"name": "Ærø ☃"}]})

    with _client(handler) as client:
        assert client.get_dataset("X", START, START + DAY) == [{"name": "Ærø ☃"}]


# --- failures -----------------------------------------------------------------


def test_client_http_400_surfaces_as_a_status_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(400, json={"error": "bad"})

    with _client(handler) as client, pytest.raises(httpx.HTTPStatusError):
        client.get_dataset("X", START, START + DAY)


def test_client_retries_a_503_then_returns_the_records() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"records": [{"ok": 1}]})

    with _client(handler) as client:
        assert client.get_dataset("X", START, START + DAY) == [{"ok": 1}]
    assert calls["n"] == 2


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (datetime(2025, 1, 15), START + DAY),  # noqa: DTZ001 - naive on purpose
        (START, datetime(2025, 1, 16)),  # noqa: DTZ001 - naive on purpose
        (START, START),  # empty
        (START + DAY, START),  # inverted
        (START.replace(second=30), START + DAY),  # seconds the API cannot take
        (START, START.replace(microsecond=5) + DAY),
    ],
)
def test_client_rejects_bad_bounds_without_a_request(
    start: datetime, end: datetime
) -> None:
    seen, handler = _recorder()
    with _client(handler) as client, pytest.raises(ValueError):
        client.get_dataset("X", start, end)

    assert seen == []


def test_client_seconds_error_names_the_bound() -> None:
    with _client(_empty) as client, pytest.raises(ValueError, match="00:00:30"):
        client.get_dataset("X", START.replace(second=30), START + DAY)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_span": timedelta(0)},
        {"max_span": timedelta(days=-1)},
        {"max_span": timedelta(seconds=90)},
        {"max_concurrency": 0},
    ],
)
def test_client_rejects_bad_construction(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        EnergiDataServiceClient(**kwargs)


def test_client_points_at_the_energi_data_service_base_url() -> None:
    with EnergiDataServiceClient() as client:
        assert client.base_url == BASE_URL == "https://api.energidataservice.dk"


# --- loops --------------------------------------------------------------------


def test_client_fetch_dataset_awaited_outside_its_loop_raises() -> None:
    with _client(_empty) as client, pytest.raises(RuntimeError):
        asyncio.run(client.fetch_dataset("X", START, START + DAY))


def test_client_get_dataset_works_inside_a_running_event_loop() -> None:
    async def main_() -> list[Any]:
        with _client(_empty) as client:
            return client.get_dataset("X", START, START + DAY)

    assert asyncio.run(main_()) == []


def test_client_get_dataset_after_close_raises_runtime_error() -> None:
    client = _client(_empty)
    client.close()

    with pytest.raises(RuntimeError):
        client.get_dataset("X", START, START + DAY)


def test_client_main_showcase_runs_against_a_stub(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real = EnergiDataServiceClient

    def stub() -> EnergiDataServiceClient:
        return real(transport=httpx.MockTransport(_empty))

    monkeypatch.setattr(
        "energydata.energidataservice.client.EnergiDataServiceClient", stub
    )

    main()

    assert "Elspotprices: 0 records" in capsys.readouterr().out
