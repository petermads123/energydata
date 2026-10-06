import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta, tzinfo
from typing import cast
from zoneinfo import ZoneInfo

import pytest

from energydata.utils import (
    async_fetch_chunked,
    date_windows,
    fetch_chunked,
    gather_chunked,
)

CPH = ZoneInfo("Europe/Copenhagen")
T0 = datetime(2026, 1, 1, tzinfo=UTC)
DAY = timedelta(days=1)


class _NoOffset(tzinfo):
    """A tzinfo that is present but cannot give an offset."""

    def utcoffset(self, dt: datetime | None) -> timedelta | None:  # noqa: ARG002 - signature fixed by tzinfo
        return None

    def dst(self, dt: datetime | None) -> timedelta | None:  # noqa: ARG002 - signature fixed by tzinfo
        return None

    def tzname(self, dt: datetime | None) -> str | None:  # noqa: ARG002 - signature fixed by tzinfo
        return None


def _elapsed(window: tuple[datetime, datetime]) -> timedelta:
    return window[1].astimezone(UTC) - window[0].astimezone(UTC)


def _assert_covers(
    windows: list[tuple[datetime, datetime]],
    start: datetime,
    end: datetime,
    span: timedelta,
) -> None:
    assert windows[0][0] == start
    assert windows[-1][1] is end
    for lo, hi in windows:
        assert timedelta(0) < _elapsed((lo, hi)) <= span
    for (_, hi), (lo, _) in zip(windows, windows[1:], strict=False):
        assert hi.astimezone(UTC) == lo.astimezone(UTC)
    assert sum((_elapsed(w) for w in windows), timedelta(0)) == _elapsed((start, end))


# --- date_windows: coverage (T12) -------------------------------------------


@pytest.mark.parametrize(
    ("length", "span", "count"),
    [
        (timedelta(days=3), DAY, 3),  # exact multiple: no empty tail
        (timedelta(hours=1), DAY, 1),  # span longer than the range
        (timedelta(microseconds=1), DAY, 1),  # the smallest range
        (timedelta(hours=25), DAY, 2),  # remainder window
        (timedelta(days=10), timedelta(days=3), 4),
        (timedelta(days=1), timedelta(days=1), 1),  # span equal to the range
        (timedelta(days=1, microseconds=1), DAY, 2),  # one tick over
    ],
)
def test_date_windows_cover_the_range_contiguously(
    length: timedelta, span: timedelta, count: int
) -> None:
    end = T0 + length

    windows = date_windows(T0, end, span)

    assert len(windows) == count
    _assert_covers(windows, T0, end, span)


def test_date_windows_last_window_is_the_remainder() -> None:
    end = T0 + timedelta(days=10)

    windows = date_windows(T0, end, timedelta(days=3))

    assert _elapsed(windows[-1]) == timedelta(days=1)


def test_date_windows_span_longer_than_range_returns_the_inputs_themselves() -> None:
    end = T0 + timedelta(hours=1)

    windows = date_windows(T0, end, DAY)

    assert windows == [(T0, end)]
    assert windows[0][0] is T0
    assert windows[0][1] is end


def test_date_windows_huge_span_does_not_overflow() -> None:
    end = T0 + DAY

    assert date_windows(T0, end, timedelta.max) == [(T0, end)]


def test_date_windows_is_idempotent() -> None:
    end = T0 + timedelta(days=7)

    assert date_windows(T0, end, DAY) == date_windows(T0, end, DAY)


def test_date_windows_period_of_years_in_year_long_windows() -> None:
    start = datetime(2024, 1, 1, tzinfo=CPH)
    end = datetime(2026, 7, 1, tzinfo=CPH)
    span = timedelta(days=365)

    windows = date_windows(start, end, span)

    assert len(windows) == 3
    _assert_covers(windows, start, end, span)


# --- date_windows: time zones -----------------------------------------------


def test_date_windows_step_elapsed_time_across_the_autumn_change() -> None:
    start = datetime(2026, 10, 24, tzinfo=CPH)
    end = datetime(2026, 10, 28, tzinfo=CPH)

    windows = date_windows(start, end, DAY)

    # Four calendar days are 97 elapsed hours here (the 25th has 25), so a fifth
    # one-hour window is needed.
    assert len(windows) == 5
    assert _elapsed(windows[-1]) == timedelta(hours=1)
    _assert_covers(windows, start, end, DAY)
    # 24 hours after local midnight on the 25th lands at 23:00 on the 25th.
    assert windows[1][1] == datetime(2026, 10, 25, 23, tzinfo=CPH)
    assert windows[1][1].utcoffset() == timedelta(hours=1)


def test_date_windows_step_elapsed_time_across_the_spring_change() -> None:
    start = datetime(2026, 3, 28, tzinfo=CPH)
    end = datetime(2026, 3, 31, tzinfo=CPH)

    windows = date_windows(start, end, DAY)

    _assert_covers(windows, start, end, DAY)
    # 24 hours after local midnight on the 29th (a 23-hour day) is 01:00 on the 30th.
    assert windows[1][1] == datetime(2026, 3, 30, 1, tzinfo=CPH)


def test_date_windows_mixed_zones_keep_start_zone_and_end_itself() -> None:
    start = datetime(2026, 1, 1, tzinfo=CPH)
    end = datetime(2026, 1, 3, 12, tzinfo=UTC)

    windows = date_windows(start, end, DAY)

    assert len(windows) == 3
    assert windows[0][1].tzinfo is CPH
    assert windows[1][1].tzinfo is CPH
    assert windows[-1][1] is end
    _assert_covers(windows, start, end, DAY)


def test_date_windows_accept_a_forward_range_inside_the_repeated_hour() -> None:
    # 02:45 (first pass, +02:00) to 02:15 (second pass, +01:00): 30 minutes elapsed.
    start = datetime(2026, 10, 25, 2, 45, tzinfo=CPH, fold=0)
    end = datetime(2026, 10, 25, 2, 15, tzinfo=CPH, fold=1)

    windows = date_windows(start, end, timedelta(hours=1))

    assert windows == [(start, end)]
    assert _elapsed(windows[0]) == timedelta(minutes=30)


def test_date_windows_reject_a_backward_range_inside_the_repeated_hour() -> None:
    # Wall-clock order says forward; elapsed order says backward by 30 minutes.
    start = datetime(2026, 10, 25, 2, 15, tzinfo=CPH, fold=1)
    end = datetime(2026, 10, 25, 2, 45, tzinfo=CPH, fold=0)

    with pytest.raises(ValueError, match="start must be before end"):
        date_windows(start, end, timedelta(hours=1))


def test_date_windows_equal_instants_in_different_zones_are_rejected() -> None:
    start = datetime(2026, 1, 1, 1, tzinfo=CPH)
    end = datetime(2026, 1, 1, tzinfo=UTC)

    with pytest.raises(ValueError, match="start must be before end"):
        date_windows(start, end, DAY)


# --- date_windows: invalid input --------------------------------------------


def test_date_windows_reject_a_naive_start_and_name_it() -> None:
    with pytest.raises(ValueError, match="start must be timezone-aware"):
        date_windows(datetime(2026, 1, 1), T0 + DAY, DAY)  # noqa: DTZ001 - naive on purpose


def test_date_windows_reject_a_naive_end_and_name_it() -> None:
    with pytest.raises(ValueError, match="end must be timezone-aware"):
        date_windows(T0, datetime(2026, 1, 2), DAY)  # noqa: DTZ001 - naive on purpose


def test_date_windows_reject_a_tzinfo_without_an_offset() -> None:
    odd = datetime(2026, 1, 1, tzinfo=_NoOffset())

    with pytest.raises(ValueError, match="timezone-aware"):
        date_windows(odd, T0 + DAY, DAY)


def test_date_windows_reject_equal_start_and_end() -> None:
    with pytest.raises(ValueError, match="start must be before end"):
        date_windows(T0, T0, DAY)


def test_date_windows_reject_start_after_end() -> None:
    with pytest.raises(ValueError, match="start must be before end"):
        date_windows(T0 + DAY, T0, DAY)


@pytest.mark.parametrize(
    "span", [timedelta(0), timedelta(seconds=-1), -timedelta(microseconds=1)]
)
def test_date_windows_reject_a_non_positive_span_and_name_it(span: timedelta) -> None:
    with pytest.raises(ValueError, match="span must be positive") as info:
        date_windows(T0, T0 + DAY, span)
    assert repr(span) in str(info.value)


def test_date_windows_do_not_mutate_their_arguments() -> None:
    start, end, span = T0, T0 + timedelta(days=3), DAY

    date_windows(start, end, span)

    assert (start, end, span) == (T0, T0 + timedelta(days=3), DAY)


# --- fetch_chunked / async_fetch_chunked (T13) ------------------------------


def _run_sync[T](
    fetch: Callable[[datetime, datetime], T],
    start: datetime,
    end: datetime,
    span: timedelta,
) -> list[T]:
    return fetch_chunked(fetch, start, end, span)


def _run_async[T](
    fetch: Callable[[datetime, datetime], T],
    start: datetime,
    end: datetime,
    span: timedelta,
) -> list[T]:
    async def wrapped(lo: datetime, hi: datetime) -> T:
        return fetch(lo, hi)

    return asyncio.run(async_fetch_chunked(wrapped, start, end, span))


RUNNERS = pytest.mark.parametrize("run", [_run_sync, _run_async], ids=["sync", "async"])


@RUNNERS
def test_fetch_chunked_returns_results_in_window_order(
    run: Callable[..., list],
) -> None:
    end = T0 + timedelta(days=3)

    results = run(lambda lo, hi: (lo, hi), T0, end, DAY)

    assert results == date_windows(T0, end, DAY)


@RUNNERS
def test_fetch_chunked_calls_in_order_once_per_window(run: Callable[..., list]) -> None:
    calls: list[tuple[datetime, datetime]] = []
    end = T0 + timedelta(days=5)

    def record(lo: datetime, hi: datetime) -> int:
        calls.append((lo, hi))
        return len(calls)

    results = run(record, T0, end, DAY)

    assert calls == date_windows(T0, end, DAY)
    assert results == [1, 2, 3, 4, 5]


@RUNNERS
def test_fetch_chunked_keeps_falsy_results(run: Callable[..., list]) -> None:
    results = run(lambda lo, hi: None, T0, T0 + timedelta(days=3), DAY)  # noqa: ARG005

    assert results == [None, None, None]


@RUNNERS
def test_fetch_chunked_single_window(run: Callable[..., list]) -> None:
    end = T0 + timedelta(hours=1)

    results = run(lambda lo, hi: (lo, hi), T0, end, DAY)

    assert results == [(T0, end)]


@RUNNERS
def test_fetch_chunked_stops_at_the_first_exception(run: Callable[..., list]) -> None:
    calls: list[datetime] = []

    def fail_on_second(lo: datetime, hi: datetime) -> int:  # noqa: ARG001
        calls.append(lo)
        if len(calls) == 2:
            raise KeyError("boom")
        return 0

    with pytest.raises(KeyError, match="boom"):
        run(fail_on_second, T0, T0 + timedelta(days=4), DAY)

    assert len(calls) == 2


@RUNNERS
def test_fetch_chunked_makes_no_call_when_validation_fails(
    run: Callable[..., list],
) -> None:
    calls: list[datetime] = []

    def record(lo: datetime, hi: datetime) -> None:  # noqa: ARG001
        calls.append(lo)

    with pytest.raises(ValueError, match="start must be before end"):
        run(record, T0, T0, DAY)

    assert calls == []


@RUNNERS
def test_fetch_chunked_makes_no_call_for_a_backward_range_in_the_repeated_hour(
    run: Callable[..., list],
) -> None:
    calls: list[datetime] = []

    def record(lo: datetime, hi: datetime) -> None:  # noqa: ARG001
        calls.append(lo)

    start = datetime(2026, 10, 25, 2, 15, tzinfo=CPH, fold=1)
    end = datetime(2026, 10, 25, 2, 45, tzinfo=CPH, fold=0)

    with pytest.raises(ValueError, match="start must be before end"):
        run(record, start, end, timedelta(hours=1))

    assert calls == []


def test_async_fetch_chunked_never_overlaps_windows() -> None:
    in_flight = 0
    peak = 0
    order: list[datetime] = []

    async def fetch(lo: datetime, hi: datetime) -> datetime:  # noqa: ARG001
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        order.append(lo)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        in_flight -= 1
        return lo

    results = asyncio.run(async_fetch_chunked(fetch, T0, T0 + timedelta(days=4), DAY))

    assert peak == 1
    assert (
        results
        == order
        == [w[0] for w in date_windows(T0, T0 + timedelta(days=4), DAY)]
    )


def test_async_fetch_chunked_validation_error_surfaces_on_await() -> None:
    async def fetch(lo: datetime, hi: datetime) -> None:  # noqa: ARG001
        raise AssertionError("must not be called")

    coroutine = async_fetch_chunked(fetch, T0, T0, DAY)

    with pytest.raises(ValueError, match="start must be before end"):
        asyncio.run(coroutine)


# --- gather_chunked (T4) ------------------------------------------------------

TIMEOUT = 10.0  # a hang fails the test instead of the suite


def _gather(
    fetch: Callable[[datetime, datetime], Awaitable[object]],
    start: datetime = T0,
    end: datetime = T0 + timedelta(days=3),
    span: timedelta = DAY,
    *,
    limit: int | None = None,
) -> list[object]:
    async def go() -> list[object]:
        return await asyncio.wait_for(
            gather_chunked(fetch, start, end, span, limit=limit), TIMEOUT
        )

    return asyncio.run(go())


def test_gather_chunked_returns_results_in_window_order_when_finishing_out_of_order() -> (
    None
):
    async def fetch(lo: datetime, hi: datetime) -> datetime:  # noqa: ARG001
        await asyncio.sleep((T0 + timedelta(days=3) - lo) / timedelta(days=1) * 0.01)
        return lo

    results = _gather(fetch)

    assert results == [w[0] for w in date_windows(T0, T0 + timedelta(days=3), DAY)]


def test_gather_chunked_passes_each_window_its_exact_bounds() -> None:
    seen: list[tuple[datetime, datetime]] = []

    async def fetch(lo: datetime, hi: datetime) -> None:
        seen.append((lo, hi))

    _gather(fetch, end=T0 + timedelta(days=2, hours=6))

    assert sorted(seen) == date_windows(T0, T0 + timedelta(days=2, hours=6), DAY)


def test_gather_chunked_single_window_gets_the_identical_bounds() -> None:
    end = T0 + timedelta(hours=5)
    seen: list[tuple[datetime, datetime]] = []

    async def fetch(lo: datetime, hi: datetime) -> str:
        seen.append((lo, hi))
        return "only"

    results = _gather(fetch, end=end, span=DAY)

    assert results == ["only"]
    assert seen[0][0] is T0
    assert seen[0][1] is end


def test_gather_chunked_keeps_falsy_results() -> None:
    async def fetch(lo: datetime, hi: datetime) -> int:  # noqa: ARG001
        return 0

    assert _gather(fetch) == [0, 0, 0]


@pytest.mark.parametrize(("limit", "peak"), [(1, 1), (2, 2), (5, 5), (None, 5)])
def test_gather_chunked_peak_in_flight_matches_the_limit(
    limit: int | None, peak: int
) -> None:
    in_flight = 0
    seen = 0

    async def fetch(lo: datetime, hi: datetime) -> None:  # noqa: ARG001
        nonlocal in_flight, seen
        in_flight += 1
        seen = max(seen, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1

    _gather(fetch, end=T0 + timedelta(days=5), limit=limit)

    assert seen == peak


def test_gather_chunked_a_limit_above_the_window_count_runs_all_at_once() -> None:
    in_flight = 0
    seen = 0

    async def fetch(lo: datetime, hi: datetime) -> None:  # noqa: ARG001
        nonlocal in_flight, seen
        in_flight += 1
        seen = max(seen, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1

    _gather(fetch, limit=100)

    assert seen == 3


def test_gather_chunked_raises_the_lowest_index_failure_not_the_first_in_time() -> None:
    late = KeyError("w0")

    async def fetch(lo: datetime, hi: datetime) -> None:  # noqa: ARG001
        if lo == T0:
            try:
                await asyncio.sleep(TIMEOUT)
            except asyncio.CancelledError:
                raise late from None
        if lo == T0 + DAY:
            raise ValueError("w1")

    with pytest.raises(KeyError) as caught:
        _gather(fetch)

    assert caught.value is late
    assert isinstance(caught.value.__cause__, ExceptionGroup)


def test_gather_chunked_never_raises_an_exception_group() -> None:
    async def fetch(lo: datetime, hi: datetime) -> None:  # noqa: ARG001
        raise ValueError("every window fails")

    with pytest.raises(ValueError, match="every window fails") as caught:
        _gather(fetch)

    assert not isinstance(caught.value, ExceptionGroup)


def test_gather_chunked_failure_cancels_the_windows_still_running() -> None:
    cancelled: list[datetime] = []

    async def fetch(lo: datetime, hi: datetime) -> None:  # noqa: ARG001
        if lo == T0:
            await asyncio.sleep(0.01)
            raise ValueError("w0")
        try:
            await asyncio.sleep(TIMEOUT)
        except asyncio.CancelledError:
            cancelled.append(lo)
            raise

    with pytest.raises(ValueError, match="w0"):
        _gather(fetch)

    assert sorted(cancelled) == [T0 + DAY, T0 + 2 * DAY]


def test_gather_chunked_failure_with_limit_one_never_starts_the_queued_windows() -> (
    None
):
    calls: list[datetime] = []

    async def fetch(lo: datetime, hi: datetime) -> None:  # noqa: ARG001
        calls.append(lo)
        raise KeyError("first")

    with pytest.raises(KeyError):
        _gather(fetch, end=T0 + timedelta(days=4), limit=1)

    assert calls == [T0]


@pytest.mark.parametrize("limit", [0, -1])
def test_gather_chunked_rejects_a_limit_below_one_before_any_call(limit: int) -> None:
    calls: list[datetime] = []

    async def fetch(lo: datetime, hi: datetime) -> None:  # noqa: ARG001
        calls.append(lo)

    with pytest.raises(ValueError, match="limit") as caught:
        _gather(fetch, limit=limit)

    assert str(limit) in str(caught.value)
    assert calls == []


@pytest.mark.parametrize(
    ("start", "end", "span", "match"),
    [
        (datetime(2026, 1, 1), T0 + DAY, DAY, "timezone"),  # noqa: DTZ001 - naive on purpose
        (T0, T0, DAY, "start must be before end"),
        (T0 + DAY, T0, DAY, "start must be before end"),
        (T0, T0 + DAY, timedelta(0), "span"),
        (T0, T0 + DAY, -DAY, "span"),
    ],
)
def test_gather_chunked_validation_errors_come_before_any_call(
    start: datetime, end: datetime, span: timedelta, match: str
) -> None:
    calls: list[datetime] = []

    async def fetch(lo: datetime, hi: datetime) -> None:  # noqa: ARG001
        calls.append(lo)

    with pytest.raises(ValueError, match=match):
        _gather(fetch, start, end, span)

    assert calls == []


def test_gather_chunked_works_across_a_dst_change() -> None:
    start = datetime(2026, 10, 24, tzinfo=CPH)
    end = datetime(2026, 10, 28, tzinfo=CPH)

    async def fetch(lo: datetime, hi: datetime) -> timedelta:
        return hi.astimezone(UTC) - lo.astimezone(UTC)

    results = _gather(fetch, start, end, DAY)

    assert sum(cast(list[timedelta], results), timedelta(0)) == (
        end.astimezone(UTC) - start.astimezone(UTC)
    )


def test_gather_chunked_leaves_no_task_running_after_a_failure() -> None:
    async def fetch(lo: datetime, hi: datetime) -> None:  # noqa: ARG001
        if lo == T0:
            raise ValueError("boom")
        await asyncio.sleep(TIMEOUT)

    async def go() -> int:
        with contextlib.suppress(ValueError):
            await gather_chunked(fetch, T0, T0 + timedelta(days=3), DAY)
        return len(asyncio.all_tasks()) - 1  # everything but this task

    assert asyncio.run(asyncio.wait_for(go(), TIMEOUT)) == 0


def test_gather_chunked_propagates_an_outer_cancellation() -> None:
    async def fetch(lo: datetime, hi: datetime) -> None:  # noqa: ARG001
        await asyncio.sleep(TIMEOUT)

    async def go() -> None:
        task = asyncio.ensure_future(gather_chunked(fetch, T0, T0 + 2 * DAY, DAY))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(asyncio.wait_for(go(), TIMEOUT))
