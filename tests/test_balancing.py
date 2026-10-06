import asyncio
from collections.abc import Callable, Sequence
from datetime import timedelta
from typing import Any

import httpx
import numpy as np
import pandas as pd
import pytest
from conftest import MarketsService
from pandas.testing import assert_frame_equal

from energydata.energidataservice import (
    EnergiDataServiceClient,
    EnergiDataServiceError,
    _markets,
    get_afrr_energy_prices,
    get_imbalance_prices,
    get_mfrr_energy_prices,
)
from energydata.utils import BiddingZone, TimeLike

CPH = "Europe/Copenhagen"
DAY = "2026-09-15"
LAST = f"{DAY}T23:45"  # the fixture's last quarter, UTC 21:45
type Getter = Callable[..., pd.DataFrame]
ALL_GETTERS = [get_imbalance_prices, get_afrr_energy_prices, get_mfrr_energy_prices]
COLUMNS = [("DK1", "up"), ("DK1", "down"), ("DK2", "up"), ("DK2", "down")]


def _row(frame: pd.DataFrame, local: str) -> list[float]:
    values = frame.loc[pd.Timestamp(local, tz=CPH)].to_numpy(dtype=float)
    return [float(v) for v in values]


def _tz(frame: pd.DataFrame) -> str:
    assert isinstance(frame.index, pd.DatetimeIndex)
    return str(frame.index.tz)


def _quarter(utc: str, zone: str, **fields: float | None) -> dict[str, Any]:
    """An ImbalancePrice record with every aFRR field the market asks for."""
    base: dict[str, Any] = {
        "TimeUTC": utc,
        "PriceArea": zone,
        "ImbalancePriceEUR": 1.0,
        "aFRRUpMW": 1.0,
        "aFRRVWAUpEUR": 10.0,
        "aFRRDownMW": -1.0,
        "aFRRVWADownEUR": 5.0,
    }
    return {**base, **fields}


# --- values on the fixture day ------------------------------------------------


@pytest.mark.parametrize(
    ("getter", "expected"),
    [
        (get_imbalance_prices, [171.04, 171.04, 254.17, 254.17]),
        (get_afrr_energy_prices, [179.52, 86.09, np.nan, 74.79]),
        (get_mfrr_energy_prices, [254.17, 171.04, 254.17, 171.0]),
    ],
)
def test_prices_on_the_fixture_day_equal_the_records(
    markets_service: MarketsService, getter: Getter, expected: list[float]
) -> None:
    with markets_service.client() as client:
        prices = getter(DAY, client=client)

    assert prices.shape == (96, 4)
    assert prices.columns.tolist() == COLUMNS
    assert prices.index[0] == pd.Timestamp(DAY, tz=CPH)
    assert _tz(prices) == CPH
    assert prices.loc[pd.Timestamp(LAST, tz=CPH)].to_numpy() == pytest.approx(
        expected, nan_ok=True
    )
    assert set(prices.dtypes) == {np.dtype("float64")}


def test_imbalance_repeats_its_single_price_in_both_directions(
    markets_service: MarketsService,
) -> None:
    with markets_service.client() as client:
        prices = get_imbalance_prices(DAY, client=client)

    for zone in ("DK1", "DK2"):
        assert prices[(zone, "up")].equals(prices[(zone, "down")])


def test_mfrr_energy_price_is_published_even_without_a_requested_volume(
    markets_service: MarketsService,
) -> None:
    # DK1 23:45 has mFRRSAUpReqMW null (nothing requested) and still a price.
    with markets_service.client() as client:
        prices = get_mfrr_energy_prices(LAST, bidding_zones="DK1", client=client)

    assert _row(prices, LAST) == [254.17, 171.04]


def test_mfrr_energy_keeps_a_zero_price_as_published() -> None:
    records = [
        {
            "TimeUTC": "2026-09-15T21:45:00",
            "PriceArea": "DK1",
            "mFRRSAUpEUR": 0.0,
            "mFRRSADownEUR": 3.0,
        }
    ]
    service = MarketsService({"MfrrEnergyActivationMarket": records})

    with service.client() as client:
        prices = get_mfrr_energy_prices(LAST, bidding_zones="DK1", client=client)

    assert _row(prices, LAST) == [0.0, 3.0]


# --- aFRR: no activation is NaN -----------------------------------------------


def test_afrr_energy_is_nan_for_a_direction_with_zero_activated_volume(
    markets_service: MarketsService,
) -> None:
    # DK2 23:45: aFRRUpMW 0.0 with price 0.0, aFRRDownMW -15.54 with price 74.79.
    with markets_service.client() as client:
        prices = get_afrr_energy_prices(LAST, bidding_zones="DK2", client=client)

    up, down = _row(prices, LAST)
    assert np.isnan(up)
    assert down == 74.79


def test_afrr_energy_masks_each_direction_and_zone_on_its_own_volume() -> None:
    records = [
        _quarter("2026-09-15T21:45:00", "DK1", aFRRDownMW=0.0, aFRRVWADownEUR=0.0),
        _quarter("2026-09-15T21:45:00", "DK2", aFRRUpMW=0.0, aFRRVWAUpEUR=0.0),
    ]
    service = MarketsService({"ImbalancePrice": records})

    with service.client() as client:
        prices = get_afrr_energy_prices(LAST, client=client)

    dk1_up, dk1_down, dk2_up, dk2_down = _row(prices, LAST)
    assert dk1_up == 10.0
    assert np.isnan(dk1_down)
    assert np.isnan(dk2_up)
    assert dk2_down == 5.0


def test_afrr_energy_keeps_the_price_when_the_activated_volume_is_missing() -> None:
    records = [_quarter("2026-09-15T21:45:00", "DK1", aFRRUpMW=None)]
    service = MarketsService({"ImbalancePrice": records})

    with service.client() as client:
        prices = get_afrr_energy_prices(LAST, bidding_zones="DK1", client=client)

    assert _row(prices, LAST) == [10.0, 5.0]


def test_afrr_energy_keeps_a_zero_price_when_volume_was_activated() -> None:
    records = [_quarter("2026-09-15T21:45:00", "DK1", aFRRVWAUpEUR=0.0)]
    service = MarketsService({"ImbalancePrice": records})

    with service.client() as client:
        prices = get_afrr_energy_prices(LAST, bidding_zones="DK1", client=client)

    assert _row(prices, LAST) == [0.0, 5.0]


def test_afrr_energy_null_price_is_nan_whatever_the_volume() -> None:
    records = [_quarter("2026-09-15T21:45:00", "DK1", aFRRVWAUpEUR=None)]
    service = MarketsService({"ImbalancePrice": records})

    with service.client() as client:
        prices = get_afrr_energy_prices(LAST, bidding_zones="DK1", client=client)

    up, down = _row(prices, LAST)
    assert np.isnan(up)
    assert down == 5.0


def test_imbalance_price_is_never_masked_by_the_afrr_volumes(
    markets_service: MarketsService,
) -> None:
    with markets_service.client() as client:
        prices = get_imbalance_prices(LAST, bidding_zones="DK2", client=client)

    assert _row(prices, LAST) == [254.17, 254.17]


# --- nulls --------------------------------------------------------------------


def test_a_null_imbalance_price_is_nan_while_afrr_of_the_same_record_is_not(
    markets_service: MarketsService,
) -> None:
    latest = "2026-10-06T12:00"  # the probe's latest record: price null, DK1 only

    with markets_service.client() as client:
        imbalance = get_imbalance_prices(latest, client=client)
        afrr = get_afrr_energy_prices(latest, client=client)

    assert imbalance.shape == (1, 4)
    assert imbalance.isna().all().all()
    assert _row(afrr, latest)[:2] == [193.6, 73.47]
    assert afrr[[("DK2", "up"), ("DK2", "down")]].isna().all().all()


def test_a_null_mfrr_price_is_nan(markets_service: MarketsService) -> None:
    with markets_service.client() as client:
        prices = get_mfrr_energy_prices("2026-10-06T12:30", client=client)

    assert prices.shape == (1, 4)
    assert prices.isna().all().all()


@pytest.mark.parametrize("getter", ALL_GETTERS)
def test_a_zone_without_records_is_an_all_nan_column_pair(
    markets_service: MarketsService, getter: Getter
) -> None:
    for name, records in markets_service.data.items():
        markets_service.data[name] = [
            r for r in records if r.get("PriceArea", "DK1") == "DK1"
        ]

    with markets_service.client() as client:
        prices = getter(DAY, client=client)

    assert prices.shape == (96, 4)
    assert prices[("DK2", "up")].isna().all()
    assert prices[("DK2", "down")].isna().all()
    assert prices[("DK1", "up")].notna().any()


@pytest.mark.parametrize("getter", ALL_GETTERS)
def test_a_period_before_the_dataset_is_all_nan_and_still_requested(
    markets_service: MarketsService, getter: Getter
) -> None:
    with markets_service.client() as client:
        prices = getter("2025-03-03", client=client)

    assert prices.shape == (96, 4)
    assert prices.isna().all().all()
    assert len(markets_service.requests) == 1


@pytest.mark.parametrize("getter", ALL_GETTERS)
def test_nothing_is_filled_from_a_neighbour(
    markets_service: MarketsService, getter: Getter
) -> None:
    gap = "2026-09-15T21:30:00"
    for name, records in markets_service.data.items():
        markets_service.data[name] = [r for r in records if r.get("TimeUTC") != gap]

    with markets_service.client() as client:
        prices = getter(DAY, client=client)

    assert prices.loc[pd.Timestamp("2026-09-15T23:30", tz=CPH)].isna().all()


# --- request shape ------------------------------------------------------------


def test_imbalance_asks_once_for_its_one_price_field(
    markets_service: MarketsService,
) -> None:
    with markets_service.client() as client:
        get_imbalance_prices(DAY, client=client)

    params = markets_service.params("ImbalancePrice")
    assert params["columns"] == "TimeUTC,PriceArea,ImbalancePriceEUR"
    assert markets_service.filters() == {"PriceArea": ["DK1", "DK2"]}
    assert params["sort"] == "TimeUTC asc"
    assert params["start"] == "2026-09-14T22:00"
    assert params["end"] == "2026-09-15T22:00"


def test_afrr_energy_asks_for_prices_and_the_volumes_that_gate_them(
    markets_service: MarketsService,
) -> None:
    with markets_service.client() as client:
        get_afrr_energy_prices(DAY, client=client)

    params = markets_service.params("ImbalancePrice")
    assert params["columns"] == (
        "TimeUTC,PriceArea,aFRRVWAUpEUR,aFRRVWADownEUR,aFRRUpMW,aFRRDownMW"
    )


def test_mfrr_energy_asks_for_its_dataset_and_two_price_fields(
    markets_service: MarketsService,
) -> None:
    with markets_service.client() as client:
        get_mfrr_energy_prices(DAY, client=client)

    assert markets_service.datasets() == ["MfrrEnergyActivationMarket"]
    params = markets_service.params()
    assert params["columns"] == "TimeUTC,PriceArea,mFRRSAUpEUR,mFRRSADownEUR"
    assert params["sort"] == "TimeUTC asc"


@pytest.mark.parametrize(
    ("zones", "columns"),
    [
        ("DK2", [("DK2", "up"), ("DK2", "down")]),
        (
            ["DK2", "DK1"],
            [("DK2", "up"), ("DK2", "down"), ("DK1", "up"), ("DK1", "down")],
        ),
        (("DK1",), [("DK1", "up"), ("DK1", "down")]),
    ],
)
@pytest.mark.parametrize("getter", ALL_GETTERS)
def test_a_zone_subset_and_its_order_are_kept(
    markets_service: MarketsService,
    getter: Getter,
    zones: BiddingZone | Sequence[BiddingZone],
    columns: list[tuple[str, str]],
) -> None:
    with markets_service.client() as client:
        prices = getter(DAY, bidding_zones=zones, client=client)

    assert prices.columns.tolist() == columns
    assert markets_service.filters()["PriceArea"] == [z for z, _ in columns[::2]]


@pytest.mark.parametrize("zones", ["dk1", "SE3", "DK3", [], ["DK1", "DK1"]])
@pytest.mark.parametrize("getter", ALL_GETTERS)
def test_a_bad_zone_raises_before_any_request(
    markets_service: MarketsService, getter: Getter, zones: str | list[str]
) -> None:
    with markets_service.client() as client, pytest.raises(ValueError):
        getter(DAY, bidding_zones=zones, client=client)

    assert markets_service.requests == []


def test_a_bad_zone_error_names_it(markets_service: MarketsService) -> None:
    with markets_service.client() as client, pytest.raises(ValueError, match="SE3"):
        get_imbalance_prices(
            DAY,
            bidding_zones="SE3",  # type: ignore[arg-type]  # deliberately invalid
            client=client,
        )


# --- period rules -------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end", "rows"),
    [
        ("2026-03-29", None, 92),
        (DAY, None, 96),
        ("2026-10-25", None, 100),
        (LAST, None, 1),
        (f"{DAY}T23:00", "2026-09-16T00:00", 4),
        (f"{DAY}T23:45", "2026-09-16T00:00", 1),
    ],
)
@pytest.mark.parametrize("getter", ALL_GETTERS)
def test_the_period_gives_every_quarter_hour_and_end_is_exclusive(
    markets_service: MarketsService,
    getter: Getter,
    start: TimeLike,
    end: TimeLike | None,
    rows: int,
) -> None:
    with markets_service.client() as client:
        prices = getter(start, end, client=client)

    assert len(prices) == rows
    assert _tz(prices) == CPH


def test_a_naive_time_is_danish_time(markets_service: MarketsService) -> None:
    with markets_service.client() as client:
        prices = get_imbalance_prices(LAST, client=client)

    assert markets_service.params()["start"] == "2026-09-15T21:45"
    assert prices.index[0].isoformat() == "2026-09-15T23:45:00+02:00"


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        (f"{DAY}T00:10", None, "00:10"),
        (f"{DAY}T00:15", f"{DAY}T00:15", "start"),
        ("2026-09-16", DAY, "start"),
        ("2026-03-29T02:15", None, "02:15"),
        ("2026-10-25T02:15", None, "02:15"),
        ("not a date", None, "not a date"),
    ],
)
@pytest.mark.parametrize("getter", ALL_GETTERS)
def test_a_bad_period_raises_naming_the_value_with_no_request(
    markets_service: MarketsService,
    getter: Getter,
    start: str,
    end: str | None,
    message: str,
) -> None:
    with markets_service.client() as client, pytest.raises(ValueError, match=message):
        getter(start, end, client=client)

    assert markets_service.requests == []


def test_an_aware_time_in_the_repeated_hour_picks_one_quarter() -> None:
    service = MarketsService({"ImbalancePrice": []})

    with service.client() as client:
        prices = get_imbalance_prices("2026-10-25T02:15+01:00", client=client)

    assert len(prices) == 1
    assert prices.index[0].isoformat() == "2026-10-25T02:15:00+01:00"


@pytest.mark.parametrize("day", ["2026-03-29", "2026-10-25"])
def test_a_dst_day_has_a_full_nan_shape(day: str) -> None:
    service = MarketsService({"ImbalancePrice": []})

    with service.client() as client:
        prices = get_imbalance_prices(day, client=client)

    assert prices.shape == (92 if day.startswith("2026-03") else 100, 4)
    assert prices.isna().all().all()


def test_a_period_is_split_into_windows_that_give_the_same_frame(
    markets_service: MarketsService,
) -> None:
    with markets_service.client() as whole:
        expected = get_imbalance_prices(DAY, client=whole)
    markets_service.requests.clear()

    with markets_service.client(max_span=timedelta(hours=6)) as split:
        prices = get_imbalance_prices(DAY, client=split)

    assert len(markets_service.requests) == 4
    assert_frame_equal(prices, expected)


def test_records_outside_the_period_are_ignored(
    markets_service: MarketsService,
) -> None:
    extra = {
        "TimeUTC": "2026-09-14T21:45:00",
        "PriceArea": "DK1",
        "ImbalancePriceEUR": 999.0,
    }
    markets_service.respond = lambda _request: httpx.Response(
        200, json={"total": 1, "records": [extra]}
    )

    with markets_service.client() as client:
        prices = get_imbalance_prices(DAY, client=client)

    assert prices.shape == (96, 4)
    assert prices.isna().all().all()


def test_a_duplicate_record_raises_naming_it(markets_service: MarketsService) -> None:
    records = markets_service.data["ImbalancePrice"]
    markets_service.data["ImbalancePrice"] = [*records, records[0]]

    with markets_service.client() as client, pytest.raises(ValueError, match="DK1"):
        get_imbalance_prices(DAY, client=client)


# --- purity and the shared fetch path ------------------------------------------


def test_a_call_is_idempotent(markets_service: MarketsService) -> None:
    with markets_service.client() as client:
        first = get_afrr_energy_prices(DAY, client=client)
        second = get_afrr_energy_prices(DAY, client=client)

    assert_frame_equal(first, second)


def test_the_zone_default_is_not_changed_by_a_call(
    markets_service: MarketsService,
) -> None:
    zones: list[BiddingZone] = ["DK2", "DK1"]
    with markets_service.client() as client:
        get_imbalance_prices(DAY, bidding_zones=zones, client=client)

    assert zones == ["DK2", "DK1"]


def test_a_passed_client_is_left_open_and_reusable(
    markets_service: MarketsService,
) -> None:
    client = markets_service.client()

    get_imbalance_prices(DAY, client=client)

    assert not client.closed
    assert len(get_mfrr_energy_prices(DAY, client=client)) == 96
    client.close()


def test_a_closed_passed_client_raises_without_a_request(
    markets_service: MarketsService,
) -> None:
    client = markets_service.client()
    client.close()

    with pytest.raises(RuntimeError):
        get_imbalance_prices(DAY, client=client)

    assert markets_service.requests == []


class _Owned:
    """Replaces the client class `_markets` creates, remembering each instance."""

    def __init__(self, service: MarketsService) -> None:
        self.service = service
        self.made: list[EnergiDataServiceClient] = []

    def __call__(self) -> EnergiDataServiceClient:
        client = self.service.client()
        self.made.append(client)
        return client


@pytest.mark.parametrize("getter", ALL_GETTERS)
def test_an_owned_client_is_created_and_closed(
    markets_service: MarketsService, monkeypatch: pytest.MonkeyPatch, getter: Getter
) -> None:
    owned = _Owned(markets_service)
    monkeypatch.setattr(_markets, "EnergiDataServiceClient", owned)

    prices = getter(DAY)

    assert prices.shape == (96, 4)
    assert [c.closed for c in owned.made] == [True]


def test_an_owned_client_is_closed_when_the_service_refuses(
    markets_service: MarketsService, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = _Owned(markets_service)
    monkeypatch.setattr(_markets, "EnergiDataServiceClient", owned)
    markets_service.respond = lambda _r: httpx.Response(400, json={"message": "no"})

    with pytest.raises(httpx.HTTPStatusError):
        get_imbalance_prices(DAY)

    assert [c.closed for c in owned.made] == [True]


def test_a_payload_without_records_raises_and_still_closes_an_owned_client(
    markets_service: MarketsService, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = _Owned(markets_service)
    monkeypatch.setattr(_markets, "EnergiDataServiceClient", owned)
    markets_service.respond = lambda _r: httpx.Response(200, json={"total": 0})

    with pytest.raises(EnergiDataServiceError):
        get_mfrr_energy_prices(DAY)

    assert [c.closed for c in owned.made] == [True]


@pytest.mark.parametrize("getter", ALL_GETTERS)
def test_a_call_works_inside_a_running_event_loop(
    markets_service: MarketsService, getter: Getter
) -> None:
    with markets_service.client() as client:
        expected = getter(DAY, client=client)

        async def inside() -> pd.DataFrame:
            return getter(DAY, client=client)

        prices = asyncio.run(inside())

    assert_frame_equal(prices, expected)


def test_windows_of_a_long_period_run_concurrently(
    markets_service: MarketsService,
) -> None:
    in_flight = 0
    peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.05)
        in_flight -= 1
        return markets_service(request)

    client = EnergiDataServiceClient(
        transport=httpx.MockTransport(handler), max_span=timedelta(days=1)
    )

    with client:
        prices = get_imbalance_prices("2026-09-14", "2026-09-17", client=client)

    assert len(prices) == 288
    assert len(markets_service.requests) == 3
    assert peak > 1
