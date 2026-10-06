import asyncio
import json
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta
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
    get_afrr_capacity_prices,
    get_fcr_d_down_prices,
    get_fcr_d_up_prices,
    get_fcr_dk1_prices,
    get_fcr_n_prices,
    get_ffr_prices,
    get_mfrr_capacity_prices,
)
from energydata.utils import BiddingZone, TimeLike

CPH = "Europe/Copenhagen"
DAY = "2026-09-15"
type Getter = Callable[..., pd.DataFrame]
CAPACITY = [get_mfrr_capacity_prices, get_afrr_capacity_prices]
DK2_ONLY = [
    get_fcr_n_prices,
    get_fcr_d_up_prices,
    get_fcr_d_down_prices,
    get_ffr_prices,
]
EVERY_GETTER = [*CAPACITY, *DK2_ONLY, get_fcr_dk1_prices]
VOLUME_COLUMNS = ["up_demand", "up_procured", "down_demand", "down_procured"]
ZONE_COLUMNS = [("DK1", "up"), ("DK1", "down"), ("DK2", "up"), ("DK2", "down")]


def _stamp(local: str) -> pd.Timestamp:
    return pd.Timestamp(local, tz=CPH)


def _row(frame: pd.DataFrame, local: str) -> list[float]:
    values = frame.loc[_stamp(local)].to_numpy(dtype=float)
    return [float(v) for v in values]


def _at(frame: pd.DataFrame, local: str, column: Any) -> float:  # noqa: ANN401 - a column label of either depth
    return float(frame.loc[_stamp(local), column])


def _tz(frame: pd.DataFrame) -> str:
    assert isinstance(frame.index, pd.DatetimeIndex)
    return str(frame.index.tz)


def _capacity(utc: str, zone: str, **fields: float | None) -> dict[str, Any]:
    base: dict[str, Any] = {
        "TimeUTC": utc,
        "PriceArea": zone,
        "UpPriceEUR": 1.0,
        "DownPriceEUR": 2.0,
        "UpDemandMW": 3.0,
        "UpProcuredMW": 4.0,
        "DownDemandMW": 5.0,
        "DownProcuredMW": 6.0,
    }
    return {**base, **fields}


def _hourly_dk1(utc_hours: Sequence[str]) -> list[dict[str, Any]]:
    """FcrDK1 records whose prices are the UTC hour of day, for DST checks."""
    return [
        {
            "HourUTC": hour,
            "FCRcross_EUR": float(int(hour[11:13])),
            "FCRdk_EUR": float(int(hour[11:13])) + 0.5,
            "FCRdomestic_MW": 1.0,
            "FCRabroad_MW": 2.0,
        }
        for hour in utc_hours
    ]


def _hours_between(first: str, count: int) -> list[str]:
    start = pd.Timestamp(first)
    return [
        (start + pd.Timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%S")
        for i in range(count)
    ]


# --- mFRR and aFRR capacity ---------------------------------------------------


@pytest.mark.parametrize(
    ("getter", "dk1", "dk2"),
    [
        (get_mfrr_capacity_prices, [2.01, 0.11], [10.84, 0.11]),
        (get_afrr_capacity_prices, [0.4, 0.02], [13.0, 1.14]),
    ],
)
def test_capacity_prices_on_the_fixture_day_equal_the_records(
    markets_service: MarketsService,
    getter: Getter,
    dk1: list[float],
    dk2: list[float],
) -> None:
    with markets_service.client() as client:
        prices = getter(DAY, client=client)

    assert prices.shape == (24, 4)
    assert prices.columns.tolist() == ZONE_COLUMNS
    assert _row(prices, f"{DAY}T23:00") == dk1 + dk2
    assert _tz(prices) == CPH
    assert set(prices.dtypes) == {np.dtype("float64")}


def test_capacity_volumes_are_zone_major_in_the_documented_order(
    markets_service: MarketsService,
) -> None:
    with markets_service.client() as client:
        prices = get_mfrr_capacity_prices(
            f"{DAY}T23:00", include_volumes=True, client=client
        )

    assert prices.columns.tolist() == [
        (zone, field)
        for zone in ("DK1", "DK2")
        for field in ("up", "down", *VOLUME_COLUMNS)
    ]
    assert _row(prices, f"{DAY}T23:00") == [
        2.01, 0.11, 319.0, 390.0, 0.0, 3.0,
        10.84, 0.11, 250.0, 257.0, 0.0, 6.0,
    ]  # fmt: skip
    assert markets_service.params()["columns"] == (
        "TimeUTC,PriceArea,UpPriceEUR,DownPriceEUR,"
        "UpDemandMW,UpProcuredMW,DownDemandMW,DownProcuredMW"
    )


@pytest.mark.parametrize("getter", CAPACITY)
def test_capacity_without_volumes_requests_and_returns_only_prices(
    markets_service: MarketsService, getter: Getter
) -> None:
    with markets_service.client() as client:
        prices = getter(f"{DAY}T23:00", client=client)

    assert prices.shape == (1, 4)
    assert markets_service.params()["columns"] == (
        "TimeUTC,PriceArea,UpPriceEUR,DownPriceEUR"
    )


def test_the_volumes_flag_is_not_sticky(markets_service: MarketsService) -> None:
    with markets_service.client() as client:
        with_volumes = get_mfrr_capacity_prices(
            DAY, include_volumes=True, client=client
        )
        without = get_mfrr_capacity_prices(DAY, client=client)

    assert with_volumes.shape == (24, 12)
    assert without.shape == (24, 4)


@pytest.mark.parametrize(
    ("getter", "dataset"),
    [
        (get_mfrr_capacity_prices, "MfrrCapacityMarket"),
        (get_afrr_capacity_prices, "AfrrReservesNordic"),
    ],
)
def test_capacity_asks_for_its_dataset_with_the_zone_filter_and_sort(
    markets_service: MarketsService, getter: Getter, dataset: str
) -> None:
    with markets_service.client() as client:
        getter(DAY, client=client)

    assert markets_service.datasets() == [dataset]
    assert markets_service.filters(dataset) == {"PriceArea": ["DK1", "DK2"]}
    params = markets_service.params(dataset)
    assert params["sort"] == "TimeUTC asc"
    assert (params["start"], params["end"]) == ("2026-09-14T22:00", "2026-09-15T22:00")


def test_afrr_capacity_never_returns_a_nordic_zone_even_if_one_is_served(
    markets_service: MarketsService,
) -> None:
    markets_service.honour_filter = False

    with markets_service.client() as client:
        default = get_afrr_capacity_prices(f"{DAY}T23:00", client=client)
        dk2 = get_afrr_capacity_prices(
            f"{DAY}T23:00", bidding_zones="DK2", client=client
        )

    assert sorted(set(default.columns.get_level_values(0))) == ["DK1", "DK2"]
    assert _row(dk2, f"{DAY}T23:00") == [13.0, 1.14]
    first, second = markets_service.requests
    assert json.loads(first.url.params["filter"]) == {"PriceArea": ["DK1", "DK2"]}
    assert json.loads(second.url.params["filter"]) == {"PriceArea": ["DK2"]}


def test_capacity_prices_are_returned_as_published_zero_included(
    markets_service: MarketsService,
) -> None:
    # DK1, 21:00 local: nothing procured downward, price published as 0.0.
    with markets_service.client() as client:
        prices = get_mfrr_capacity_prices(
            f"{DAY}T21:00", bidding_zones="DK1", include_volumes=True, client=client
        )

    assert _at(prices, f"{DAY}T21:00", ("DK1", "down")) == 0.0
    assert _at(prices, f"{DAY}T21:00", ("DK1", "down_procured")) == 0.0
    assert _at(prices, f"{DAY}T21:00", ("DK1", "up")) == 3.2


def test_the_latest_capacity_record_has_a_zero_down_price_not_nan(
    markets_service: MarketsService,
) -> None:
    with markets_service.client() as client:
        prices = get_mfrr_capacity_prices("2026-10-07T23:00", client=client)

    assert _row(prices, "2026-10-07T23:00")[:2] == [0.8, 0.0]
    assert prices[("DK2", "up")].isna().all()


def test_a_null_value_is_nan_and_independent_of_the_other_fields() -> None:
    record = _capacity(
        "2026-09-15T21:00:00", "DK1", DownDemandMW=None, DownPriceEUR=0.11
    )
    service = MarketsService({"MfrrCapacityMarket": [record]})

    with service.client() as client:
        prices = get_mfrr_capacity_prices(
            f"{DAY}T23:00", bidding_zones="DK1", include_volumes=True, client=client
        )

    assert np.isnan(_at(prices, f"{DAY}T23:00", ("DK1", "down_demand")))
    assert _at(prices, f"{DAY}T23:00", ("DK1", "down")) == 0.11


def test_a_zone_without_a_record_is_an_all_nan_pair(
    markets_service: MarketsService,
) -> None:
    markets_service.data["MfrrCapacityMarket"] = [
        r for r in markets_service.data["MfrrCapacityMarket"] if r["PriceArea"] == "DK1"
    ]

    with markets_service.client() as client:
        prices = get_mfrr_capacity_prices(DAY, client=client)

    assert prices.shape == (24, 4)
    assert prices[("DK2", "up")].isna().all()
    assert prices[("DK2", "down")].isna().all()


@pytest.mark.parametrize("getter", CAPACITY)
def test_capacity_zone_subset_and_order_are_kept(
    markets_service: MarketsService, getter: Getter
) -> None:
    zones: list[BiddingZone] = ["DK2", "DK1"]

    with markets_service.client() as client:
        prices = getter(DAY, bidding_zones=zones, client=client)

    assert prices.columns.get_level_values(0).tolist() == ["DK2", "DK2", "DK1", "DK1"]
    assert list(markets_service.filters()["PriceArea"]) == ["DK2", "DK1"]


@pytest.mark.parametrize("zones", ["dk1", "SE3", [], ["DK1", "DK1"]])
@pytest.mark.parametrize("getter", CAPACITY)
def test_capacity_bad_zones_raise_before_any_request(
    markets_service: MarketsService, getter: Getter, zones: str | list[str]
) -> None:
    with markets_service.client() as client, pytest.raises(ValueError):
        getter(DAY, bidding_zones=zones, client=client)

    assert markets_service.requests == []


@pytest.mark.parametrize(
    ("day", "rows"), [("2026-03-29", 23), (DAY, 24), ("2026-10-25", 25)]
)
@pytest.mark.parametrize("getter", EVERY_GETTER[:-1])
def test_an_hourly_lone_date_has_the_length_of_the_local_day(
    getter: Getter, day: str, rows: int
) -> None:
    with MarketsService({}).client() as client:
        prices = getter(day, client=client)

    assert len(prices) == rows
    assert prices.isna().all().all()


def test_the_repeated_autumn_hour_keeps_both_hours_unfilled() -> None:
    records = [
        _capacity("2026-10-25T00:00:00", "DK1", UpPriceEUR=1.0),
        _capacity("2026-10-25T01:00:00", "DK1", UpPriceEUR=2.0),
    ]
    service = MarketsService({"MfrrCapacityMarket": records})

    with service.client() as client:
        prices = get_mfrr_capacity_prices(
            "2026-10-25", bidding_zones="DK1", client=client
        )

    ups = prices[("DK1", "up")]
    assert len(ups) == 25
    assert ups.iloc[2] == 1.0 and ups.iloc[3] == 2.0
    assert ups.drop(ups.index[[2, 3]]).isna().all()


@pytest.mark.parametrize("getter", EVERY_GETTER[:-1])
def test_an_hourly_market_rejects_a_quarter_hour_timestamp(
    markets_service: MarketsService, getter: Getter
) -> None:
    with markets_service.client() as client, pytest.raises(ValueError, match="00:15"):
        getter(f"{DAY}T00:15", client=client)

    assert markets_service.requests == []


@pytest.mark.parametrize("getter", EVERY_GETTER)
def test_an_hourly_lone_timestamp_is_one_slot_or_one_block(
    markets_service: MarketsService, getter: Getter
) -> None:
    with markets_service.client() as client:
        prices = getter(f"{DAY}T20:00", client=client)

    assert len(prices) == 1


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        (f"{DAY}T05:00", f"{DAY}T05:00", "start"),
        ("2026-09-16", DAY, "start"),
        ("2026-03-29T02:00", None, "02:00"),
        ("2026-10-25T02:00", None, "02:00"),
        ("nonsense", None, "nonsense"),
    ],
)
@pytest.mark.parametrize("getter", EVERY_GETTER)
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


@pytest.mark.parametrize(
    ("getter", "start"),
    [
        (get_mfrr_capacity_prices, "2023-06-20"),
        (get_afrr_capacity_prices, "2022-12-07"),
        (get_fcr_n_prices, "2021-11-09"),
        (get_fcr_d_up_prices, "2021-11-09"),
        (get_fcr_d_down_prices, "2021-11-09"),
        (get_fcr_dk1_prices, "2021-01-18"),
        (get_ffr_prices, "2021-04-25"),
    ],
)
def test_a_period_before_the_dataset_is_all_nan_and_still_requested(
    markets_service: MarketsService, getter: Getter, start: str
) -> None:
    with markets_service.client() as client:
        prices = getter(start, client=client)

    assert prices.isna().all().all()
    assert len(prices) == (6 if getter is get_fcr_dk1_prices else 24)
    assert len(markets_service.requests) == 1


def test_capacity_drops_an_off_grid_record_without_error() -> None:
    records = [
        _capacity("2026-09-15T21:00:00", "DK1", UpPriceEUR=7.0),
        _capacity("2026-09-15T21:15:00", "DK1", UpPriceEUR=9.0),
    ]
    service = MarketsService({"MfrrCapacityMarket": records})

    with service.client() as client:
        prices = get_mfrr_capacity_prices(
            f"{DAY}T23:00", bidding_zones="DK1", client=client
        )

    assert _at(prices, f"{DAY}T23:00", ("DK1", "up")) == 7.0


def test_records_outside_the_period_are_ignored(
    markets_service: MarketsService,
) -> None:
    extra = _capacity("2026-09-14T21:00:00", "DK1", UpPriceEUR=999.0)
    markets_service.respond = lambda _r: httpx.Response(
        200, json={"total": 1, "records": [extra]}
    )

    with markets_service.client() as client:
        prices = get_mfrr_capacity_prices(DAY, client=client)

    assert prices.isna().all().all()


def test_capacity_is_idempotent(markets_service: MarketsService) -> None:
    with markets_service.client() as client:
        first = get_afrr_capacity_prices(DAY, include_volumes=True, client=client)
        second = get_afrr_capacity_prices(DAY, include_volumes=True, client=client)

    assert_frame_equal(first, second)


# --- FCR-N, FCR-D up, FCR-D down and FFR (DK2) --------------------------------


@pytest.mark.parametrize(
    ("getter", "local", "price"),
    [
        (get_fcr_d_down_prices, f"{DAY}T12:00", 2.872822),
        (get_fcr_d_down_prices, f"{DAY}T23:00", 1.749259),
        (get_fcr_d_up_prices, f"{DAY}T13:00", 4.402034),
        (get_fcr_d_up_prices, f"{DAY}T23:00", 5.853653),
        (get_fcr_n_prices, f"{DAY}T13:00", 12.181186),
        (get_fcr_n_prices, f"{DAY}T23:00", 23.322175),
        (get_ffr_prices, f"{DAY}T23:00", 23.0),
    ],
)
def test_dk2_prices_equal_the_fixture_totals(
    markets_service: MarketsService, getter: Getter, local: str, price: float
) -> None:
    with markets_service.client() as client:
        prices = getter(DAY, client=client)

    assert prices.shape == (24, 1)
    assert prices.columns.tolist() == ["price"]
    assert not isinstance(prices.columns, pd.MultiIndex)
    assert prices.index.name == "time"
    assert _tz(prices) == CPH
    assert _at(prices, local, "price") == pytest.approx(price)
    assert set(prices.dtypes) == {np.dtype("float64")}


@pytest.mark.parametrize("getter", [get_fcr_n_prices, get_fcr_d_up_prices])
def test_an_hour_without_a_total_row_is_nan_not_filled(
    markets_service: MarketsService, getter: Getter
) -> None:
    # The fixture's 12:00 hour has no FCR-N or FCR-D up Total row.
    with markets_service.client() as client:
        prices = getter(DAY, client=client)

    assert np.isnan(_at(prices, f"{DAY}T12:00", "price"))
    assert prices["price"].loc[: _stamp(f"{DAY}T12:00")].isna().all()
    assert prices["price"].loc[_stamp(f"{DAY}T13:00") :].notna().all()


@pytest.mark.parametrize(
    ("getter", "product"),
    [
        (get_fcr_n_prices, "FCR-N"),
        (get_fcr_d_up_prices, "FCR-D upp"),
        (get_fcr_d_down_prices, "FCR-D ned"),
    ],
)
def test_fcr_asks_only_for_its_product_total_and_dk2(
    markets_service: MarketsService, getter: Getter, product: str
) -> None:
    with markets_service.client() as client:
        getter(DAY, include_volumes=True, client=client)

    assert markets_service.filters("FcrNdDK2") == {
        "AuctionType": ["Total"],
        "ProductName": [product],
        "PriceArea": ["DK2"],
    }
    params = markets_service.params("FcrNdDK2")
    assert params["columns"] == (
        "HourUTC,PriceArea,PriceTotalEUR,PurchasedVolumeLocal,PurchasedVolumeTotal"
    )
    assert params["sort"] == "HourUTC asc"


@pytest.mark.parametrize(
    ("getter", "product", "price", "local", "total"),
    [
        (get_fcr_n_prices, "FCR-N", 23.322175, 7.9, 239.0),
        (get_fcr_d_up_prices, "FCR-D upp", 5.853653, 60.9, 594.0),
        (get_fcr_d_down_prices, "FCR-D ned", 1.749259, 60.7, 574.3),
    ],
)
def test_fcr_takes_the_dk2_row_not_a_swedish_row_with_the_same_price(
    markets_service: MarketsService,
    getter: Getter,
    product: str,
    price: float,
    local: float,
    total: float,
) -> None:
    dk2 = [
        r
        for r in markets_service.data["FcrNdDK2"]
        if r["HourUTC"] == "2026-09-15T21:00:00"
        and r["AuctionType"] == "Total"
        and r["ProductName"] == product
    ]
    assert {r["PriceArea"] for r in dk2} >= {"DK2", "SE1", "SE2"}
    markets_service.respond = lambda _r: httpx.Response(
        200, json={"total": len(dk2), "records": dk2}
    )

    with markets_service.client() as client:
        prices = getter(f"{DAY}T23:00", include_volumes=True, client=client)

    assert prices.columns.tolist() == ["price", "purchased_local", "purchased_total"]
    assert _row(prices, f"{DAY}T23:00") == [pytest.approx(price), local, total]


def test_fcr_trusts_the_server_filter_for_products_and_raises_on_a_duplicate(
    markets_service: MarketsService,
) -> None:
    markets_service.honour_filter = False

    with (
        markets_service.client() as client,
        pytest.raises(ValueError, match="duplicate"),
    ):
        get_fcr_n_prices(DAY, client=client)


def test_fcr_calls_send_the_same_filter_each_time(
    markets_service: MarketsService,
) -> None:
    with markets_service.client() as client:
        get_fcr_n_prices(DAY, client=client)
        get_fcr_n_prices(DAY, client=client)
        get_fcr_d_up_prices(DAY, client=client)

    sent = [dict(r.url.params)["filter"] for r in markets_service.requests]
    assert sent[0] == sent[1]
    assert "FCR-D upp" in sent[2] and "FCR-N" not in sent[2]


def test_ffr_values_zero_and_spike_are_kept(markets_service: MarketsService) -> None:
    with markets_service.client() as client:
        prices = get_ffr_prices(DAY, include_volumes=True, client=client)

    assert prices.columns.tolist() == ["price", "demand", "purchased"]
    assert _row(prices, f"{DAY}T07:00") == [0.0, 0.0, 0.0]
    assert _at(prices, f"{DAY}T16:00", "price") == 737.0
    assert _row(prices, f"{DAY}T23:00") == [23.0, 5.485, 6.2]


def test_ffr_sends_no_zone_filter_and_asks_only_for_its_fields(
    markets_service: MarketsService,
) -> None:
    with markets_service.client() as client:
        get_ffr_prices(DAY, client=client)
        get_ffr_prices(DAY, include_volumes=True, client=client)

    first, second = (dict(r.url.params) for r in markets_service.requests)
    assert "filter" not in first
    assert first["columns"] == "HourUTC,FFR_PriceEUR"
    assert second["columns"] == "HourUTC,FFR_PriceEUR,FFR_DemandMW,FFR_PurchasedMW"
    assert markets_service.datasets() == ["FfrDK2", "FfrDK2"]


@pytest.mark.parametrize(
    ("getter", "columns"),
    [
        (get_fcr_n_prices, ["price", "purchased_local", "purchased_total"]),
        (get_ffr_prices, ["price", "demand", "purchased"]),
    ],
)
def test_an_empty_response_keeps_the_named_columns_and_the_full_index(
    getter: Getter, columns: list[str]
) -> None:
    with MarketsService({}).client() as client:
        prices = getter(DAY, include_volumes=True, client=client)

    assert prices.columns.tolist() == columns
    assert prices.shape == (24, 3)
    assert prices.isna().all().all()


@pytest.mark.parametrize("getter", DK2_ONLY)
def test_dk2_functions_take_no_zone_argument(
    markets_service: MarketsService, getter: Getter
) -> None:
    with markets_service.client() as client, pytest.raises(TypeError):
        getter(DAY, None, "DK2", client=client)

    assert markets_service.requests == []


@pytest.mark.parametrize("getter", DK2_ONLY)
def test_dk2_volumes_are_off_by_default(
    markets_service: MarketsService, getter: Getter
) -> None:
    with markets_service.client() as client:
        plain = getter(DAY, client=client)
        full = getter(DAY, include_volumes=True, client=client)

    assert plain.columns.tolist() == ["price"]
    assert full.columns.tolist()[0] == "price"
    assert full.shape == (24, 3)


def test_a_null_dk2_price_is_nan() -> None:
    record = {"HourUTC": "2026-09-15T21:00:00", "FFR_PriceEUR": None}
    service = MarketsService({"FfrDK2": [record]})

    with service.client() as client:
        prices = get_ffr_prices(f"{DAY}T23:00", client=client)

    assert prices.shape == (1, 1)
    assert prices.isna().all().all()


# --- FCR DK1 ------------------------------------------------------------------


def test_fcr_dk1_fixture_day_has_six_four_hour_blocks(
    markets_service: MarketsService,
) -> None:
    with markets_service.client() as client:
        prices = get_fcr_dk1_prices(DAY, client=client)

    assert [t.hour for t in prices.index] == [0, 4, 8, 12, 16, 20]
    assert prices.columns.tolist() == ["cross_border", "danish"]
    assert prices["cross_border"].tolist() == [18.22, 13.0, 34.79, 52.61, 49.2, 25.0]
    assert prices["danish"].tolist() == [3.69, 11.62, 34.79, 52.61, 49.2, 25.0]
    assert _tz(prices) == CPH


def test_fcr_dk1_volumes_and_the_exact_request(markets_service: MarketsService) -> None:
    with markets_service.client() as client:
        prices = get_fcr_dk1_prices(DAY, include_volumes=True, client=client)

    assert prices.columns.tolist() == ["cross_border", "danish", "domestic", "abroad"]
    assert _row(prices, f"{DAY}T00:00") == [18.22, 3.69, 5.0, 24.0]
    params = markets_service.params("FcrDK1")
    assert "filter" not in params
    assert params["columns"] == (
        "HourUTC,FCRcross_EUR,FCRdk_EUR,FCRdomestic_MW,FCRabroad_MW"
    )
    assert params["start"] == "2026-09-14T22:00"


def test_fcr_dk1_without_volumes_asks_only_for_prices(
    markets_service: MarketsService,
) -> None:
    with markets_service.client() as client:
        get_fcr_dk1_prices(DAY, client=client)

    assert markets_service.params()["columns"] == "HourUTC,FCRcross_EUR,FCRdk_EUR"


def test_fcr_dk1_takes_the_first_hour_of_a_block_never_a_later_one(
    markets_service: MarketsService,
) -> None:
    markets_service.data["FcrDK1"] = [
        r
        for r in markets_service.data["FcrDK1"]
        if r["HourUTC"] != "2026-09-15T02:00:00"
    ]

    with markets_service.client() as client:
        prices = get_fcr_dk1_prices(DAY, client=client)

    assert prices.loc[_stamp(f"{DAY}T04:00")].isna().all()  # 05:00-07:00 hold 13.0
    assert prices["cross_border"].drop(_stamp(f"{DAY}T04:00")).notna().all()


@pytest.mark.parametrize(
    ("start", "request_start", "request_end"),
    [
        (f"{DAY}T04:00", "2026-09-15T02:00", "2026-09-15T06:00"),
        (f"{DAY}T20:00", "2026-09-15T18:00", "2026-09-15T22:00"),
        ("2026-03-29T00:00", "2026-03-28T23:00", "2026-03-29T02:00"),
        ("2026-10-25T00:00", "2026-10-24T22:00", "2026-10-25T03:00"),
        ("2026-09-14T22:00Z", "2026-09-14T22:00", "2026-09-15T02:00"),
    ],
)
def test_fcr_dk1_lone_block_start_is_one_row_fetching_four_wall_clock_hours(
    markets_service: MarketsService, start: str, request_start: str, request_end: str
) -> None:
    with markets_service.client() as client:
        prices = get_fcr_dk1_prices(start, client=client)

    assert len(prices) == 1
    params = markets_service.params()
    assert (params["start"], params["end"]) == (request_start, request_end)


def test_fcr_dk1_lone_block_start_carries_the_blocks_value(
    markets_service: MarketsService,
) -> None:
    with markets_service.client() as client:
        prices = get_fcr_dk1_prices(f"{DAY}T04:00", client=client)

    assert _row(prices, f"{DAY}T04:00") == [13.0, 11.62]


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (f"{DAY}T01:00", None),
        (f"{DAY}T02:00", None),
        (f"{DAY}T04:30", None),
        (f"{DAY}T04:00", f"{DAY}T05:00"),
        (DAY, f"{DAY}T06:00"),
        (f"{DAY}T02:00", f"{DAY}T08:00"),
    ],
)
def test_fcr_dk1_rejects_a_bound_that_is_not_a_block_start_with_no_request(
    markets_service: MarketsService, start: str, end: str | None
) -> None:
    with (
        markets_service.client() as client,
        pytest.raises(ValueError, match="block|boundary"),
    ):
        get_fcr_dk1_prices(start, end, client=client)

    assert markets_service.requests == []


@pytest.mark.parametrize(
    ("start", "rows"),
    [
        (date(2026, 9, 15), 6),
        (DAY, 6),
        (datetime(2026, 9, 15), 1),  # noqa: DTZ001 - a naive local time is the point
        (pd.Timestamp("2026-09-15"), 1),
        (f"{DAY}T00:00", 1),
    ],
)
def test_fcr_dk1_a_lone_date_is_a_day_and_a_lone_midnight_timestamp_is_a_block(
    markets_service: MarketsService, start: TimeLike, rows: int
) -> None:
    with markets_service.client() as client:
        prices = get_fcr_dk1_prices(start, client=client)

    assert len(prices) == rows


def test_fcr_dk1_partial_day_and_aware_start(markets_service: MarketsService) -> None:
    with markets_service.client() as client:
        partial = get_fcr_dk1_prices(f"{DAY}T08:00", "2026-09-16", client=client)
        aware = get_fcr_dk1_prices(f"{DAY}T02:00+00:00", client=client)

    assert partial["cross_border"].tolist() == [34.79, 52.61, 49.2, 25.0]
    assert aware.index[0] == _stamp(f"{DAY}T04:00")
    assert _row(aware, f"{DAY}T04:00") == [13.0, 11.62]


@pytest.mark.parametrize(
    ("day", "first_utc", "expected"),
    [
        ("2026-03-29", "2026-03-28T23:00:00", [23, 2, 6, 10, 14, 18]),
        ("2026-10-25", "2026-10-24T22:00:00", [22, 3, 7, 11, 15, 19]),
    ],
)
def test_fcr_dk1_dst_day_has_six_blocks_each_holding_its_first_hours_value(
    day: str, first_utc: str, expected: list[int]
) -> None:
    service = MarketsService({"FcrDK1": _hourly_dk1(_hours_between(first_utc, 26))})

    with service.client() as client:
        prices = get_fcr_dk1_prices(day, client=client)

    assert [t.hour for t in prices.index] == [0, 4, 8, 12, 16, 20]
    assert prices["cross_border"].tolist() == [float(h) for h in expected]
    assert prices["danish"].tolist() == [h + 0.5 for h in expected]


def test_fcr_dk1_multi_day_period_and_end_exclusive(
    markets_service: MarketsService,
) -> None:
    with markets_service.client() as client:
        prices = get_fcr_dk1_prices(DAY, "2026-09-17", client=client)

    assert len(prices) == 12
    assert prices["cross_border"].iloc[:6].notna().all()
    assert prices["cross_border"].iloc[6:].isna().all()


def test_fcr_dk1_is_idempotent(markets_service: MarketsService) -> None:
    with markets_service.client() as client:
        first = get_fcr_dk1_prices(DAY, include_volumes=True, client=client)
        second = get_fcr_dk1_prices(DAY, include_volumes=True, client=client)

    assert_frame_equal(first, second)


# --- the shared fetch path for the other shapes ----------------------------------

SHAPES = [
    get_mfrr_capacity_prices,
    get_fcr_n_prices,
    get_fcr_dk1_prices,
    get_ffr_prices,
]


class _Owned:
    """Replaces the client class `_markets` creates, remembering each instance."""

    def __init__(self, service: MarketsService) -> None:
        self.service = service
        self.made: list[EnergiDataServiceClient] = []

    def __call__(self) -> EnergiDataServiceClient:
        client = self.service.client()
        self.made.append(client)
        return client


@pytest.mark.parametrize("getter", EVERY_GETTER)
def test_an_owned_client_is_created_and_closed(
    markets_service: MarketsService, monkeypatch: pytest.MonkeyPatch, getter: Getter
) -> None:
    owned = _Owned(markets_service)
    monkeypatch.setattr(_markets, "EnergiDataServiceClient", owned)

    prices = getter(DAY)

    assert len(prices) in (6, 24)
    assert [c.closed for c in owned.made] == [True]


@pytest.mark.parametrize("getter", SHAPES)
def test_an_owned_client_is_closed_when_the_service_refuses(
    markets_service: MarketsService, monkeypatch: pytest.MonkeyPatch, getter: Getter
) -> None:
    owned = _Owned(markets_service)
    monkeypatch.setattr(_markets, "EnergiDataServiceClient", owned)
    markets_service.respond = lambda _r: httpx.Response(400, json={"message": "no"})

    with pytest.raises(httpx.HTTPStatusError):
        getter(DAY)

    assert [c.closed for c in owned.made] == [True]


@pytest.mark.parametrize("getter", SHAPES)
def test_a_payload_without_records_raises_and_still_closes_an_owned_client(
    markets_service: MarketsService, monkeypatch: pytest.MonkeyPatch, getter: Getter
) -> None:
    owned = _Owned(markets_service)
    monkeypatch.setattr(_markets, "EnergiDataServiceClient", owned)
    markets_service.respond = lambda _r: httpx.Response(200, json={"total": 0})

    with pytest.raises(EnergiDataServiceError):
        getter(DAY)

    assert [c.closed for c in owned.made] == [True]


@pytest.mark.parametrize("getter", EVERY_GETTER)
def test_a_passed_client_is_left_open_and_a_closed_one_is_refused(
    markets_service: MarketsService, getter: Getter
) -> None:
    client = markets_service.client()
    getter(DAY, client=client)
    assert not client.closed

    client.close()
    markets_service.requests.clear()
    with pytest.raises(RuntimeError):
        getter(DAY, client=client)

    assert markets_service.requests == []


@pytest.mark.parametrize("getter", SHAPES)
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
        prices = get_mfrr_capacity_prices("2026-09-14", "2026-09-17", client=client)

    assert len(prices) == 72
    assert len(markets_service.requests) == 3
    assert peak > 1
