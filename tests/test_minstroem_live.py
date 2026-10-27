"""Live tests of the Min Strøm Consumer API; they need a real key.

Every test is marked `live`, so plain `pytest` deselects them. Run them with
`MINSTROEM_API_KEY` set and `-m live`; each adds a line to the end-of-run
summary.

The output is pasted into a chat, so nothing here prints the key, an address text
or an address id, under any traceback style (`--tb=short`, `auto`, `long` and
`native` are run offline; `line` and `no` print less). The one thing that defeats
it is `--showlocals` (`-l`), which prints every local variable, the addresses
frame included: do not add it.
A failed request is reported with its status, error code, reason and request id
and the address id replaced by `<address>`, and without the frames of the
package, whose arguments hold the request path. The address and the id the tests
pass around are objects whose `repr`, which pytest prints for the arguments of a
frame, is `<address>` and `<private>`.

The suite's own logic is run offline, against the in-repo mock, by
`tests/test_minstroem_spec.py` through `tests/minstroem_live_mock.py`.
"""

import functools
import math
import os
import re
import uuid
from collections.abc import Callable, Iterator
from types import TracebackType

import pandas as pd
import pytest

from energydata.minstroem import (
    API_KEY_ENV,
    Aggregation,
    MinStroemClient,
    MinStroemError,
    get_addresses,
    get_consumption,
    get_consumption_costs,
    get_forecast_prices,
    get_prices,
    get_prices_with_forecast,
    get_solar_export,
    get_solar_export_revenue,
)
from energydata.utils import RetryError

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        # The client strips its key, so a blank one is none.
        not os.environ.get(API_KEY_ENV, "").strip(),
        reason="set MINSTROEM_API_KEY to run the live Min Strøm tests",
    ),
]

type Report = Callable[[str], None]

TZ = "Europe/Copenhagen"

_ADDRESS_ID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


class _Private(str):
    """A string that shows as `<private>` where pytest prints a `repr` of it."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<private>"


class _Address(dict[str, object]):
    """One row of `get_addresses`, shown as `<address>` in a report of arguments."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<address>"


def _anonymous(text: str, *, keep: str | None = None) -> str:
    """`text` with every address id (a UUID) replaced by `<address>`.

    The API's request id is a UUID too, and the one fact support asks for, so
    `keep` (any case) is left as it is.
    """
    kept = (keep or "").lower()

    def replace(found: re.Match[str]) -> str:
        return (
            found.group(0) if kept and found.group(0).lower() == kept else "<address>"
        )

    return _ADDRESS_ID.sub(replace, text)


def _describe(error: MinStroemError | RetryError) -> str:
    """What a failed request says, without the address id.

    A `MinStroemError` keeps its status, error code, reason and request id; the
    message alone would name the address in the path it requested.
    """
    if isinstance(error, MinStroemError):
        facts = (
            f"status={error.status} error_code={error.error_code} "
            f"reason={error.reason!r} request_id={error.request_id}"
        )
        return _anonymous(
            f"a request failed: {type(error).__name__}: {error} [{facts}]",
            keep=error.request_id,
        )
    return _anonymous(f"a request failed: {type(error).__name__}: {error}")


def _test_frame(
    entry: TracebackType | None, test: Callable[..., object]
) -> TracebackType | None:
    """The traceback entry of `test` itself, with the frames it called cut off."""
    code = getattr(test, "__code__", None)
    while entry is not None and entry.tb_frame.f_code is not code:
        entry = entry.tb_next
    if entry is not None:
        entry.tb_next = None
    return entry


def _anonymous_failures[**P, R](test: Callable[P, R]) -> Callable[P, R]:
    """Fail a live test with the facts of a failed request but not the address id.

    A `MinStroemError` (or a `RetryError`) puts the address id into the path in
    its message, and the exception it was chained from puts it in the URL. The
    output is pasted back into a chat, so the test fails with `_describe` instead,
    with the chain suppressed and the traceback cut at the test's own frame: the
    frames below it, in the package, have the request path as an argument, which
    `--tb=long` prints. Skips, assertion failures and everything else pass through
    untouched.
    """

    @functools.wraps(test)
    def run(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return test(*args, **kwargs)
        except (MinStroemError, RetryError) as error:
            raise AssertionError(_describe(error)).with_traceback(
                _test_frame(error.__traceback__, test)
            ) from None

    return run


@pytest.fixture(scope="module")
def live_client() -> Iterator[MinStroemClient]:
    """One client for the whole module, so the rate limit is shared sensibly."""
    with MinStroemClient() as client:
        yield client


@pytest.fixture(scope="module")
def address(live_client: MinStroemClient) -> _Address:
    """The address under test: `MINSTROEM_ADDRESS_ID`, else one that forecasts.

    `MINSTROEM_ADDRESS_ID` is matched without regard to case or surrounding space,
    as the package accepts an explicit id in either case.
    """
    addresses = get_addresses(client=live_client)
    if addresses.empty:
        pytest.skip("the account has no addresses")
    wanted = os.environ.get("MINSTROEM_ADDRESS_ID", "").strip().lower()
    if wanted:
        rows = addresses[addresses["id"].str.lower() == wanted]
        if rows.empty:
            pytest.skip("MINSTROEM_ADDRESS_ID is not an address of this account")
    else:
        rows = addresses[addresses["forecast_prices"]]
        if rows.empty:
            rows = addresses
    return _Address({str(key): value for key, value in rows.iloc[0].to_dict().items()})


def _need(address: _Address, capability: str) -> _Private:
    """The address id, or skip the test naming the missing capability."""
    if not address[capability]:
        pytest.skip(f"the address lacks the {capability!r} capability")
    return _Private(str(address["id"]))


def _span(frame: pd.DataFrame) -> str:
    """First and last timestamp of a frame, or `-` when it is empty."""
    if frame.empty:
        return "-"
    return f"{frame.index[0].isoformat()} .. {frame.index[-1].isoformat()}"


def _day(offset: int) -> pd.Timestamp:
    """Local midnight `offset` calendar days from today.

    Calendar arithmetic, not elapsed time: seven days back across a DST change is
    local midnight, not 01:00, which a day interval would refuse.
    """
    today = pd.Timestamp.now(tz=TZ).tz_localize(None).normalize()
    return (today + pd.DateOffset(days=offset)).tz_localize(TZ)


def _hours_between(start: pd.Timestamp, end: pd.Timestamp) -> int:
    """Elapsed hours in `[start, end)`: 23 or 25 for a DST day."""
    return int((end - start) / pd.Timedelta(hours=1))


def _hours(flags: pd.Series, value: bool) -> pd.DatetimeIndex:
    """The hours whose flag is `value`; an `<NA>` hour is neither true nor false."""
    matches = flags.eq(value).fillna(False).to_numpy(dtype=bool)
    return pd.DatetimeIndex(flags.index[matches])


@_anonymous_failures
def test_live_addresses_shape(
    live_client: MinStroemClient, live_report: Report
) -> None:
    addresses = get_addresses(client=live_client)

    assert list(addresses.columns[-6:]) == [
        "consumption",
        "solar_export",
        "consumption_costs",
        "solar_export_revenue",
        "prices",
        "forecast_prices",
    ]
    assert all(addresses[c].dtype == bool for c in addresses.columns[-6:])
    live_report(
        f"addresses: {len(addresses)}, regions {sorted(addresses['region'].dropna())}, "
        f"forecast-capable {int(addresses['forecast_prices'].sum())}"
    )


@_anonymous_failures
def test_live_forecast_prices(
    live_client: MinStroemClient, address: _Address, live_report: Report
) -> None:
    address_id = _need(address, "forecast_prices")

    forecast = get_forecast_prices(address_id=address_id, client=live_client)

    assert list(forecast.columns) == ["total_price_per_kwh", "variable_charges_per_kwh"]
    assert str(forecast.index.dtype) == f"datetime64[ns, {TZ}]"
    assert forecast.index.name == "time"
    assert forecast.attrs["currency"] in ("DKK", "SEK", "NOK")
    assert forecast.attrs["vat_included"] is True
    assert forecast.attrs["fetched_at"].tzinfo is not None
    if not forecast.empty:
        assert forecast.index.is_monotonic_increasing
        assert (forecast.index.to_series().diff().dropna() == pd.Timedelta("1h")).all()
        # Floored in UTC: a Copenhagen time cannot be floored in the repeated hour
        # of the autumn change, where 02:30 is ambiguous.
        now = pd.Timestamp.now(tz="UTC")
        assert forecast.index[0] >= now.floor("h") - pd.Timedelta("1h")
        assert forecast["total_price_per_kwh"].dropna().map(math.isfinite).all()
        live_report(
            f"forecast: {len(forecast)} hours {_span(forecast)}, region "
            f"{address['region']}, {forecast.attrs['currency']}, first "
            f"{forecast['total_price_per_kwh'].iloc[0]:.3f}, last "
            f"{forecast['total_price_per_kwh'].iloc[-1]:.3f}"
        )
    else:
        live_report("forecast: EMPTY (the API returned no forecast rows)")


@_anonymous_failures
def test_live_prices_with_forecast_open_end(
    live_client: MinStroemClient, address: _Address, live_report: Report
) -> None:
    address_id = _need(address, "forecast_prices")
    _need(address, "prices")
    start = _day(0)

    combined = get_prices_with_forecast(
        start, address_id=address_id, client=live_client
    )

    assert list(combined.columns) == [
        "total_price_per_kwh",
        "variable_charges_per_kwh",
        "is_forecast",
    ]
    assert combined.index[0] == start
    assert (combined.index.to_series().diff().dropna() == pd.Timedelta("1h")).all()
    # An hour with neither a confirmed nor a forecast row is <NA>, which is
    # neither True nor False: count with the Series, never with Python truthiness.
    flags = combined["is_forecast"]
    confirmed = int(flags.eq(False).sum())
    forecasted = int(flags.eq(True).sum())
    neither = int(flags.isna().sum())
    assert confirmed + forecasted + neither == len(combined)
    confirmed_hours, forecast_hours = _hours(flags, False), _hours(flags, True)
    if len(confirmed_hours) and len(forecast_hours):
        assert confirmed_hours.max() < forecast_hours.min(), "confirmed after forecast"
    live_report(
        f"combined (today..): {len(combined)} hours {_span(combined)}, "
        f"confirmed {confirmed}, forecast {forecasted}, neither {neither}"
    )


@_anonymous_failures
def test_live_prices_with_forecast_closed_end(
    live_client: MinStroemClient, address: _Address, live_report: Report
) -> None:
    address_id = _need(address, "forecast_prices")
    _need(address, "prices")
    start = _day(-1)
    end = _day(2)

    combined = get_prices_with_forecast(
        start, end, address_id=address_id, client=live_client
    )

    assert combined.index[0] == start
    assert combined.index[-1] == end - pd.Timedelta(hours=1)
    live_report(
        f"combined (yesterday..+2d): {len(combined)} hours, "
        f"{int(combined['total_price_per_kwh'].isna().sum())} NaN"
    )


@_anonymous_failures
def test_live_combined_series_is_confirmed_where_confirmed_and_forecast_after(
    live_client: MinStroemClient, address: _Address, live_report: Report
) -> None:
    """Cross-check the combined series against the two endpoints it is made of.

    The four requests are not one snapshot: when the next day's prices are
    published the confirmed prices grow by a day and the forecast starts a day
    later. Confirmed prices do not change once published, so they are always
    compared; the forecast is compared value for value only when it is the same
    before and after the combined request, and otherwise only required to be made
    of hours one of the two snapshots had.
    """
    address_id = _need(address, "forecast_prices")
    _need(address, "prices")
    start = _day(-1)
    end = _day(2)

    forecast_before = get_forecast_prices(address_id=address_id, client=live_client)
    combined = get_prices_with_forecast(
        start, end, address_id=address_id, client=live_client
    )
    confirmed = get_prices(start, end, address_id=address_id, client=live_client)
    forecast_after = get_forecast_prices(address_id=address_id, client=live_client)

    total = "total_price_per_kwh"
    flags = combined["is_forecast"]
    shown = _hours(flags, False)
    predicted = _hours(flags, True)
    pd.testing.assert_series_equal(
        combined.loc[shown, total],
        confirmed.loc[shown, total],
        check_freq=False,
    )
    if len(shown) and len(predicted):
        assert shown.max() < predicted.min()
    if forecast_before.equals(forecast_after):
        assert predicted.isin(forecast_after.index).all(), (
            "forecast hours not in the forecast"
        )
        pd.testing.assert_series_equal(
            combined.loc[predicted, total],
            forecast_after.loc[predicted, total],
            check_freq=False,
        )
        compared = f"{len(predicted)} forecast hours match get_forecast_prices"
    else:
        known = forecast_before.index.union(forecast_after.index)
        assert predicted.isin(known).all(), "forecast hours in neither snapshot"
        compared = (
            f"the forecast moved during the run, so its {len(predicted)} hours "
            "in the combined series were not compared value for value"
        )
    live_report(
        f"combined cross-check: {len(shown)} confirmed hours match get_prices, "
        f"{compared}"
    )


@_anonymous_failures
def test_live_forecast_hours_are_whole_local_hours_and_unique(
    live_client: MinStroemClient, address: _Address, live_report: Report
) -> None:
    address_id = _need(address, "forecast_prices")

    forecast = get_forecast_prices(address_id=address_id, client=live_client)

    if forecast.empty:
        live_report("forecast hours: EMPTY")
        return
    index = pd.DatetimeIndex(forecast.index)
    assert index.is_unique
    assert index.is_monotonic_increasing
    assert (index.minute == 0).all()
    assert (index.second == 0).all()
    assert str(index.tz) == TZ
    assert (forecast.index.to_series().diff().dropna() == pd.Timedelta("1h")).all()
    live_report(
        f"forecast hours: {len(forecast)} whole local hours, "
        f"{int(forecast['total_price_per_kwh'].isna().sum())} without a total price"
    )


@_anonymous_failures
def test_live_prices_hour(
    live_client: MinStroemClient, address: _Address, live_report: Report
) -> None:
    address_id = _need(address, "prices")
    start = _day(-2)
    end = _day(0)

    prices = get_prices(start, end, address_id=address_id, client=live_client)

    assert len(prices) == _hours_between(start, end)
    assert prices.attrs["resolution"] == "hour"
    live_report(
        f"prices hour: {len(prices)} rows {_span(prices)}, "
        f"{int(prices['total_price_per_kwh'].isna().sum())} NaN, "
        f"mean {prices['total_price_per_kwh'].mean():.3f} {prices.attrs['currency']}"
    )


@_anonymous_failures
def test_live_prices_fifteen_minutes(
    live_client: MinStroemClient, address: _Address, live_report: Report
) -> None:
    address_id = _need(address, "prices")
    start = _day(-1)
    end = _day(0)

    prices = get_prices(
        start,
        end,
        resolution="fifteenMinutes",
        address_id=address_id,
        client=live_client,
    )

    assert len(prices) == 4 * _hours_between(start, end)
    live_report(
        f"prices 15 min: {len(prices)} rows, "
        f"{int(prices['total_price_per_kwh'].isna().sum())} NaN"
    )


@_anonymous_failures
def test_live_prices_future_to_probe(
    live_client: MinStroemClient, address: _Address, live_report: Report
) -> None:
    """Report whether `/prices` accepts a `to` three days ahead; passes either way."""
    address_id = _need(address, "prices")
    start = _day(0)
    end = _day(3)

    try:
        prices = get_prices(start, end, address_id=address_id, client=live_client)
    except MinStroemError as exc:
        live_report(
            f"PROBE /prices with to=+3 days: REFUSED, HTTP {exc.status} {exc.error_code}"
        )
        return
    rows = int(prices["total_price_per_kwh"].notna().sum())
    live_report(f"PROBE /prices with to=+3 days: accepted, {rows} priced rows")


@pytest.mark.parametrize("aggregation", ["hour", "day", "month", "year"])
@_anonymous_failures
def test_live_consumption(
    live_client: MinStroemClient,
    address: _Address,
    live_report: Report,
    aggregation: Aggregation,
) -> None:
    address_id = _need(address, "consumption")
    start, end = {
        "hour": (_day(-2), _day(0)),
        "day": (_day(-7), _day(0)),
        "month": (pd.Timestamp("2026-01-01", tz=TZ), pd.Timestamp("2026-04-01", tz=TZ)),
        "year": (pd.Timestamp("2025-01-01", tz=TZ), pd.Timestamp("2026-01-01", tz=TZ)),
    }[aggregation]

    usage = get_consumption(
        start,
        end,
        aggregation=aggregation,
        address_id=address_id,
        client=live_client,
    )

    assert list(usage.columns) == ["energy_kwh", "is_estimate"]
    live_report(
        f"consumption {aggregation}: {len(usage)} rows, "
        f"{int(usage['energy_kwh'].isna().sum())} missing, "
        f"sum {usage['energy_kwh'].sum():.1f} kWh"
    )


@_anonymous_failures
def test_live_solar_export(
    live_client: MinStroemClient, address: _Address, live_report: Report
) -> None:
    address_id = _need(address, "solar_export")

    export = get_solar_export(
        _day(-7),
        _day(0),
        aggregation="day",
        address_id=address_id,
        client=live_client,
    )

    assert list(export.columns) == ["energy_kwh", "is_estimate"]
    live_report(f"solar export day: {len(export)} rows")


@_anonymous_failures
def test_live_consumption_costs(
    live_client: MinStroemClient, address: _Address, live_report: Report
) -> None:
    address_id = _need(address, "consumption_costs")

    costs = get_consumption_costs(
        _day(-7),
        _day(0),
        aggregation="day",
        address_id=address_id,
        client=live_client,
    )

    assert costs.attrs["vat_included"] is True
    live_report(
        f"consumption costs day: {len(costs)} rows, total "
        f"{costs['total_cost'].sum():.2f} {costs.attrs['currency']}"
    )


@_anonymous_failures
def test_live_solar_export_revenue(
    live_client: MinStroemClient, address: _Address, live_report: Report
) -> None:
    address_id = _need(address, "solar_export_revenue")

    revenue = get_solar_export_revenue(
        _day(-7),
        _day(0),
        aggregation="day",
        address_id=address_id,
        client=live_client,
    )

    assert revenue.attrs["vat_included"] is False
    live_report(
        f"solar export revenue day: {len(revenue)} rows, net "
        f"{revenue['net_revenue'].sum():.2f} {revenue.attrs['currency']}"
    )


@_anonymous_failures
def test_live_hourly_consumption_over_two_windows(
    live_client: MinStroemClient, address: _Address, live_report: Report
) -> None:
    address_id = _need(address, "consumption")
    start = _day(-400)
    end = _day(0)

    usage = get_consumption(start, end, address_id=address_id, client=live_client)

    assert usage.index[0] == start
    assert len(usage) == _hours_between(start, end)
    live_report(
        f"consumption hour over 400 days (2 windows): {len(usage)} rows, "
        f"{int(usage['energy_kwh'].isna().sum())} missing"
    )


@_anonymous_failures
def test_live_listed_address_id_works_as_listed(
    live_client: MinStroemClient, address: _Address, live_report: Report
) -> None:
    """The id from `get_addresses` is accepted by the series endpoints as listed.

    The package sends an explicit id in its canonical lowercase form; if the API
    issues upper-case ids and compares them case-sensitively, this is the test
    that would show a 404.
    """
    listed = _need(address, "prices")

    frame = get_prices(
        _day(-1),
        _day(0),
        address_id=listed,
        client=live_client,
    )

    requested_in_lower_case = frame.attrs["address_id"] == listed.lower()
    assert requested_in_lower_case, "the request did not use the listed id, lower-cased"
    live_report(f"listed address id is lower-case: {listed == listed.lower()}")


@_anonymous_failures
def test_live_wrong_key_is_401_and_never_echoed(live_report: Report) -> None:
    wrong = _Private("not-the-key-" + uuid.uuid4().hex)
    real = _Private(os.environ[API_KEY_ENV].strip())

    with MinStroemClient(wrong) as client, pytest.raises(MinStroemError) as caught:
        get_addresses(client=client)

    error = caught.value
    assert error.status == 401
    shown = {
        "str": str(error),
        "repr": repr(error),
        "args": repr(error.args),
        "client repr": repr(client),
    }
    leaks = [where for where, text in shown.items() if wrong in text or real in text]
    assert not leaks, f"a key appears in: {', '.join(leaks)}"
    live_report(f"wrong key: HTTP {error.status} {error.error_code}")


@_anonymous_failures
def test_live_unknown_address_is_403_or_404(
    live_client: MinStroemClient, live_report: Report
) -> None:
    unknown = str(uuid.uuid4())

    with pytest.raises(MinStroemError) as caught:
        get_forecast_prices(address_id=unknown, client=live_client)

    assert caught.value.status in (403, 404)
    live_report(
        f"unknown address: HTTP {caught.value.status} {caught.value.error_code}"
    )


@_anonymous_failures
def test_live_address_default(
    live_client: MinStroemClient, live_report: Report
) -> None:
    addresses = get_addresses(client=live_client)

    if len(addresses) == 1 and addresses["forecast_prices"].iloc[0]:
        forecast = get_forecast_prices(client=live_client)
        live_report(f"address default: one address, forecast {len(forecast)} hours")
    elif len(addresses) == 1:
        live_report("address default: one address without forecast capability")
    else:
        # No address and several both refuse the default and say what to do. The
        # message for several lists every id and label, so it is never printed.
        wanted = "no addresses on this account" if addresses.empty else "address_id"
        with pytest.raises(ValueError) as caught:
            get_forecast_prices(client=live_client)
        explained = wanted in str(caught.value)
        assert explained, "the ValueError for the default address did not say why"
        live_report(
            f"address default: {len(addresses)} addresses, ValueError as designed"
        )
