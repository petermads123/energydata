"""A source-agnostic API client that runs its HTTP work on a private event loop."""

import asyncio
import threading
from collections.abc import Awaitable, Callable, Mapping
from typing import Self

import httpx

from .readers import Format, Parsed, read_response
from .retry import DEFAULT_TIMEOUT, RetryPolicy, async_request_with_retry


class ApiClient:
    """Base class for a client of one HTTP API.

    The client owns one private asyncio event loop on a daemon thread, started on
    first use, together with an `httpx.AsyncClient` and a semaphore that live on
    that loop. Requests go through the async retry wrapper and are parsed by
    `read_response`. Synchronous callers use `run`, which works from any thread,
    including one that already has a running event loop such as Jupyter's.
    Subclasses add endpoint methods: `async` ones built on `request`, and
    synchronous ones built on `run`.

    The client is reusable, so its connection pool survives between calls, and
    closable: use it as a context manager or call `close`.
    """

    def __init__(
        self,
        base_url: str,
        *,
        policy: RetryPolicy | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_concurrency: int = 4,
        headers: Mapping[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Create a client; no thread or connection is opened until first use.

        Args:
            base_url: The URL every request path is appended to.
            policy: Retry settings. Defaults to `RetryPolicy()`.
            timeout: Per-request timeout in seconds.
            max_concurrency: The most requests in flight at once, across every
                caller of this client.
            headers: Headers sent with every request.
            transport: An `httpx` transport to send through, for tests (for
                example `httpx.MockTransport`).

        Raises:
            ValueError: If `base_url` is empty or `max_concurrency` is below 1.
        """
        if not base_url:
            raise ValueError(f"base_url must not be empty, got {base_url!r}")
        if max_concurrency < 1:
            raise ValueError(
                f"max_concurrency must be at least 1, got {max_concurrency}"
            )
        self._base_url = base_url
        self._policy = policy
        self._timeout = timeout
        self._max_concurrency = max_concurrency
        self._headers = dict(headers) if headers is not None else None
        self._transport = transport
        self._lock = threading.Lock()
        self._closed = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._http: httpx.AsyncClient | None = None
        self._semaphore: asyncio.Semaphore | None = None

    @property
    def base_url(self) -> str:
        """The URL every request path is appended to."""
        return self._base_url

    @property
    def max_concurrency(self) -> int:
        """The most requests in flight at once."""
        return self._max_concurrency

    @property
    def closed(self) -> bool:
        """Whether `close` has been called."""
        return self._closed

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int | float] | None = None,
        fmt: Format | None = None,
    ) -> Parsed:
        """Send one request on the client's loop and parse the body.

        This is the extension point for subclasses and may only be awaited
        from inside `run`, since the connection pool and the concurrency cap
        belong to the client's own loop.

        Args:
            method: The HTTP method.
            path: Appended to `base_url`.
            params: Query parameters.
            fmt: The body format. `None` picks it from the `Content-Type`.

        Returns:
            The parsed body.

        Raises:
            RuntimeError: If awaited on any loop other than the client's.
            RetriesExhaustedError: As for `async_request_with_retry`.
            HoldoffTooLongError: As for `async_request_with_retry`.
            httpx.HTTPStatusError: As for `async_request_with_retry`.
            ParseError: If the body cannot be parsed.
        """
        http, semaphore = self._on_own_loop()
        url = f"{self._base_url.rstrip('/')}/{path.lstrip('/')}"
        async with semaphore:
            response = await async_request_with_retry(
                method, url, client=http, policy=self._policy, params=params
            )
        return read_response(response, fmt)

    def run[T](self, work: Callable[[], Awaitable[T]]) -> T:
        """Run `work()` on the client's loop and block until it finishes.

        Args:
            work: Called on the loop thread; returns the awaitable to run.

        Returns:
            Whatever the awaitable returns. Its exception is re-raised here.

        Raises:
            RuntimeError: If the client is closed, or if called from the
                client's own loop thread, which would deadlock.
        """
        loop = self._ensure_started()
        if self._thread is not None and threading.get_ident() == self._thread.ident:
            raise RuntimeError("run() was called from the client's own loop thread")

        async def call() -> T:
            return await work()

        future = asyncio.run_coroutine_threadsafe(call(), loop)
        try:
            return future.result()
        except BaseException:
            future.cancel()  # an interrupt must not leave the work running
            raise

    def close(self) -> None:
        """Close the HTTP client, stop the loop and join its thread.

        Work still running on the loop is cancelled. Safe to call twice.

        Raises:
            RuntimeError: If called from the client's own loop thread.
        """
        with self._lock:
            if self._closed:
                return
            thread, loop = self._thread, self._loop
            if thread is not None and threading.get_ident() == thread.ident:
                raise RuntimeError(
                    "close() was called from the client's own loop thread"
                )
            self._closed = True
            if thread is None or loop is None:
                return
            asyncio.run_coroutine_threadsafe(self._shutdown(), loop).result()
            loop.call_soon_threadsafe(loop.stop)
            thread.join()
            loop.close()

    def __enter__(self) -> Self:
        """Return the client."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close the client."""
        self.close()

    def _on_own_loop(self) -> tuple[httpx.AsyncClient, asyncio.Semaphore]:
        """Return the HTTP client and semaphore, if running on the client's loop."""
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if (
            running is None
            or running is not self._loop
            or self._http is None
            or self._semaphore is None
        ):
            raise RuntimeError(
                "request() may only be awaited on the client's own loop; use run()"
            )
        return self._http, self._semaphore

    def _ensure_started(self) -> asyncio.AbstractEventLoop:
        """Start the loop thread on first use and return the loop."""
        with self._lock:
            if self._closed:
                raise RuntimeError("the client is closed")
            if self._loop is None:
                loop = asyncio.new_event_loop()
                thread = threading.Thread(
                    target=self._serve,
                    args=(loop,),
                    name="api-client-loop",
                    daemon=True,
                )
                thread.start()
                try:
                    asyncio.run_coroutine_threadsafe(self._open(), loop).result()
                except BaseException:
                    loop.call_soon_threadsafe(loop.stop)  # leave nothing half-started
                    thread.join()
                    loop.close()
                    raise
                self._loop, self._thread = loop, thread
            return self._loop

    @staticmethod
    def _serve(loop: asyncio.AbstractEventLoop) -> None:
        """Run `loop` until it is stopped."""
        asyncio.set_event_loop(loop)
        loop.run_forever()

    async def _open(self) -> None:
        """Create the HTTP client and semaphore on the loop thread."""
        self._http = httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
            headers=self._headers,
            transport=self._transport,
        )
        self._semaphore = asyncio.Semaphore(self._max_concurrency)

    async def _shutdown(self) -> None:
        """Cancel the work still on the loop, then close the HTTP client."""
        current = asyncio.current_task()
        others = [task for task in asyncio.all_tasks() if task is not current]
        for task in others:
            task.cancel()
        await asyncio.gather(*others, return_exceptions=True)
        if self._http is not None:
            await self._http.aclose()


def main() -> None:
    """Showcase this module's functionality."""

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"path": request.url.path, "query": dict(request.url.params)}
        )

    base_url = "https://api.example.test"
    max_concurrency = 2  # requests in flight at once, across all callers
    transport = httpx.MockTransport(respond)  # a stand-in server; omit to go online

    with ApiClient(
        base_url, max_concurrency=max_concurrency, transport=transport
    ) as client:
        method = "GET"  # "GET", "POST"
        path = "/prices"
        params = {"area": "DK1"}

        async def fetch_one() -> Parsed:
            return await client.request(method, path, params=params)

        body = client.run(fetch_one)

        print(f"one request: {body!r}")

        # Three requests at once, still capped at max_concurrency in flight.
        async def fetch_three() -> list[Parsed]:
            return await asyncio.gather(*[fetch_one() for _ in range(3)])

        bodies = client.run(fetch_three)

        print(f"three requests: {len(bodies)} bodies")
        print(f"closed inside the block: {client.closed}")
    print(f"closed after the block: {client.closed}")


if __name__ == "__main__":
    main()
