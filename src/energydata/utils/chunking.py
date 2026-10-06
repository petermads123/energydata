"""Split a period an API refuses as too long into windows, and fetch each one."""

from collections.abc import Awaitable, Callable
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


if __name__ == "__main__":
    main()
