"""A pytest plugin that runs the live Min Strøm suite against the in-repo mock.

`tests/test_minstroem_live.py` needs a real key and the real API, so a defect in
the live tests themselves would only show on the user's machine. This plugin lets
the offline suite run those tests anyway. Loaded with `-p minstroem_live_mock`,
a dummy `MINSTROEM_API_KEY` and a scenario name in `MINSTROEM_LIVE_MOCK`, it gives
every `MinStroemClient` the transport of a `MinStroemService` shaped by that
scenario, and keeps every real httpx transport refusing from the moment pytest is
configured, so the run cannot reach the network even though the live tests are
exempt from the main guard and their module-scoped fixtures are set up before any
per-test guard could run.

The scenarios are the shapes of data the real API makes ordinary and a test
written against a tidy mock would never meet: a missing confirmed hour, a
forecast that starts after a gap and has holes in it, no forecast at all, an
address without a capability, no address, several addresses, and a failing endpoint.

The data is laid out around the instant the run starts, and the package and the
live tests read the clock themselves (the confirmed-price cap, `fetched_at`, "today"
in the tests), so a run that straddles an hour or local midnight could meet data laid
out for the wrong day. `MINSTROEM_LIVE_NOW` pins the clock: an ISO 8601 instant with
an offset, around which the data is laid out and to which `pandas.Timestamp.now` is
fixed for the whole run. Left unset, the run uses the real clock.
"""

import os
from collections.abc import Callable, Collection
from datetime import UTC, datetime, time, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
import pytest
from conftest import (
    ADDRESS_ID,
    MinStroemService,
    block_real_transports,
    minstroem_address,
    minstroem_price_row,
)

from energydata.minstroem import API_KEY_ENV, MinStroemClient
from energydata.utils import DEFAULT_TIMEOUT, RetryPolicy

SCENARIO_ENV = "MINSTROEM_LIVE_MOCK"
NOW_ENV = "MINSTROEM_LIVE_NOW"
SECOND_ADDRESS_ID = "0e8d1c3a-7b49-4f0e-9d2a-3c6b5a4d7e91"
SCENARIOS = (
    "plain",
    "hole",
    "gappy_forecast",
    "empty_forecast",
    "no_prices",
    "no_forecast",
    "no_addresses",
    "two_addresses",
    "prices_not_found",
)

_COPENHAGEN = ZoneInfo("Europe/Copenhagen")
_PATCHES = pytest.MonkeyPatch()


def _hourly(
    first: datetime,
    stop: datetime,
    *,
    holes: Collection[datetime] = (),
    null_total: Collection[datetime] = (),
    null_variable: Collection[datetime] = (),
) -> list[dict[str, Any]]:
    """Hourly price rows over `[first, stop)`, minus `holes`, some with null fields."""
    rows: list[dict[str, Any]] = []
    start = first.astimezone(UTC)
    while start < stop.astimezone(UTC):
        if start not in holes:
            rows.append(
                minstroem_price_row(
                    start,
                    None if start in null_total else 0.5 + start.hour / 100,
                    None if start in null_variable else 0.25,
                )
            )
        start += timedelta(hours=1)
    return rows


def build_service(scenario: str, now: datetime, api_key: str) -> MinStroemService:
    """The mock API for `scenario`, with prices laid out relative to `now`.

    Confirmed prices run from three days back to tomorrow noon and the forecast
    from the current hour for 48 hours, so the two overlap and the forecast
    reaches past the confirmed prices; each scenario then bends one thing. A time
    such as "02:00 today" or "tomorrow noon" is that many elapsed hours after local
    midnight, so on a daylight-saving change day it is an hour off the wall clock.

    - `plain`: that shape, no holes.
    - `hole`: two confirmed hours missing (one today, one yesterday) and one
      confirmed row with a null total.
    - `gappy_forecast`: confirmed prices stop at 02:00 today, or at the current
      hour when that is earlier; the forecast starts two hours after the current
      hour, so the current hour and the next have neither source whatever the time
      of day. It also misses two hours and has a null total in its first row and a
      null variable charge in a later one.
    - `empty_forecast`: the forecast has no rows.
    - `no_prices`: the address lacks the `prices` capability.
    - `no_forecast`: the address lacks the `forecastPrices` capability.
    - `no_addresses`: the account has no address.
    - `two_addresses`: the account has a second address, which is the one that
      can forecast.
    - `prices_not_found`: `/prices` answers 404 with the address id in its reason.

    Args:
        scenario: One of `SCENARIOS`.
        now: The instant the data is laid out around.
        api_key: The bearer key the mock accepts.

    Returns:
        A configured `MinStroemService`.

    Raises:
        ValueError: If `scenario` is not one of `SCENARIOS`.
    """
    if scenario not in SCENARIOS:
        known = ", ".join(SCENARIOS)
        raise ValueError(f"scenario must be one of {known}, got {scenario!r}")
    today = now.astimezone(_COPENHAGEN).date()
    this_hour = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)

    def at(days: int, hours: int = 0) -> datetime:
        midnight = datetime.combine(
            today + timedelta(days=days), time(0), tzinfo=_COPENHAGEN
        )
        return midnight.astimezone(UTC) + timedelta(hours=hours)

    def ahead(hours: int) -> datetime:
        return this_hour + timedelta(hours=hours)

    confirmed = _hourly(at(-3), at(1, 12))
    forecast = _hourly(ahead(0), ahead(48))
    addresses = [minstroem_address()]
    if scenario == "hole":
        confirmed = _hourly(
            at(-3), at(1, 12), holes={at(0, 1), at(-1, 5)}, null_total={at(0, 3)}
        )
    elif scenario == "gappy_forecast":
        confirmed = _hourly(at(-3), min(at(0, 2), ahead(0)))
        forecast = _hourly(
            ahead(2),
            ahead(40),
            holes={ahead(5), ahead(6)},
            null_total={ahead(2)},
            null_variable={ahead(7)},
        )
    elif scenario == "empty_forecast":
        forecast = []
    elif scenario == "no_prices":
        addresses = [minstroem_address(prices=False)]
    elif scenario == "no_forecast":
        addresses = [minstroem_address(forecastPrices=False)]
    elif scenario == "no_addresses":
        addresses = []
    elif scenario == "two_addresses":
        addresses = [
            minstroem_address(label="Flat", forecastPrices=False),
            minstroem_address(SECOND_ADDRESS_ID, "Cabin"),
        ]
    service = MinStroemService(addresses, api_key=api_key)
    service.rows["prices"] = confirmed
    service.forecast = forecast
    if scenario == "prices_not_found":
        service.overrides["prices"] = lambda _request: httpx.Response(
            404,
            json={
                "error": True,
                "reason": f"No prices for address {ADDRESS_ID}.",
                "errorCode": "resource_not_found",
            },
        )
    return service


def _pinned_now(raw: str | None) -> datetime | None:
    """The instant `NOW_ENV` pins the clock to, or None when it is unset or empty.

    Raises:
        pytest.UsageError: If the value is not an ISO 8601 instant with an offset.
    """
    if not raw:
        return None
    try:
        pinned = datetime.fromisoformat(raw)
    except ValueError:
        pinned = None
    if pinned is None or pinned.tzinfo is None:
        raise pytest.UsageError(
            f"{NOW_ENV} must be an ISO 8601 instant with an offset, "
            f"like 2026-06-15T12:00:00+02:00, got {raw!r}"
        )
    return pinned.astimezone(UTC)


def _fixed_clock(pinned: datetime) -> Callable[[tzinfo | str | None], pd.Timestamp]:
    """A stand-in for `pandas.Timestamp.now` that always returns `pinned`."""

    def now(tz: tzinfo | str | None = None) -> pd.Timestamp:
        if tz is None:
            return pd.Timestamp(pinned.astimezone().replace(tzinfo=None))
        return pd.Timestamp(pinned).tz_convert(tz)

    return now


def pytest_configure() -> None:
    """Give every `MinStroemClient` the transport of the scenario's mock.

    Real httpx transports refuse from here, before any fixture of any scope is
    set up, until `pytest_unconfigure`. With `NOW_ENV` set, the data is laid out
    around that instant and `pandas.Timestamp.now` is fixed to it too.

    Raises:
        pytest.UsageError: If there is no usable key (the client strips it, so a
            blank one is none), the scenario is not one of `SCENARIOS`, or the
            instant is not an ISO 8601 time with an offset the data can be laid
            out around.
    """
    key = os.environ.get(API_KEY_ENV, "").strip()
    if not key:
        raise pytest.UsageError(f"minstroem_live_mock needs a dummy {API_KEY_ENV}")
    scenario = os.environ.get(SCENARIO_ENV) or "plain"
    if scenario not in SCENARIOS:
        known = ", ".join(SCENARIOS)
        raise pytest.UsageError(
            f"{SCENARIO_ENV} must be one of {known}, got {scenario!r}"
        )
    raw_now = os.environ.get(NOW_ENV)
    pinned = _pinned_now(raw_now)
    try:
        service = build_service(scenario, pinned or datetime.now(UTC), key)
    except OverflowError as error:
        raise pytest.UsageError(
            f"{NOW_ENV} is too close to the end of the calendar to lay data out "
            f"around, got {raw_now!r}"
        ) from error
    block_real_transports(_PATCHES)
    if pinned is not None:
        _PATCHES.setattr(pd.Timestamp, "now", staticmethod(_fixed_clock(pinned)))
    original = MinStroemClient.__init__

    def routed(
        self: MinStroemClient,
        api_key: str | None = None,
        *,
        policy: RetryPolicy | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_concurrency: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,  # noqa: ARG001 - the mock's replaces it
    ) -> None:
        original(
            self,
            api_key,
            policy=policy or RetryPolicy(base_delay=0.0, max_delay=0.0),
            timeout=timeout,
            max_concurrency=max_concurrency,
            transport=httpx.MockTransport(service),
        )

    _PATCHES.setattr(MinStroemClient, "__init__", routed)


def pytest_unconfigure() -> None:
    """Restore `MinStroemClient`, the clock and the real transports."""
    _PATCHES.undo()
