import asyncio
import contextlib
import dataclasses
import math
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from energydata.utils import (
    DEFAULT_TIMEOUT,
    RETRY_EXCEPTIONS,
    RETRY_STATUSES,
    HoldoffTooLongError,
    RetriesExhaustedError,
    RetryError,
    RetryPolicy,
    async_request_with_retry,
    backoff_delay,
    request_with_retry,
    retry_after_seconds,
)

URL = "https://api.example.test/data"
NOW = datetime(2026, 1, 1, tzinfo=UTC)
FAST = RetryPolicy(max_attempts=5, base_delay=1.0, max_delay=60.0)


@pytest.fixture
def upper_jitter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make jitter deterministic: every delay is its cap."""
    monkeypatch.setattr(random, "uniform", lambda a, b: b)  # noqa: ARG005


# --- harness: run one scenario through the sync or the async wrapper ----------

type Step = int | tuple[int, dict[str, str]] | Exception | httpx.Response


@dataclass
class Outcome:
    """What one scenario did: result or error, sleeps and requests seen."""

    response: httpx.Response | None = None
    error: BaseException | None = None
    slept: list[float] = field(default_factory=list)
    requests: list[httpx.Request] = field(default_factory=list)


def _script(
    steps: list[Step], outcome: Outcome
) -> Callable[[httpx.Request], httpx.Response]:
    queue = list(steps)

    def handler(request: httpx.Request) -> httpx.Response:
        outcome.requests.append(request)
        step = queue.pop(0) if len(queue) > 1 else queue[0]  # the last step repeats
        if isinstance(step, Exception):
            raise step
        if isinstance(step, httpx.Response):
            return step
        if isinstance(step, int):
            return httpx.Response(step, json={"status": step})
        status, headers = step
        return httpx.Response(status, headers=headers, json={"status": status})

    return handler


def run(
    kind: str,
    steps: list[Step],
    policy: RetryPolicy | None = None,
    *,
    follow_redirects: bool = False,
    **kwargs: object,
) -> Outcome:
    """Run `steps` through the `kind` ("sync" or "async") wrapper on a mock transport."""
    outcome = Outcome()
    transport = httpx.MockTransport(_script(steps, outcome))
    if kind == "sync":
        client = httpx.Client(transport=transport, follow_redirects=follow_redirects)
        try:
            outcome.response = request_with_retry(
                "GET",
                URL,
                client=client,
                policy=policy,
                sleep=outcome.slept.append,
                **kwargs,  # type: ignore[arg-type]  # forwarded request options
            )
        except Exception as error:
            outcome.error = error
        finally:
            client.close()
        return outcome

    async def go() -> None:
        async def record(seconds: float) -> None:
            outcome.slept.append(seconds)

        async with httpx.AsyncClient(
            transport=transport, follow_redirects=follow_redirects
        ) as client:
            try:
                outcome.response = await async_request_with_retry(
                    "GET",
                    URL,
                    client=client,
                    policy=policy,
                    sleep=record,
                    **kwargs,  # type: ignore[arg-type]  # forwarded request options
                )
            except Exception as error:
                outcome.error = error

    asyncio.run(go())
    return outcome


KINDS = pytest.mark.parametrize("kind", ["sync", "async"])


def retry_after(value: str, status: int = 503) -> tuple[int, dict[str, str]]:
    return status, {"Retry-After": value}


# --- RETRY_STATUSES / RETRY_EXCEPTIONS (T1) ---------------------------------


def test_retry_constants_name_the_documented_sets() -> None:
    assert frozenset({429, 500, 502, 503, 504}) == RETRY_STATUSES
    assert set(RETRY_EXCEPTIONS) == {
        httpx.TimeoutException,
        httpx.NetworkError,
        httpx.RemoteProtocolError,
    }
    assert DEFAULT_TIMEOUT == 30.0


@KINDS
@pytest.mark.parametrize("status", sorted(RETRY_STATUSES))
@pytest.mark.parametrize("failures", [1, 3])
@pytest.mark.usefixtures("upper_jitter")
def test_each_retried_status_is_retried_then_the_success_is_returned(
    kind: str, status: int, failures: int
) -> None:
    outcome = run(kind, [*([status] * failures), 200], FAST)

    assert outcome.error is None
    assert outcome.response is not None
    assert outcome.response.status_code == 200
    assert len(outcome.requests) == failures + 1
    assert len(outcome.slept) == failures


@KINDS
@pytest.mark.parametrize(
    "exception",
    [
        httpx.ConnectTimeout("t"),
        httpx.ReadTimeout("t"),
        httpx.WriteTimeout("t"),
        httpx.PoolTimeout("t"),
        httpx.ConnectError("t"),
        httpx.ReadError("t"),
        httpx.WriteError("t"),
        httpx.RemoteProtocolError("t"),
    ],
    ids=lambda e: type(e).__name__,
)
@pytest.mark.usefixtures("upper_jitter")
def test_each_retried_exception_is_retried_then_the_success_is_returned(
    kind: str, exception: Exception
) -> None:
    outcome = run(kind, [exception, exception, 200], FAST)

    assert outcome.error is None
    assert outcome.response is not None
    assert outcome.response.status_code == 200
    assert len(outcome.requests) == 3


@KINDS
@pytest.mark.parametrize("status", [200, 201, 204, 299])
def test_a_2xx_status_is_returned_on_the_first_attempt(kind: str, status: int) -> None:
    outcome = run(kind, [status], FAST)

    assert outcome.error is None
    assert outcome.response is not None
    assert outcome.response.status_code == status
    assert len(outcome.requests) == 1
    assert outcome.slept == []


# --- non-retried outcomes (T2) ----------------------------------------------


@KINDS
@pytest.mark.parametrize(
    "status", [400, 401, 403, 404, 408, 409, 418, 425, 499, 501, 505, 511]
)
def test_other_error_statuses_raise_after_one_request(kind: str, status: int) -> None:
    outcome = run(kind, [status, 200], FAST)

    assert isinstance(outcome.error, httpx.HTTPStatusError)
    assert outcome.error.response.status_code == status
    assert len(outcome.requests) == 1
    assert outcome.slept == []


@KINDS
@pytest.mark.parametrize("status", [302, 304])
def test_a_redirect_not_followed_by_the_client_raises_after_one_request(
    kind: str, status: int
) -> None:
    outcome = run(kind, [(status, {"Location": "/elsewhere"}), 200], FAST)

    assert isinstance(outcome.error, httpx.HTTPStatusError)
    assert len(outcome.requests) == 1


@KINDS
@pytest.mark.parametrize(
    "exception",
    [
        httpx.ProxyError("t"),
        httpx.UnsupportedProtocol("t"),
        httpx.LocalProtocolError("t"),
        httpx.DecodingError("t"),
        httpx.TooManyRedirects("t"),
        ValueError("t"),
        KeyError("t"),
    ],
    ids=lambda e: type(e).__name__,
)
def test_an_exception_outside_the_retry_set_propagates_after_one_request(
    kind: str, exception: Exception
) -> None:
    outcome = run(kind, [exception, 200], FAST)

    assert outcome.error is exception
    assert len(outcome.requests) == 1
    assert outcome.slept == []


# --- backoff_delay (T3) ------------------------------------------------------


class _Edge:
    """A random source whose `uniform` returns one end of its range."""

    def __init__(self, high: bool) -> None:
        self.high = high

    def uniform(self, a: float, b: float) -> float:
        return b if self.high else a


def _edge(high: bool) -> random.Random:
    return _Edge(high)  # type: ignore[return-value]  # duck-typed stand-in for Random


def test_backoff_delay_floor_doubles_and_saturates_at_max_delay() -> None:
    policy = RetryPolicy(base_delay=1.0, max_delay=8.0)

    floors = [backoff_delay(n, policy, _edge(high=False)) for n in range(1, 8)]
    ceilings = [backoff_delay(n, policy, _edge(high=True)) for n in range(1, 8)]

    assert floors == [0.5, 1.0, 2.0, 4.0, 4.0, 4.0, 4.0]
    assert ceilings == [1.0, 2.0, 4.0, 8.0, 8.0, 8.0, 8.0]


@pytest.mark.parametrize("retry", range(1, 12))
def test_backoff_delay_stays_within_half_the_cap_and_the_cap(retry: int) -> None:
    policy = RetryPolicy()
    cap = min(policy.max_delay, policy.base_delay * 2 ** (retry - 1))
    rng = random.Random(retry)

    values = [backoff_delay(retry, policy, rng) for _ in range(200)]

    assert all(cap / 2 <= value <= cap for value in values)
    assert max(values) > min(values)  # jitter actually varies


@pytest.mark.parametrize("retry", [1_025, 10**6, 10**30])
def test_backoff_delay_does_not_overflow_for_huge_retry_numbers(retry: int) -> None:
    value = backoff_delay(retry, RetryPolicy(max_delay=60.0), random.Random(0))

    assert 30.0 <= value <= 60.0


@pytest.mark.parametrize("retry", [1, 10**6])
def test_backoff_delay_is_zero_for_a_zero_base(retry: int) -> None:
    assert backoff_delay(retry, RetryPolicy(base_delay=0, max_delay=0)) == 0.0


def test_backoff_delay_zero_base_with_a_larger_max_stays_zero() -> None:
    assert backoff_delay(50, RetryPolicy(base_delay=0, max_delay=10)) == 0.0


@pytest.mark.parametrize("retry", [0, -1, -(10**6)])
def test_backoff_delay_rejects_a_retry_below_one_and_names_it(retry: int) -> None:
    with pytest.raises(ValueError, match="retry must be at least 1") as info:
        backoff_delay(retry, RetryPolicy())

    assert str(retry) in str(info.value)


def test_backoff_delay_with_a_seeded_rng_is_deterministic() -> None:
    first = [backoff_delay(n, FAST, random.Random(42)) for n in range(1, 6)]
    second = [backoff_delay(n, FAST, random.Random(42)) for n in range(1, 6)]

    assert first == second


def test_backoff_delay_defaults_to_the_random_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(random, "uniform", lambda a, b: a)  # noqa: ARG005

    assert backoff_delay(3, FAST) == 2.0


@KINDS
@pytest.mark.usefixtures("upper_jitter")
def test_the_wrappers_sleep_the_backoff_delays(kind: str) -> None:
    policy = RetryPolicy(max_attempts=5, base_delay=1.0, max_delay=5.0)

    outcome = run(kind, [503, 503, 503, 503, 200], policy)

    assert outcome.slept == [1.0, 2.0, 4.0, 5.0]


@KINDS
@pytest.mark.usefixtures("upper_jitter")
def test_transport_errors_are_slept_with_backoff_not_a_holdoff_reader(
    kind: str,
) -> None:
    calls: list[httpx.Response] = []

    def reader(response: httpx.Response) -> float | None:
        calls.append(response)
        return 99.0

    policy = RetryPolicy(base_delay=1.0, holdoff_reader=reader)

    outcome = run(kind, [httpx.ReadTimeout("t"), 200], policy)

    assert outcome.slept == [1.0]
    assert calls == []


# --- RetryPolicy (T4) --------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "named"),
    [
        ({"max_attempts": 0}, "0"),
        ({"max_attempts": -3}, "-3"),
        ({"base_delay": -0.1}, "-0.1"),
        ({"base_delay": math.nan}, "nan"),
        ({"base_delay": math.inf, "max_delay": math.inf}, "inf"),
        ({"base_delay": 1.0, "max_delay": 0.5}, "0.5"),
        ({"max_delay": math.nan}, "nan"),
        ({"max_delay": math.inf}, "inf"),
        ({"max_holdoff": -1.0}, "-1.0"),
        ({"max_holdoff": math.nan}, "nan"),
        ({"max_holdoff": math.inf}, "inf"),
    ],
)
def test_retry_policy_rejects_invalid_settings_and_names_the_value(
    kwargs: dict[str, float], named: str
) -> None:
    with pytest.raises(ValueError) as info:
        RetryPolicy(**kwargs)  # type: ignore[arg-type]  # deliberately invalid

    assert named in str(info.value)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_attempts": 1},
        {"base_delay": 0},
        {"base_delay": -0.0},
        {"base_delay": 2.0, "max_delay": 2.0},
        {"max_holdoff": 0},
        {"base_delay": 0, "max_delay": 0, "max_holdoff": 0, "max_attempts": 1},
    ],
)
def test_retry_policy_accepts_the_boundary_values(kwargs: dict[str, float]) -> None:
    RetryPolicy(**kwargs)  # type: ignore[arg-type]  # numeric kwargs from parametrize


def test_retry_policy_defaults_match_the_documented_ones() -> None:
    policy = RetryPolicy()

    assert (policy.max_attempts, policy.base_delay, policy.max_delay) == (5, 1.0, 60.0)
    assert policy.max_holdoff == 300.0
    assert policy.holdoff_reader is None


def test_retry_policy_is_frozen() -> None:
    policy = RetryPolicy()

    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.max_attempts = 2  # type: ignore[misc]  # proving it is frozen


# --- retry_after_seconds (T5) ------------------------------------------------


def _header(value: str | None) -> httpx.Response:
    headers = {} if value is None else {"Retry-After": value}
    return httpx.Response(503, headers=httpx.Headers(headers, encoding="utf-8"))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0", 0.0),
        ("5", 5.0),
        ("120", 120.0),
        ("1.5", 1.5),
        ("  7\t", 7.0),
        ("0.05", 0.05),
    ],
)
def test_retry_after_seconds_reads_numbers_of_seconds(
    value: str, expected: float
) -> None:
    assert retry_after_seconds(_header(value)) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "   ",
        "-5",
        "nan",
        "inf",
        "1e3",
        ".5",
        "1.",
        "5 seconds",
        "５",  # a fullwidth digit is not a seconds value
        "soon",
        "Mon, 32 Foo 2026 99:99:99 GMT",
        "Thu, 01 Jan 2026 00:00:00 +9999",
    ],
)
def test_retry_after_seconds_returns_none_for_anything_unusable(
    value: str | None,
) -> None:
    assert retry_after_seconds(_header(value), now=NOW) is None


@pytest.mark.parametrize(
    "value",
    [
        "Thu, 01 Jan 2026 00:00:37 GMT",
        "Thursday, 01-Jan-26 00:00:37 GMT",
        "Thu Jan  1 00:00:37 2026",
    ],
)
def test_retry_after_seconds_reads_all_three_http_date_forms(value: str) -> None:
    assert retry_after_seconds(_header(value), now=NOW) == 37.0


def test_retry_after_seconds_reads_a_minus_zero_zone_as_utc() -> None:
    value = "Thu, 01 Jan 2026 00:00:10 -0000"

    assert retry_after_seconds(_header(value), now=NOW) == 10.0


def test_retry_after_seconds_gives_zero_for_a_past_date() -> None:
    value = "Thu, 01 Jan 2015 00:00:00 GMT"

    assert retry_after_seconds(_header(value), now=NOW) == 0.0


def test_retry_after_seconds_accepts_a_now_in_another_zone() -> None:
    from zoneinfo import ZoneInfo

    local = NOW.astimezone(ZoneInfo("Europe/Copenhagen"))
    value = "Thu, 01 Jan 2026 00:02:00 GMT"

    assert retry_after_seconds(_header(value), now=local) == 120.0


def test_retry_after_seconds_defaults_now_to_the_current_time() -> None:
    value = format_datetime(datetime.now(UTC) + timedelta(seconds=120), usegmt=True)

    seconds = retry_after_seconds(_header(value))

    assert seconds is not None
    assert 110 < seconds <= 120


@pytest.mark.parametrize("value", [None, "5"])
def test_retry_after_seconds_rejects_a_naive_now_even_without_a_header(
    value: str | None,
) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        retry_after_seconds(_header(value), now=datetime(2026, 1, 1))  # noqa: DTZ001 - naive on purpose


def test_retry_after_seconds_reads_hundreds_of_digits_as_infinity() -> None:
    assert retry_after_seconds(_header("9" * 400)) == math.inf


def test_retry_after_seconds_gives_none_for_a_duplicated_header() -> None:
    # httpx joins repeated headers as "5, 5", which is neither seconds nor a date.
    response = httpx.Response(429, headers=[("Retry-After", "5"), ("Retry-After", "5")])

    assert retry_after_seconds(response) is None


# --- holdoffs in the wrappers (T5, T6) --------------------------------------


@KINDS
@pytest.mark.parametrize(
    ("value", "expected"), [("3", 3.0), ("0", 0.0), ("1.5", 1.5), ("120", 120.0)]
)
def test_the_retry_after_header_replaces_the_computed_delay(
    kind: str, value: str, expected: float
) -> None:
    outcome = run(kind, [retry_after(value), 200], FAST)

    assert outcome.slept == [expected]
    assert outcome.response is not None


@KINDS
def test_a_holdoff_is_not_capped_by_max_delay(kind: str) -> None:
    policy = RetryPolicy(max_delay=60.0, max_holdoff=300.0)

    outcome = run(kind, [retry_after("120"), 200], policy)

    assert outcome.slept == [120.0]


@KINDS
def test_a_retry_after_date_is_waited_for_with_the_current_clock(kind: str) -> None:
    when = format_datetime(datetime.now(UTC) + timedelta(seconds=120), usegmt=True)

    outcome = run(kind, [retry_after(when), 200], FAST)

    assert len(outcome.slept) == 1
    assert 110 < outcome.slept[0] <= 120


@KINDS
def test_a_past_retry_after_date_sleeps_zero_not_the_backoff(kind: str) -> None:
    outcome = run(kind, [retry_after("Thu, 01 Jan 2015 00:00:00 GMT"), 200], FAST)

    assert outcome.slept == [0.0]


@KINDS
@pytest.mark.parametrize("value", ["nan", "inf", "1e3", "-5", "", "soon"])
@pytest.mark.usefixtures("upper_jitter")
def test_an_unusable_retry_after_falls_back_to_the_backoff(
    kind: str, value: str
) -> None:
    policy = RetryPolicy(base_delay=1.0)

    outcome = run(kind, [retry_after(value), 200], policy)

    assert outcome.slept == [1.0]


@KINDS
@pytest.mark.parametrize(
    ("returned", "expected"),
    [
        (2.0, 2.0),
        (0.0, 0.0),
        (-4.0, 0.0),
        (None, 7.0),
        (math.nan, 7.0),
        (math.inf, 7.0),
        (-math.inf, 7.0),
    ],
)
def test_a_holdoff_reader_overrides_the_header_and_bad_values_fall_back(
    kind: str, returned: float | None, expected: float
) -> None:
    policy = RetryPolicy(holdoff_reader=lambda response: returned)  # noqa: ARG005

    outcome = run(kind, [retry_after("7"), 200], policy)

    assert outcome.slept == [expected]


@KINDS
def test_a_holdoff_reader_can_read_the_body(kind: str) -> None:
    def from_body(response: httpx.Response) -> float | None:
        value = response.json().get("retry_in")
        return float(value) if value is not None else None

    body = httpx.Response(429, json={"retry_in": 4})
    policy = RetryPolicy(holdoff_reader=from_body)

    outcome = run(kind, [body, 200], policy)

    assert outcome.slept == [4.0]


@KINDS
def test_a_holdoff_over_the_cap_raises_without_sleeping_or_retrying(kind: str) -> None:
    policy = RetryPolicy(max_holdoff=300.0)

    outcome = run(kind, [retry_after("301", 429), 200], policy)

    error = outcome.error
    assert isinstance(error, HoldoffTooLongError)
    assert error.holdoff == 301.0
    assert error.attempts == 1
    assert error.response is not None
    assert error.response.status_code == 429
    assert "301" in str(error)
    assert "300" in str(error)
    assert outcome.slept == []
    assert len(outcome.requests) == 1


@KINDS
def test_a_holdoff_equal_to_the_cap_is_slept(kind: str) -> None:
    policy = RetryPolicy(max_holdoff=10.0)

    outcome = run(kind, [retry_after("10"), 200], policy)

    assert outcome.error is None
    assert outcome.slept == [10.0]


@KINDS
def test_a_holdoff_just_over_the_cap_raises(kind: str) -> None:
    policy = RetryPolicy(max_holdoff=10.0)

    outcome = run(kind, [retry_after("10.001"), 200], policy)

    assert isinstance(outcome.error, HoldoffTooLongError)
    assert outcome.error.holdoff == 10.001


@KINDS
def test_a_holdoff_of_hundreds_of_digits_raises_as_too_long(kind: str) -> None:
    outcome = run(kind, [retry_after("9" * 400), 200], FAST)

    assert isinstance(outcome.error, HoldoffTooLongError)
    assert outcome.error.holdoff == math.inf


@KINDS
def test_a_reader_holdoff_over_the_cap_raises(kind: str) -> None:
    policy = RetryPolicy(max_holdoff=5.0, holdoff_reader=lambda r: 6.0)  # noqa: ARG005

    outcome = run(kind, [503, 200], policy)

    assert isinstance(outcome.error, HoldoffTooLongError)
    assert outcome.error.holdoff == 6.0


@KINDS
def test_a_zero_cap_still_allows_a_zero_holdoff(kind: str) -> None:
    outcome = run(kind, [retry_after("0"), 200], RetryPolicy(max_holdoff=0))

    assert outcome.slept == [0.0]
    assert outcome.response is not None


@KINDS
def test_the_last_attempt_is_exhaustion_not_a_holdoff_error(kind: str) -> None:
    policy = RetryPolicy(max_attempts=2, max_holdoff=10.0)

    outcome = run(kind, [retry_after("1"), retry_after("99999")], policy)

    assert isinstance(outcome.error, RetriesExhaustedError)
    assert outcome.error.attempts == 2
    assert outcome.slept == [1.0]


@KINDS
def test_a_too_long_holdoff_before_the_last_attempt_raises_on_that_attempt(
    kind: str,
) -> None:
    policy = RetryPolicy(max_attempts=2, max_holdoff=10.0)

    outcome = run(kind, [retry_after("9999"), retry_after("9999")], policy)

    assert isinstance(outcome.error, HoldoffTooLongError)
    assert outcome.error.attempts == 1
    assert len(outcome.requests) == 1
    assert outcome.slept == []


# --- exhaustion (T7) ---------------------------------------------------------


@KINDS
@pytest.mark.usefixtures("upper_jitter")
def test_exhausted_by_status_sets_the_response_and_not_the_exception(kind: str) -> None:
    policy = RetryPolicy(max_attempts=3, base_delay=1.0)

    outcome = run(kind, [503], policy)

    error = outcome.error
    assert isinstance(error, RetriesExhaustedError)
    assert error.attempts == 3
    assert error.response is not None
    assert error.response.status_code == 503
    assert error.exception is None
    assert error.__cause__ is None
    assert len(outcome.requests) == 3
    assert len(outcome.slept) == 2  # no sleep after the final attempt
    assert "3" in str(error)
    assert "503" in str(error)


@KINDS
@pytest.mark.usefixtures("upper_jitter")
def test_exhausted_by_a_transport_error_sets_and_chains_the_exception(
    kind: str,
) -> None:
    boom = httpx.ConnectError("boom")
    policy = RetryPolicy(max_attempts=2)

    outcome = run(kind, [boom], policy)

    error = outcome.error
    assert isinstance(error, RetriesExhaustedError)
    assert error.attempts == 2
    assert error.response is None
    assert error.exception is boom
    assert error.__cause__ is boom
    assert "ConnectError" in str(error)
    assert "boom" in str(error)
    assert len(outcome.slept) == 1


@KINDS
def test_exhaustion_reports_the_last_outcome_when_the_kinds_alternate(
    kind: str,
) -> None:
    policy = RetryPolicy(max_attempts=2, base_delay=0)

    outcome = run(kind, [httpx.ReadTimeout("t"), 502], policy)

    assert isinstance(outcome.error, RetriesExhaustedError)
    assert outcome.error.response is not None
    assert outcome.error.response.status_code == 502
    assert outcome.error.exception is None


@KINDS
@pytest.mark.parametrize(
    "step", [503, httpx.ConnectError("boom")], ids=["status", "error"]
)
def test_a_single_attempt_policy_makes_one_request_and_never_sleeps(
    kind: str, step: Step
) -> None:
    outcome = run(kind, [step], RetryPolicy(max_attempts=1))

    assert isinstance(outcome.error, RetriesExhaustedError)
    assert outcome.error.attempts == 1
    assert len(outcome.requests) == 1
    assert outcome.slept == []


@KINDS
def test_a_single_attempt_policy_prefers_exhaustion_over_a_long_holdoff(
    kind: str,
) -> None:
    policy = RetryPolicy(max_attempts=1, max_holdoff=10.0)

    outcome = run(kind, [retry_after("9999")], policy)

    assert isinstance(outcome.error, RetriesExhaustedError)


@KINDS
def test_a_single_attempt_policy_returns_a_success(kind: str) -> None:
    outcome = run(kind, [200], RetryPolicy(max_attempts=1))

    assert outcome.response is not None


@KINDS
@pytest.mark.usefixtures("upper_jitter")
def test_success_on_the_last_allowed_attempt_is_returned(kind: str) -> None:
    outcome = run(kind, [503, 503, 200], RetryPolicy(max_attempts=3))

    assert outcome.error is None
    assert len(outcome.requests) == 3


def test_both_errors_share_a_base_and_are_not_httpx_errors() -> None:
    assert issubclass(RetriesExhaustedError, RetryError)
    assert issubclass(HoldoffTooLongError, RetryError)
    assert not issubclass(RetryError, httpx.HTTPError)


# --- request details (T9) ----------------------------------------------------


@KINDS
@pytest.mark.usefixtures("upper_jitter")
def test_every_attempt_resends_the_same_request(kind: str) -> None:
    outcome = run(
        kind,
        [503, 200],
        FAST,
        params={"q": 1, "s": "x y"},
        headers={"X-Key": "abc"},
        json={"k": "v"},
    )

    assert len(outcome.requests) == 2
    first, second = outcome.requests
    assert first.url == second.url
    assert first.url.params["q"] == "1"
    assert first.url.params["s"] == "x y"
    assert first.headers["X-Key"] == second.headers["X-Key"] == "abc"
    assert first.content == second.content == b'{"k":"v"}'


@KINDS
def test_raw_content_reaches_the_request(kind: str) -> None:
    outcome = run(kind, [200], FAST, content=b"raw-bytes")

    assert outcome.requests[0].content == b"raw-bytes"


@KINDS
def test_the_method_and_url_reach_the_request(kind: str) -> None:
    outcome = run(kind, [200], FAST)

    assert outcome.requests[0].method == "GET"
    assert str(outcome.requests[0].url) == URL


@KINDS
@pytest.mark.parametrize("timeout", [0.0, 2.5])
def test_a_per_request_timeout_is_forwarded_even_when_zero(
    kind: str, timeout: float
) -> None:
    outcome = run(kind, [200], FAST, timeout=timeout)

    assert outcome.requests[0].extensions["timeout"]["read"] == timeout


@KINDS
def test_without_a_timeout_the_clients_own_is_used(kind: str) -> None:
    outcome = run(kind, [200], FAST)

    assert outcome.requests[0].extensions["timeout"]["read"] == 5.0  # httpx default


@pytest.mark.usefixtures("upper_jitter")
def test_the_default_policy_is_used_when_none_is_given() -> None:
    outcome = run("sync", [503], None)

    assert isinstance(outcome.error, RetriesExhaustedError)
    assert outcome.error.attempts == RetryPolicy().max_attempts
    assert outcome.slept == [1.0, 2.0, 4.0, 8.0]


# --- clients the wrappers create and clients they are given (T9) -------------


@dataclass
class Spy:
    """Counts the closes of clients the wrappers create."""

    closed: int = 0
    requests: list[httpx.Request] = field(default_factory=list)


def _patch_created_clients(
    monkeypatch: pytest.MonkeyPatch, steps: list[Step]
) -> tuple[Spy, Outcome]:
    """Serve `steps` through the transports of clients the wrappers create."""
    outcome = Outcome()
    handler = _script(steps, outcome)
    spy = Spy()

    def sync_request(
        _self: httpx.HTTPTransport, request: httpx.Request
    ) -> httpx.Response:
        return handler(request)

    async def async_request(
        _self: httpx.AsyncHTTPTransport, request: httpx.Request
    ) -> httpx.Response:
        return handler(request)

    original_close = httpx.Client.close
    original_aclose = httpx.AsyncClient.aclose

    def close(self: httpx.Client) -> None:
        spy.closed += 1
        original_close(self)

    async def aclose(self: httpx.AsyncClient) -> None:
        spy.closed += 1
        await original_aclose(self)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", sync_request)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", async_request)
    monkeypatch.setattr(httpx.Client, "close", close)
    monkeypatch.setattr(httpx.AsyncClient, "aclose", aclose)
    return spy, outcome


def _call_owned(
    kind: str,
    sleep: Callable[[float], None],
    policy: RetryPolicy | None = None,
    **kwargs: object,
) -> httpx.Response:
    if kind == "sync":
        return request_with_retry("GET", URL, policy=policy, sleep=sleep, **kwargs)  # type: ignore[arg-type]  # forwarded options

    async def async_sleep(seconds: float) -> None:
        sleep(seconds)

    return asyncio.run(
        async_request_with_retry(
            "GET",
            URL,
            policy=policy,
            sleep=async_sleep,
            **kwargs,  # type: ignore[arg-type]  # forwarded options
        )
    )


@KINDS
def test_a_created_client_is_closed_after_success_and_the_body_is_readable(
    kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy, _ = _patch_created_clients(monkeypatch, [200])

    response = _call_owned(kind, lambda _s: None)

    assert spy.closed == 1
    assert response.json() == {"status": 200}


@KINDS
@pytest.mark.parametrize(
    ("steps", "expected"),
    [
        ([404], httpx.HTTPStatusError),
        ([503], RetriesExhaustedError),
        ([retry_after("9999")], HoldoffTooLongError),
        ([ValueError("x")], ValueError),
        ([httpx.ConnectError("x")], RetriesExhaustedError),
    ],
    ids=["status", "exhausted", "holdoff", "other", "transport"],
)
def test_a_created_client_is_closed_on_every_way_out(
    kind: str,
    monkeypatch: pytest.MonkeyPatch,
    steps: list[Step],
    expected: type[Exception],
) -> None:
    spy, outcome = _patch_created_clients(monkeypatch, steps)

    with pytest.raises(expected):
        _call_owned(kind, lambda _s: None, RetryPolicy(max_attempts=2, base_delay=0))

    assert spy.closed == 1
    assert len(outcome.requests) >= 1


@KINDS
def test_a_created_client_is_closed_when_the_holdoff_reader_raises(
    kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy, _ = _patch_created_clients(monkeypatch, [503])

    def reader(_response: httpx.Response) -> float | None:
        raise RuntimeError("reader broke")

    with pytest.raises(RuntimeError, match="reader broke"):
        _call_owned(kind, lambda _s: None, RetryPolicy(holdoff_reader=reader))

    assert spy.closed == 1


@KINDS
def test_a_created_client_is_closed_when_the_sleep_is_interrupted(
    kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy, _ = _patch_created_clients(monkeypatch, [503, 200])

    def interrupted(_seconds: float) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _call_owned(kind, interrupted)

    assert spy.closed == 1


def test_a_created_async_client_is_closed_when_cancelled_during_the_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy, _ = _patch_created_clients(monkeypatch, [503, 200])
    sleeping = asyncio.Event()

    async def sleep(_seconds: float) -> None:
        sleeping.set()
        await asyncio.sleep(3600)

    async def go() -> None:
        task = asyncio.create_task(async_request_with_retry("GET", URL, sleep=sleep))
        await sleeping.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())

    assert spy.closed == 1


@KINDS
def test_a_created_client_follows_redirects(
    kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy, outcome = _patch_created_clients(
        monkeypatch, [(302, {"Location": "/next"}), 200]
    )

    response = _call_owned(kind, lambda _s: None)

    assert response.status_code == 200
    assert [str(r.url) for r in outcome.requests] == [
        URL,
        "https://api.example.test/next",
    ]
    assert spy.closed == 1


@KINDS
def test_a_created_client_uses_the_default_timeout(
    kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, outcome = _patch_created_clients(monkeypatch, [200])

    _call_owned(kind, lambda _s: None)

    assert outcome.requests[0].extensions["timeout"]["read"] == DEFAULT_TIMEOUT


@KINDS
def test_a_created_client_receives_a_per_request_timeout(
    kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, outcome = _patch_created_clients(monkeypatch, [200])

    _call_owned(kind, lambda _s: None, timeout=1.5)

    assert outcome.requests[0].extensions["timeout"]["read"] == 1.5


@pytest.mark.parametrize(
    "steps",
    [[200], [404], [503], [retry_after("9999")]],
    ids=["success", "status", "exhausted", "holdoff"],
)
def test_a_passed_client_is_left_open_on_every_exit(steps: list[Step]) -> None:
    outcome = Outcome()
    client = httpx.Client(transport=httpx.MockTransport(_script(steps, outcome)))
    policy = RetryPolicy(max_attempts=2, base_delay=0)

    with contextlib.suppress(httpx.HTTPStatusError, RetryError):
        request_with_retry(
            "GET", URL, client=client, policy=policy, sleep=lambda _s: None
        )

    assert not client.is_closed
    client.close()


@pytest.mark.parametrize(
    "steps",
    [[200], [404], [503], [retry_after("9999")]],
    ids=["success", "status", "exhausted", "holdoff"],
)
def test_a_passed_async_client_is_left_open_on_every_exit(steps: list[Step]) -> None:
    outcome = Outcome()
    client = httpx.AsyncClient(transport=httpx.MockTransport(_script(steps, outcome)))
    policy = RetryPolicy(max_attempts=2, base_delay=0)

    async def noop(_seconds: float) -> None:
        return None

    async def go() -> bool:
        with contextlib.suppress(httpx.HTTPStatusError, RetryError):
            await async_request_with_retry(
                "GET", URL, client=client, policy=policy, sleep=noop
            )
        closed = client.is_closed
        await client.aclose()
        return closed

    assert asyncio.run(go()) is False


def test_the_no_network_guard_refuses_a_real_transport() -> None:
    with (
        httpx.Client() as client,
        pytest.raises(AssertionError, match="reach the network"),
    ):
        client.get(URL)


# --- sync/async parity and the event loop (T8) -------------------------------


def _signature(outcome: Outcome) -> tuple[object, ...]:
    error = outcome.error
    return (
        None if outcome.response is None else outcome.response.status_code,
        None if error is None else type(error).__name__,
        getattr(error, "attempts", None),
        len(outcome.requests),
        outcome.slept,
    )


@pytest.mark.parametrize(
    ("steps", "policy"),
    [
        ([503, 503, 200], FAST),
        ([429, 200], FAST),
        ([retry_after("2"), 200], FAST),
        ([retry_after("9999")], FAST),
        ([503], RetryPolicy(max_attempts=3)),
        ([httpx.ConnectError("x")], RetryPolicy(max_attempts=2)),
        ([404], FAST),
        ([ValueError("x")], FAST),
        ([200], RetryPolicy(max_attempts=1)),
        ([503], RetryPolicy(max_attempts=1)),
    ],
    ids=[
        "status-then-ok",
        "429",
        "holdoff",
        "holdoff-too-long",
        "exhausted",
        "transport-exhausted",
        "404",
        "other-exception",
        "single-ok",
        "single-fail",
    ],
)
@pytest.mark.usefixtures("upper_jitter")
def test_sync_and_async_wrappers_behave_identically(
    steps: list[Step], policy: RetryPolicy
) -> None:
    sync_outcome = run("sync", steps, policy)
    async_outcome = run("async", steps, policy)

    assert _signature(sync_outcome) == _signature(async_outcome)


def test_the_async_wrapper_does_not_block_the_event_loop_while_waiting() -> None:
    ticks: list[float] = []
    outcome = Outcome()
    steps: list[Step] = [retry_after("0.05"), 200]
    transport = httpx.MockTransport(_script(steps, outcome))

    async def ticker() -> None:
        while True:
            ticks.append(asyncio.get_running_loop().time())
            await asyncio.sleep(0.005)

    async def go() -> httpx.Response:
        task = asyncio.create_task(ticker())
        async with httpx.AsyncClient(transport=transport) as client:
            response = await async_request_with_retry("GET", URL, client=client)
        task.cancel()
        return response

    response = asyncio.run(go())

    assert response.status_code == 200
    assert len(ticks) >= 3  # the loop kept running during the 50 ms wait


def test_the_async_wrapper_awaits_the_sleep_it_is_given() -> None:
    awaited: list[float] = []

    async def sleep(seconds: float) -> None:
        await asyncio.sleep(0)
        awaited.append(seconds)

    outcome = Outcome()
    transport = httpx.MockTransport(_script([retry_after("4"), 200], outcome))

    async def go() -> None:
        async with httpx.AsyncClient(transport=transport) as client:
            await async_request_with_retry("GET", URL, client=client, sleep=sleep)

    asyncio.run(go())

    assert awaited == [4.0]


# --- main --------------------------------------------------------------------


def test_the_showcase_runs(capsys: pytest.CaptureFixture[str]) -> None:
    from energydata.utils.retry import main

    main()

    output = capsys.readouterr().out
    assert "slept: [1.0]" in output
    assert "404" in output
