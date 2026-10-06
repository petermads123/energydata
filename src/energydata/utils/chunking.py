"""Split a period an API refuses as too long into windows, and fetch each one."""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo


def date_windows(
    start: datetime, end: datetime, span: timedelta
) -> list[tuple[datetime, datetime]]:
    """Split `[start, end)` into contiguous windows of at most `span`.

    Stepping is done in elapsed (UTC) time, so every window is at most `span`
    long even across a DST change. Boundaries are returned in `start`'s
    timezone, except the final end, which is `end` itself.

    Args:
        start: Start of the period, inclusive. Must be timezone-aware.
        end: End of the period, exclusive. Must be timezone-aware.
        span: The longest a single window may be. Must be positive.

    Returns:
        The windows in order; each ends where the next begins. The last may be
        shorter than `span`.

    Raises:
        ValueError: If `start` or `end` is naive, `start >= end`, or `span` is
            not positive.
    """
    for name, value in (("start", start), ("end", end)):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{name} must be timezone-aware, got {value!r}")
    if start.astimezone(UTC) >= end.astimezone(UTC):  # elapsed time, not wall clock
        raise ValueError(f"start must be before end, got start={start!r}, end={end!r}")
    if span <= timedelta(0):
        raise ValueError(f"span must be positive, got {span!r}")
    zone = start.tzinfo
    lo = start.astimezone(UTC)
    stop = end.astimezone(UTC)
    windows: list[tuple[datetime, datetime]] = []
    window_start = start
    while lo < stop:
        if span >= stop - lo:
            windows.append((window_start, end))
            break
        lo += span
        window_end = lo.astimezone(zone)
        windows.append((window_start, window_end))
        window_start = window_end
    return windows


def fetch_chunked[T](
    fetch: Callable[[datetime, datetime], T],
    start: datetime,
    end: datetime,
    span: timedelta,
) -> list[T]:
    """Call `fetch` once per window of `[start, end)`, in order.

    Args:
        fetch: Called with each window's start and end.
        start: Start of the period, inclusive. Must be timezone-aware.
        end: End of the period, exclusive. Must be timezone-aware.
        span: The longest a single window may be. Must be positive.

    Returns:
        The results, in window order. An exception from `fetch` propagates and
        no later window is fetched.

    Raises:
        ValueError: As for `date_windows`, before any call is made.
    """
    return [fetch(lo, hi) for lo, hi in date_windows(start, end, span)]


async def async_fetch_chunked[T](
    fetch: Callable[[datetime, datetime], Awaitable[T]],
    start: datetime,
    end: datetime,
    span: timedelta,
) -> list[T]:
    """Await `fetch` once per window of `[start, end)`, one window at a time.

    Windows run sequentially, never concurrently, so chunking a request does
    not multiply the load on the server.

    Args:
        fetch: Awaited with each window's start and end.
        start: Start of the period, inclusive. Must be timezone-aware.
        end: End of the period, exclusive. Must be timezone-aware.
        span: The longest a single window may be. Must be positive.

    Returns:
        The results, in window order. An exception from `fetch` propagates and
        no later window is fetched.

    Raises:
        ValueError: As for `date_windows`, before any call is made.
    """
    results: list[T] = []
    for lo, hi in date_windows(start, end, span):
        results.append(await fetch(lo, hi))
    return results


async def _gather_ordered[T](calls: Sequence[Callable[[], Awaitable[T]]]) -> list[T]:
    """Run `calls` concurrently and return their results in call order.

    The first failure cancels the calls still running. The exception raised is
    that of the lowest-index call that failed, chained from the task group's
    `ExceptionGroup`, so a caller never has to unpack a group.
    """
    results: dict[int, T] = {}
    failures: dict[int, Exception] = {}

    async def run_one(position: int, call: Callable[[], Awaitable[T]]) -> None:
        try:
            results[position] = await call()
        except Exception as exc:  # recorded so the lowest index can be re-raised
            failures[position] = exc
            raise

    try:
        async with asyncio.TaskGroup() as group:
            for position, call in enumerate(calls):
                group.create_task(run_one(position, call))
    except ExceptionGroup as group_error:
        raise failures[min(failures)] from group_error
    return [results[position] for position in range(len(calls))]


async def gather_chunked[T](
    fetch: Callable[[datetime, datetime], Awaitable[T]],
    start: datetime,
    end: datetime,
    span: timedelta,
    *,
    limit: int | None = None,
) -> list[T]:
    """Await `fetch` for every window of `[start, end)` concurrently.

    Args:
        fetch: Awaited with each window's start and end.
        start: Start of the period, inclusive. Must be timezone-aware.
        end: End of the period, exclusive. Must be timezone-aware.
        span: The longest a single window may be. Must be positive.
        limit: The most windows in flight at once. `None` means unbounded.

    Returns:
        The results, in window order however the windows finish. When windows
        fail, the rest are cancelled and the exception of the lowest-index
        failed window is raised, chained from the group.

    Raises:
        ValueError: If `limit` is below 1, or as for `date_windows`, before
            any call is made.
    """
    if limit is not None and limit < 1:
        raise ValueError(f"limit must be at least 1 or None, got {limit}")
    windows = date_windows(start, end, span)
    semaphore = asyncio.Semaphore(limit) if limit is not None else None

    failed = False

    async def guarded(lo: datetime, hi: datetime) -> T:
        nonlocal failed
        if failed:  # a queued window must not start once another has failed
            raise asyncio.CancelledError
        try:
            return await fetch(lo, hi)
        except Exception:
            failed = True
            raise

    def bind(lo: datetime, hi: datetime) -> Callable[[], Awaitable[T]]:
        async def call() -> T:
            if semaphore is None:
                return await guarded(lo, hi)
            async with semaphore:
                return await guarded(lo, hi)

        return call

    return await _gather_ordered([bind(lo, hi) for lo, hi in windows])


def main() -> None:
    """Showcase this module's functionality."""
    zone_name = "Europe/Copenhagen"  # any IANA time-zone name
    zone = ZoneInfo(zone_name)
    start = datetime(2024, 1, 1, tzinfo=zone)
    end = datetime(2026, 7, 1, tzinfo=zone)
    span = timedelta(days=365)

    windows = date_windows(start, end, span)

    print(f"{len(windows)} windows over {start.date()} to {end.date()}:")
    for lo, hi in windows:
        print(f"  {lo.isoformat()} -> {hi.isoformat()}")

    # Steps are elapsed time: across the October change the boundary is 23:00.
    start = datetime(2026, 10, 24, tzinfo=zone)
    end = datetime(2026, 10, 28, tzinfo=zone)
    span = timedelta(days=1)

    windows = date_windows(start, end, span)

    print(f"{len(windows)} windows across the DST change, {span} each:")
    for lo, hi in windows:
        print(f"  {lo.isoformat()} -> {hi.isoformat()}")

    # A fetch function returning one string per window.
    def describe(lo: datetime, hi: datetime) -> str:
        return f"{lo.date()}..{hi.date()}"

    results = fetch_chunked(describe, start, end, span)

    print(f"results: {results}")

    # The same windows fetched concurrently, two at a time, still in order.
    async def describe_later(lo: datetime, hi: datetime) -> str:
        await asyncio.sleep(0)
        return describe(lo, hi)

    limit = 2  # at most this many windows in flight; None means unbounded

    concurrent = asyncio.run(
        gather_chunked(describe_later, start, end, span, limit=limit)
    )

    print(f"concurrent results (limit {limit}): {concurrent}")


if __name__ == "__main__":
    main()
