import asyncio
import concurrent.futures
import threading
from collections.abc import Callable, Coroutine, Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import pytest

from energydata.utils import ApiClient, RetryPolicy
from energydata.utils.readers import Format, Parsed

BASE = "https://api.example.test"
FAST = RetryPolicy(base_delay=0.0, max_delay=0.0)
TIMEOUT = 10.0  # a hang fails the test instead of the suite
LOOP_THREAD = "api-client-loop"

type Handler = Callable[[httpx.Request], httpx.Response]
type AsyncHandler = Callable[[httpx.Request], Coroutine[Any, Any, httpx.Response]]


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"path": request.url.path})


def _loop_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == LOOP_THREAD and t.is_alive()]


@contextmanager
def _client(
    handler: Handler | AsyncHandler = _ok,
    *,
    max_concurrency: int = 4,
    headers: dict[str, str] | None = None,
) -> Iterator[ApiClient]:
    client = ApiClient(
        BASE,
        transport=httpx.MockTransport(handler),
        policy=FAST,
        max_concurrency=max_concurrency,
        headers=headers,
    )
    with client:
        yield client


def _get(
    client: ApiClient,
    path: str = "/p",
    *,
    params: dict[str, int] | None = None,
    fmt: Format | None = None,
) -> Callable[[], Coroutine[Any, Any, Parsed]]:
    async def work() -> Parsed:
        return await client.request("GET", path, params=params, fmt=fmt)

    return work


@pytest.fixture(autouse=True)
def _no_leaked_loop_threads() -> Iterator[None]:
    before = len(_loop_threads())
    yield
    assert len(_loop_threads()) == before, "a test leaked an api-client-loop thread"


# --- construction -------------------------------------------------------------


@pytest.mark.parametrize(
    ("base_url", "max_concurrency"),
    [("", 4), (None, 4), (BASE, 0), (BASE, -1)],
)
def test_api_client_rejects_bad_constructor_arguments(
    base_url: str | None, max_concurrency: int
) -> None:
    with pytest.raises(ValueError):
        ApiClient(base_url, max_concurrency=max_concurrency)  # type: ignore[arg-type]  # None is malformed on purpose


def test_api_client_error_names_the_bad_value() -> None:
    with pytest.raises(ValueError, match="-3"):
        ApiClient(BASE, max_concurrency=-3)


def test_api_client_exposes_its_settings_as_properties() -> None:
    client = ApiClient(BASE, max_concurrency=1)

    assert client.base_url == BASE
    assert client.max_concurrency == 1
    assert client.closed is False


def test_api_client_starts_no_thread_until_first_use() -> None:
    before = len(_loop_threads())

    client = ApiClient(BASE)

    assert len(_loop_threads()) == before
    client.close()
    assert client.closed is True
    assert len(_loop_threads()) == before


def test_api_client_closed_before_first_use_refuses_run_without_a_thread() -> None:
    client = ApiClient(BASE)
    client.close()
    client.close()

    async def work() -> None:
        return None

    with pytest.raises(RuntimeError, match="closed"):
        client.run(work)
    assert _loop_threads() == []


# --- requests -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("base", "path", "expected"),
    [
        ("https://x.test/", "/p", "https://x.test/p?a=1"),
        ("https://x.test/api/", "/v1", "https://x.test/api/v1?a=1"),
        ("https://x.test/api", "v1", "https://x.test/api/v1?a=1"),
        ("https://x.test/api/", "/v1/p", "https://x.test/api/v1/p?a=1"),
    ],
)
def test_api_client_joins_base_url_and_path_and_sends_params(
    base: str, path: str, expected: str
) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={})

    with ApiClient(base, transport=httpx.MockTransport(handler)) as client:
        client.run(_get(client, path, params={"a": 1}))

    assert seen == [expected]


def test_api_client_parses_the_body_through_the_readers() -> None:
    with _client() as client:
        body = client.run(_get(client, "/data"))

    assert body == {"path": "/data"}


def test_api_client_fmt_overrides_the_content_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(
            200, content=b'{"a": 1}', headers={"Content-Type": "text/plain"}
        )

    with _client(handler) as client:
        body = client.run(_get(client, fmt="json"))

    assert body == {"a": 1}


def test_api_client_retries_a_503_then_parses_the_200() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": 1})

    with _client(handler) as client:
        body = client.run(_get(client))

    assert body == {"ok": 1}
    assert calls == 2


def test_api_client_a_400_is_not_retried_and_raises_a_status_error() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        nonlocal calls
        calls += 1
        return httpx.Response(400, json={"error": "bad"})

    with _client(handler) as client, pytest.raises(httpx.HTTPStatusError):
        client.run(_get(client))

    assert calls == 1


def test_api_client_sends_its_headers_copied_at_construction() -> None:
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("X-Key"))
        return httpx.Response(200, json={})

    headers = {"X-Key": "1"}
    with _client(handler, headers=headers) as client:
        headers["X-Key"] = "2"
        client.run(_get(client))

    assert seen == ["1"]


def test_api_client_unreadable_header_fails_the_same_way_every_time() -> None:
    client = ApiClient(
        BASE,
        headers={"User-Agent": "café"},
        transport=httpx.MockTransport(_ok),
    )

    with pytest.raises(UnicodeEncodeError):
        client.run(_get(client))
    with pytest.raises(UnicodeEncodeError):
        client.run(_get(client))
    client.close()

    assert _loop_threads() == []


# --- concurrency --------------------------------------------------------------


def _counting_handler() -> tuple[AsyncHandler, Callable[[], int]]:
    in_flight = 0
    peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return httpx.Response(200, json={})

    return handler, lambda: peak


@pytest.mark.parametrize("cap", [1, 3])
def test_api_client_caps_requests_in_flight(cap: int) -> None:
    handler, peak = _counting_handler()

    with _client(handler, max_concurrency=cap) as client:

        async def many() -> list[Parsed]:
            return await asyncio.gather(
                *[client.request("GET", "/p") for _ in range(8)]
            )

        client.run(many)

    assert peak() == cap


def test_api_client_cap_is_shared_across_threads() -> None:
    handler, peak = _counting_handler()

    with _client(handler, max_concurrency=2) as client:

        async def many() -> list[Parsed]:
            return await asyncio.gather(
                *[client.request("GET", "/p") for _ in range(5)]
            )

        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(client.run, many) for _ in range(2)]
            for future in futures:
                future.result(TIMEOUT)

    assert peak() == 2


# --- run ----------------------------------------------------------------------


def test_api_client_run_works_from_inside_a_running_event_loop() -> None:
    with _client() as client:

        async def caller() -> object:
            return client.run(_get(client))  # blocks this loop; the client's runs apart

        body = asyncio.run(asyncio.wait_for(caller(), TIMEOUT))

    assert body == {"path": "/p"}


def test_api_client_run_works_from_a_thread_with_its_own_loop() -> None:
    with _client() as client, concurrent.futures.ThreadPoolExecutor(1) as pool:
        future = pool.submit(asyncio.run, _call_run(client))

        assert future.result(TIMEOUT) == {"path": "/p"}


async def _call_run(client: ApiClient) -> object:
    return client.run(_get(client))


def test_api_client_concurrent_first_use_starts_one_loop_and_runs_off_the_caller() -> (
    None
):
    barrier = threading.Barrier(8)
    seen: list[int] = []

    async def where() -> None:
        seen.append(threading.get_ident())

    with _client() as client:

        def caller() -> None:
            barrier.wait(TIMEOUT)
            client.run(where)

        with concurrent.futures.ThreadPoolExecutor(8) as pool:
            for future in [pool.submit(caller) for _ in range(8)]:
                future.result(TIMEOUT)

        assert len(_loop_threads()) == 1
        assert len(set(seen)) == 1
        assert seen[0] != threading.get_ident()
        assert seen[0] == _loop_threads()[0].ident


def test_api_client_is_reusable_across_many_runs_on_one_thread() -> None:
    with _client() as client:
        for _ in range(50):
            assert client.run(_get(client)) == {"path": "/p"}
            assert len(_loop_threads()) == 1


def test_api_client_run_reraises_the_works_own_exception_object() -> None:
    error = ValueError("x")

    async def work() -> None:
        raise error

    with _client() as client:
        with pytest.raises(ValueError) as caught:
            client.run(work)

        assert caught.value is error
        assert client.run(_get(client)) == {"path": "/p"}  # still usable


def test_api_client_run_preserves_the_exception_type() -> None:
    async def work() -> None:
        raise ZeroDivisionError

    with _client() as client, pytest.raises(ZeroDivisionError):
        client.run(work)


def test_api_client_run_returns_falsy_results() -> None:
    async def work() -> int:
        return 0

    with _client() as client:
        assert client.run(work) == 0


@pytest.mark.parametrize("method", ["run", "close"])
def test_api_client_run_and_close_from_the_loop_thread_raise(method: str) -> None:
    with _client() as client:

        async def inner() -> None:
            return None

        async def work() -> None:
            if method == "run":
                client.run(inner)
            else:
                client.close()

        with pytest.raises(RuntimeError, match="own loop thread"):
            client.run(work)

        assert client.closed is False
        assert client.run(_get(client)) == {"path": "/p"}


@pytest.mark.parametrize("started", [False, True])
def test_api_client_request_on_a_foreign_loop_raises(started: bool) -> None:
    with _client() as client:
        if started:
            client.run(_get(client))

        with pytest.raises(RuntimeError, match="own loop"):
            asyncio.run(client.request("GET", "/p"))


def test_api_client_request_outside_any_loop_raises_on_await() -> None:
    client = ApiClient(BASE)
    coroutine = client.request("GET", "/p")

    with pytest.raises(RuntimeError, match="own loop"):
        asyncio.run(coroutine)


# --- close --------------------------------------------------------------------


def test_api_client_close_is_idempotent_joins_the_thread_and_refuses_run() -> None:
    client = ApiClient(BASE, transport=httpx.MockTransport(_ok), policy=FAST)
    client.run(_get(client))
    assert len(_loop_threads()) == 1

    client.close()
    client.close()

    assert client.closed is True
    assert _loop_threads() == []
    with pytest.raises(RuntimeError, match="closed"):
        client.run(_get(client))


def test_api_client_context_manager_returns_itself_and_closes_on_exit() -> None:
    client = ApiClient(BASE, transport=httpx.MockTransport(_ok))

    with client as entered:
        assert entered is client
        assert client.closed is False

    assert client.closed is True


def test_api_client_context_manager_closes_and_propagates_an_error() -> None:
    client = ApiClient(BASE, transport=httpx.MockTransport(_ok), policy=FAST)

    with pytest.raises(KeyError), client:
        client.run(_get(client))
        raise KeyError("inside")

    assert client.closed is True


def test_api_client_close_cancels_work_running_in_another_thread() -> None:
    client = ApiClient(BASE, transport=httpx.MockTransport(_ok), policy=FAST)
    started = threading.Event()

    async def forever() -> None:
        started.set()
        await asyncio.Event().wait()

    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        future = pool.submit(client.run, forever)
        assert started.wait(TIMEOUT)

        client.close()

        with pytest.raises(concurrent.futures.CancelledError):
            future.result(TIMEOUT)
    assert _loop_threads() == []


def test_api_client_an_interrupted_run_cancels_the_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = asyncio.run_coroutine_threadsafe
    cancelled = threading.Event()
    started = threading.Event()

    class Interrupted:
        def __init__(self, inner: concurrent.futures.Future[Any]) -> None:
            self.inner = inner

        def result(self) -> None:
            assert started.wait(TIMEOUT)
            raise KeyboardInterrupt

        def cancel(self) -> bool:
            return self.inner.cancel()

    def patched(
        coroutine: Coroutine[Any, Any, Any], loop: asyncio.AbstractEventLoop
    ) -> concurrent.futures.Future[Any] | Interrupted:
        future = real(coroutine, loop)
        if getattr(coroutine, "__qualname__", "").endswith("call"):
            return Interrupted(future)
        return future

    async def work() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    with _client() as client:
        monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", patched)

        with pytest.raises(KeyboardInterrupt):
            client.run(work)

        assert cancelled.wait(TIMEOUT)
        monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", real)
        assert client.run(_get(client)) == {"path": "/p"}


def test_api_client_main_showcase_runs(capsys: pytest.CaptureFixture[str]) -> None:
    from energydata.utils.api_client import main

    main()

    out = capsys.readouterr().out
    assert "one request" in out
    assert "closed after the block: True" in out
