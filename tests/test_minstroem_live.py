"""Live tests of the Min Strøm Consumer API; they need a real key.

Every test is marked `live`, so plain `pytest` deselects them. Run them with
`MINSTROEM_API_KEY` set and `-m live`; each adds a line to the end-of-run
summary. Nothing here prints the key, an address text or an address id.
"""

import os
import uuid
from collections.abc import Callable, Iterator

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

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.environ.get(API_KEY_ENV),
        reason="set MINSTROEM_API_KEY to run the live Min Strøm tests",
    ),
]

type Report = Callable[[str], None]

TZ = "Europe/Copenhagen"


@pytest.fixture(scope="module")
def live_client() -> Iterator[MinStroemClient]:
    """One client for the whole module, so the rate limit is shared sensibly."""
    with MinStroemClient() as client:
        yield client


@pytest.fixture(scope="module")
def address(live_client: MinStroemClient) -> dict[str, object]:
    """The address under test: `MINSTROEM_ADDRESS_ID`, else one that forecasts."""
    addresses = get_addresses(client=live_client)
    if addresses.empty:
        pytest.skip("the account has no addresses")
    wanted = os.environ.get("MINSTROEM_ADDRESS_ID")
    if wanted:
        rows = addresses[addresses["id"] == wanted]
        if rows.empty:
            pytest.skip("MINSTROEM_ADDRESS_ID is not an address of this account")
    else:
        rows = addresses[addresses["forecast_prices"]]
        if rows.empty:
            rows = addresses
    return {str(key): value for key, value in rows.iloc[0].to_dict().items()}


def _need(address: dict[str, object], capability: str) -> str:
    """The address id, or skip the test naming the missing capability."""
    if not address[capability]:
        pytest.skip(f"the address lacks the {capability!r} capability")
    return str(address["id"])


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


def test_live_forecast_prices(
    live_client: MinStroemClient, address: dict[str, object], live_report: Report
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
        now = pd.Timestamp.now(tz=TZ)
        assert forecast.index[0] >= now.floor("h") - pd.Timedelta("1h")
        assert forecast["total_price_per_kwh"].dropna().map(pd.notna).all()
        live_report(
            f"forecast: {len(forecast)} hours {_span(forecast)}, region "
            f"{address['region']}, {forecast.attrs['currency']}, first "
            f"{forecast['total_price_per_kwh'].iloc[0]:.3f}, last "
            f"{forecast['total_price_per_kwh'].iloc[-1]:.3f}"
        )
    else:
        live_report("forecast: EMPTY (the API returned no forecast rows)")


def test_live_prices_with_forecast_open_end(
    live_client: MinStroemClient, address: dict[str, object], live_report: Report
) -> None:
    address_id = _need(address, "forecast_prices")
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
    flags = combined["is_forecast"].tolist()
    seen_forecast = False
    for flag in flags:
        if flag is True:
            seen_forecast = True
        assert not (seen_forecast and flag is False), "confirmed after forecast"
    live_report(
        f"combined (today..): {len(combined)} hours {_span(combined)}, "
        f"confirmed {flags.count(False)}, forecast {flags.count(True)}, "
        f"neither {len(flags) - flags.count(False) - flags.count(True)}"
    )


def test_live_prices_with_forecast_closed_end(
    live_client: MinStroemClient, address: dict[str, object], live_report: Report
) -> None:
    address_id = _need(address, "forecast_prices")
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


def test_live_combined_series_is_confirmed_where_confirmed_and_forecast_after(
    live_client: MinStroemClient, address: dict[str, object], live_report: Report
) -> None:
    """Cross-check the combined series against the two endpoints it is made of."""
    address_id = _need(address, "forecast_prices")
    _need(address, "prices")
    start = _day(-1)
    end = _day(2)

    combined = get_prices_with_forecast(
        start, end, address_id=address_id, client=live_client
    )
    confirmed = get_prices(start, end, address_id=address_id, client=live_client)
    forecast = get_forecast_prices(address_id=address_id, client=live_client)

    total = "total_price_per_kwh"
    flags = combined["is_forecast"]
    shown = combined.index[flags == False]  # noqa: E712 - a boolean-array compare
    predicted = combined.index[flags == True]  # noqa: E712 - a boolean-array compare
    pd.testing.assert_series_equal(
        combined.loc[shown, total],
        confirmed.loc[shown, total],
        check_freq=False,
    )
    pd.testing.assert_series_equal(
        combined.loc[predicted, total],
        forecast.loc[predicted, total],
        check_freq=False,
    )
    if len(shown) and len(predicted):
        assert shown.max() < predicted.min()
    live_report(
        f"combined cross-check: {len(shown)} confirmed hours match get_prices, "
        f"{len(predicted)} forecast hours match get_forecast_prices"
    )


def test_live_forecast_hours_are_whole_local_hours_and_unique(
    live_client: MinStroemClient, address: dict[str, object], live_report: Report
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


def test_live_prices_hour(
    live_client: MinStroemClient, address: dict[str, object], live_report: Report
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


def test_live_prices_fifteen_minutes(
    live_client: MinStroemClient, address: dict[str, object], live_report: Report
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


def test_live_prices_future_to_probe(
    live_client: MinStroemClient, address: dict[str, object], live_report: Report
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
def test_live_consumption(
    live_client: MinStroemClient,
    address: dict[str, object],
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


def test_live_solar_export(
    live_client: MinStroemClient, address: dict[str, object], live_report: Report
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


def test_live_consumption_costs(
    live_client: MinStroemClient, address: dict[str, object], live_report: Report
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


def test_live_solar_export_revenue(
    live_client: MinStroemClient, address: dict[str, object], live_report: Report
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


def test_live_hourly_consumption_over_two_windows(
    live_client: MinStroemClient, address: dict[str, object], live_report: Report
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


def test_live_listed_address_id_works_as_listed(
    live_client: MinStroemClient, address: dict[str, object], live_report: Report
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

    assert frame.attrs["address_id"] == listed.lower()
    live_report(f"listed address id is lower-case: {listed == listed.lower()}")


def test_live_wrong_key_is_401_and_never_echoed(live_report: Report) -> None:
    wrong = "not-the-key-" + uuid.uuid4().hex
    real = os.environ[API_KEY_ENV]

    with MinStroemClient(wrong) as client, pytest.raises(MinStroemError) as caught:
        get_addresses(client=client)

    error = caught.value
    assert error.status == 401
    for text in (str(error), repr(error), repr(error.args), repr(client)):
        assert wrong not in text
        assert real not in text
    live_report(f"wrong key: HTTP {error.status} {error.error_code}")


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
        with pytest.raises(ValueError, match="address_id"):
            get_forecast_prices(client=live_client)
        live_report(
            f"address default: {len(addresses)} addresses, ValueError as designed"
        )
