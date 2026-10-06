"""Send HTTP requests with retries, exponential backoff and server holdoffs."""

import asyncio
import math
import random
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

RETRY_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})
RETRY_EXCEPTIONS: tuple[type[Exception], ...] = (
    httpx.TimeoutException,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
)
DEFAULT_TIMEOUT: float = 30.0

type HoldoffFn = Callable[[httpx.Response], float | None]

_SECONDS = re.compile(r"[0-9]+(\.[0-9]+)?")


@dataclass(frozen=True)
class RetryPolicy:
    """Settings for `request_with_retry` and `async_request_with_retry`.

    Attributes:
        max_attempts: Total attempts, the first one included. At least 1.
        base_delay: Seconds before the first retry, doubled for each later one.
        max_delay: Upper bound, in seconds, of a computed backoff delay.
        max_holdoff: Longest server-requested wait, in seconds, that is honoured.
        holdoff_reader: Reads a holdoff in seconds from a response, or returns
            `None` when it has none. Takes precedence over `Retry-After`.
    """

    max_attempts: int = 5
    base_delay: float = 1.0
    max_delay: float = 60.0
    max_holdoff: float = 300.0
    holdoff_reader: HoldoffFn | None = None

    def __post_init__(self) -> None:
        """Validate the settings.

        Raises:
            ValueError: If a setting is out of range.
        """
        if self.max_attempts < 1:
            raise ValueError(
                f"max_attempts must be at least 1, got {self.max_attempts}"
            )
        if not 0 <= self.base_delay < math.inf:
            raise ValueError(
                f"base_delay must be finite and non-negative, got {self.base_delay}"
            )
        if not self.base_delay <= self.max_delay < math.inf:
            raise ValueError(
                f"max_delay must be finite and at least base_delay ({self.base_delay}), "
                f"got {self.max_delay}"
            )
        if not 0 <= self.max_holdoff < math.inf:
            raise ValueError(
                f"max_holdoff must be finite and non-negative, got {self.max_holdoff}"
            )


class RetryError(Exception):
    """Base of the errors raised when a request is given up on.

    Attributes:
        attempts: How many attempts were made.
        response: The last response received, if any.
    """

    def __init__(
        self, message: str, attempts: int, response: httpx.Response | None
    ) -> None:
        """Store the attempt count and last response.

        Args:
            message: The error message.
            attempts: How many attempts were made.
            response: The last response received, if any.
        """
        super().__init__(message)
        self.attempts = attempts
        self.response = response


class RetriesExhaustedError(RetryError):
    """Every attempt failed with a retryable outcome.

    Exactly one of `response` and `exception` is set.

    Attributes:
        exception: The last transport error, if the last attempt raised one.
    """

    def __init__(
        self,
        attempts: int,
        response: httpx.Response | None,
        exception: Exception | None,
    ) -> None:
        """Build the error and name the last outcome in its message.

        Args:
            attempts: How many attempts were made.
            response: The last response, when the last attempt got a status.
            exception: The last transport error, when the last attempt raised.
        """
        if response is not None:
            last = f"HTTP {response.status_code}"
        else:
            last = f"{type(exception).__name__}: {exception}"
        super().__init__(
            f"gave up after {attempts} attempts, last: {last}", attempts, response
        )
        self.exception = exception


class HoldoffTooLongError(RetryError):
    """The server asked for a wait longer than the policy allows.

    Attributes:
        holdoff: The wait the server asked for, in seconds.
    """

    def __init__(
        self,
        attempts: int,
        response: httpx.Response,
        holdoff: float,
        max_holdoff: float,
    ) -> None:
        """Build the error and state both the requested wait and the cap.

        Args:
            attempts: How many attempts were made.
            response: The response that carried the holdoff.
            holdoff: The requested wait, in seconds.
            max_holdoff: The longest wait the policy allows, in seconds.
        """
        super().__init__(
            f"server asked to wait {holdoff} seconds, longer than the "
            f"{max_holdoff} seconds allowed (HTTP {response.status_code})",
            attempts,
            response,
        )
        self.holdoff = holdoff


def retry_after_seconds(
    response: httpx.Response, now: datetime | None = None
) -> float | None:
    """Read the wait a `Retry-After` header asks for.

    Args:
        response: The response to read.
        now: The current time, for HTTP-date values. Defaults to the current UTC
            time. Must be timezone-aware.

    Returns:
        The wait in seconds, or `None` when the header is absent, empty,
        negative or unparseable. A date in the past gives `0.0`.

    Raises:
        ValueError: If `now` is naive.
    """
    if now is not None and now.utcoffset() is None:
        raise ValueError(f"now must be timezone-aware, got {now!r}")
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if _SECONDS.fullmatch(value):
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    current = now if now is not None else datetime.now(UTC)
    try:
        return max(0.0, (when - current).total_seconds())
    except OverflowError:
        return None


def backoff_delay(
    retry: int, policy: RetryPolicy, rng: random.Random | None = None
) -> float:
    """Compute the delay before a retry, with equal jitter.

    The cap grows as `base_delay * 2 ** (retry - 1)` up to `max_delay`; the
    result is uniform in `[cap / 2, cap]`, so the floor grows exponentially and
    the value never exceeds the cap.

    Args:
        retry: Which retry this is, counting from 1.
        policy: The settings to read the delays from.
        rng: Random source. Defaults to the `random` module.

    Returns:
        The delay in seconds.

    Raises:
        ValueError: If `retry` is less than 1.
    """
    if retry < 1:
        raise ValueError(f"retry must be at least 1, got {retry}")
    try:
        grown = math.ldexp(policy.base_delay, retry - 1)
    except OverflowError:
        grown = math.inf
    cap = min(policy.max_delay, grown)
    uniform = rng.uniform if rng is not None else random.uniform
    return cap / 2 + uniform(0, cap / 2)


def _holdoff(policy: RetryPolicy, response: httpx.Response) -> float | None:
    """Find the wait a response asks for: the caller's reader, else the header."""
    if policy.holdoff_reader is not None:
        value = policy.holdoff_reader(response)
        if value is not None and math.isfinite(value):
            return max(0.0, value)
    return retry_after_seconds(response)


def _retry_delay(
    attempt: int,
    policy: RetryPolicy,
    response: httpx.Response | None,
    exception: Exception | None,
) -> float:
    """Decide how long to wait after a retryable outcome, or give up.

    Shared by the sync and async wrappers so both decide identically.

    Args:
        attempt: The attempt that just failed, counting from 1.
        policy: The retry settings.
        response: The retryable response, or `None` after a transport error.
        exception: The transport error, or `None` after a retryable status.

    Returns:
        Seconds to sleep before the next attempt.

    Raises:
        RetriesExhaustedError: If that was the last attempt.
        HoldoffTooLongError: If the server asked for more than `max_holdoff`.
    """
    if attempt >= policy.max_attempts:
        raise RetriesExhaustedError(attempt, response, exception) from exception
    if response is not None:
        holdoff = _holdoff(policy, response)
        if holdoff is not None:
            if holdoff > policy.max_holdoff:
                raise HoldoffTooLongError(
                    attempt, response, holdoff, policy.max_holdoff
                )
            return holdoff
    return backoff_delay(attempt, policy)


def request_with_retry(
    method: str,
    url: str,
    *,
    client: httpx.Client | None = None,
    policy: RetryPolicy | None = None,
    params: Mapping[str, str | int | float] | None = None,
    headers: Mapping[str, str] | None = None,
    content: bytes | None = None,
    json: object = None,
    timeout: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> httpx.Response:
    """Send a request, retrying transient failures.

    Connection errors, timeouts and HTTP 429, 500, 502, 503 and 504 are retried.
    Any other non-2xx status is raised on the attempt that received it.

    Args:
        method: The HTTP method.
        url: The URL to request.
        client: A client to send with. When `None` one is created and closed
            before returning; a passed client is left open.
        policy: Retry settings. Defaults to `RetryPolicy()`.
        params: Query parameters.
        headers: Request headers.
        content: A raw request body.
        json: A JSON-serialisable request body.
        timeout: Per-request timeout in seconds, when not `None`.
        sleep: Called with the seconds to wait between attempts.

    Returns:
        The first 2xx response.

    Raises:
        RetriesExhaustedError: If every attempt failed with a retryable outcome.
        HoldoffTooLongError: If the server asked for a wait over the policy cap.
        httpx.HTTPStatusError: For a non-retryable non-2xx status.
    """
    policy = policy if policy is not None else RetryPolicy()
    options: dict[str, object] = {}
    if timeout is not None:
        options["timeout"] = timeout
    owned = client is None
    active = (
        httpx.Client(timeout=DEFAULT_TIMEOUT, follow_redirects=True)
        if client is None
        else client
    )
    try:
        attempt = 1
        while True:
            try:
                response = active.request(
                    method,
                    url,
                    params=params,
                    headers=headers,
                    content=content,
                    json=json,
                    **options,  # type: ignore[arg-type]  # only `timeout` is ever set
                )
            except RETRY_EXCEPTIONS as exc:
                delay = _retry_delay(attempt, policy, None, exc)
            else:
                if response.status_code not in RETRY_STATUSES:
                    response.raise_for_status()
                    return response
                delay = _retry_delay(attempt, policy, response, None)
            sleep(delay)
            attempt += 1
    finally:
        if owned:
            active.close()


async def async_request_with_retry(
    method: str,
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
    policy: RetryPolicy | None = None,
    params: Mapping[str, str | int | float] | None = None,
    headers: Mapping[str, str] | None = None,
    content: bytes | None = None,
    json: object = None,
    timeout: float | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> httpx.Response:
    """Send a request asynchronously, retrying transient failures.

    Behaves exactly as `request_with_retry`, but waits with `await sleep(...)`,
    so the event loop keeps running while it waits.

    Args:
        method: The HTTP method.
        url: The URL to request.
        client: A client to send with. When `None` one is created and closed
            before returning; a passed client is left open.
        policy: Retry settings. Defaults to `RetryPolicy()`.
        params: Query parameters.
        headers: Request headers.
        content: A raw request body.
        json: A JSON-serialisable request body.
        timeout: Per-request timeout in seconds, when not `None`.
        sleep: Awaited with the seconds to wait between attempts.

    Returns:
        The first 2xx response.

    Raises:
        RetriesExhaustedError: If every attempt failed with a retryable outcome.
        HoldoffTooLongError: If the server asked for a wait over the policy cap.
        httpx.HTTPStatusError: For a non-retryable non-2xx status.
    """
    policy = policy if policy is not None else RetryPolicy()
    options: dict[str, object] = {}
    if timeout is not None:
        options["timeout"] = timeout
    owned = client is None
    active = (
        httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, follow_redirects=True)
        if client is None
        else client
    )
    try:
        attempt = 1
        while True:
            try:
                response = await active.request(
                    method,
                    url,
                    params=params,
                    headers=headers,
                    content=content,
                    json=json,
                    **options,  # type: ignore[arg-type]  # only `timeout` is ever set
                )
            except RETRY_EXCEPTIONS as exc:
                delay = _retry_delay(attempt, policy, None, exc)
            else:
                if response.status_code not in RETRY_STATUSES:
                    response.raise_for_status()
                    return response
                delay = _retry_delay(attempt, policy, response, None)
            await sleep(delay)
            attempt += 1
    finally:
        if owned:
            await active.aclose()


def main() -> None:
    """Showcase this module's functionality."""
    url = "https://api.example.test/data"
    method = "GET"
    policy = RetryPolicy(max_attempts=3, base_delay=0.5)
    answers = [503, 200]  # status codes served in turn
    seen: list[str] = []
    slept: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        status = answers[len(seen)]
        seen.append(f"{request.method} {request.url} -> {status}")
        return httpx.Response(status, headers={"Retry-After": "1"}, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))

    response = request_with_retry(
        method, url, client=client, policy=policy, sleep=slept.append
    )

    print(f"attempts: {seen}")
    print(f"slept: {slept} (the server's Retry-After, not the backoff)")
    print(f"result: {response.status_code} {response.json()}")

    # A 404 is not transient: raised on the first attempt, never retried.
    answers = [404]
    seen.clear()
    slept.clear()

    try:
        request_with_retry(
            method, url, client=client, policy=policy, sleep=slept.append
        )
    except httpx.HTTPStatusError as error:
        print(f"attempts: {seen}, slept: {slept}, raised: {error.response.status_code}")


if __name__ == "__main__":
    main()
