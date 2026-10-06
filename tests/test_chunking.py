import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo

import pytest

from energydata.utils import async_fetch_chunked, date_windows, fetch_chunked

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
