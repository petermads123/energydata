import asyncio
import json
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import httpx
import pandas as pd
import pytest

from energydata.energidataservice import (
    EnergiDataServiceClient,
    EnergiDataServiceError,
    day_ahead,
    get_day_ahead_prices,
)
from energydata.energidataservice.day_ahead import SWITCH
from energydata.utils import BiddingZone, RetryPolicy, TimeLike, period_index

CPH = "Europe/Copenhagen"
QUARTER = timedelta(minutes=15)
FAST = RetryPolicy(base_delay=0.0, max_delay=0.0)
TIME_FORMAT = "%Y-%m-%dT%H:%M"
# Hourly era: before 2025-10-01 00:00 Danish time. Quarter era: from it.
HOURLY_DAY = "2025-01-15"
QUARTER_DAY = "2026-01-15"

type Records = list[dict[str, Any]]


def _hourly(utc: str, zone: str, price: float | None) -> dict[str, Any]:
    return {"HourUTC": utc, "PriceArea": zone, "SpotPriceEUR": price}


def _quarter(utc: str, zone: str, price: float | None) -> dict[str, Any]:
    return {"TimeUTC": utc, "PriceArea": zone, "DayAheadPriceEUR": price}


class Service:
    """A mock Energi Data Service: serves stored records and notes every request."""

    def __init__(
        self, hourly: Records | None = None, quarter: Records | None = None
    ) -> None:
        """Store the records each dataset serves."""
        self.data: dict[str, Records] = {
            "Elspotprices": hourly or [],
            "DayAheadPrices": quarter or [],
        }
        self.time_field = {"Elspotprices": "HourUTC", "DayAheadPrices": "TimeUTC"}
        self.requests: list[httpx.Request] = []
        self.honour_filter = True

    def datasets(self) -> list[str]:
        """The dataset of each request, in arrival order."""
        return [request.url.path.rsplit("/", 1)[1] for request in self.requests]

    def params(self, dataset: str) -> dict[str, str]:
        """The query parameters of the one request made to `dataset`."""
        (request,) = [r for r in self.requests if r.url.path == f"/dataset/{dataset}"]
        return dict(request.url.params)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Answer a request the way the API does: window, then zone filter."""
        self.requests.append(request)
        dataset = request.url.path.rsplit("/", 1)[1]
        params = request.url.params
        start = datetime.strptime(params["start"], TIME_FORMAT)  # noqa: DTZ007 - UTC text
        end = datetime.strptime(params["end"], TIME_FORMAT)  # noqa: DTZ007 - UTC text
        zones = (
            json.loads(params["filter"])["PriceArea"] if "filter" in params else None
        )
        field = self.time_field[dataset]
        records = [
            r
            for r in self.data[dataset]
            if start <= datetime.fromisoformat(r[field]) < end
            and (zones is None or not self.honour_filter or r["PriceArea"] in zones)
        ]
        return httpx.Response(200, json={"total": len(records), "records": records})

    def client(
        self, max_span: timedelta = timedelta(days=31)
    ) -> EnergiDataServiceClient:
        """A real client wired to this mock."""
        return EnergiDataServiceClient(
            transport=httpx.MockTransport(self), policy=FAST, max_span=max_span
        )


@contextmanager
def _served(
    service: Service, max_span: timedelta = timedelta(days=31)
) -> Iterator[EnergiDataServiceClient]:
    with service.client(max_span) as client:
        yield client


def _stamp(local: str) -> pd.Timestamp:
    return pd.Timestamp(local, tz=CPH)


def _utc(local: str) -> str:
    """The `...T..:..:..` UTC text of a Danish local time, as the API writes it."""
    return _stamp(local).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%S")


def _quarters(day: str, zone: str, prices: Sequence[float | None]) -> Records:
    """15-minute records for `zone` from local midnight of `day`, one per price."""
    first = _stamp(day)
    return [
        _quarter(
            (first + i * QUARTER).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%S"),
            zone,
            price,
        )
        for i, price in enumerate(prices)
    ]


def _hours(day: str, zone: str, prices: Sequence[float | None]) -> Records:
    first = _stamp(day)
    return [
        _hourly(
            (first + timedelta(hours=i))
            .tz_convert("UTC")
            .strftime("%Y-%m-%dT%H:%M:%S"),
            zone,
            price,
        )
        for i, price in enumerate(prices)
    ]


def _get(
    service: Service,
    start: TimeLike,
    end: TimeLike | None = None,
    bidding_zones: BiddingZone | Sequence[BiddingZone] = ("DK1", "DK2"),
) -> pd.DataFrame:
    with _served(service) as client:
        return get_day_ahead_prices(start, end, bidding_zones, client=client)


# --- shape: A1 ----------------------------------------------------------------


def test_day_ahead_quarter_era_day_has_index_columns_and_values() -> None:
    dk1 = [float(i) for i in range(96)]
    dk2 = [100.0 + i for i in range(96)]
    service = Service(
        quarter=_quarters(QUARTER_DAY, "DK1", dk1) + _quarters(QUARTER_DAY, "DK2", dk2)
    )

    prices = _get(service, QUARTER_DAY)

    expected = period_index(_stamp(QUARTER_DAY), _stamp("2026-01-16"), QUARTER)
    pd.testing.assert_index_equal(prices.index, expected)
    assert str(prices.index.dtype) == "datetime64[ns, Europe/Copenhagen]"
    assert list(prices.columns) == ["DK1", "DK2"]
    assert (prices.dtypes == "float64").all()
    assert prices["DK1"].tolist() == dk1
    assert prices["DK2"].tolist() == dk2


@pytest.mark.parametrize(
    ("day", "rows"),
    [
        ("2025-03-30", 92),  # spring change, hourly era
        ("2025-10-26", 100),  # autumn change, quarter era
        ("2026-03-29", 92),
        ("2026-10-25", 100),
    ],
)
def test_day_ahead_dst_days_have_92_or_100_slots(day: str, rows: int) -> None:
    prices = _get(Service(), day)

    assert len(prices) == rows
    assert prices.index.is_unique
    assert prices.isna().all().all()


def test_day_ahead_hourly_era_autumn_change_day_has_100_slots_from_25_hours() -> None:
    day = "2024-10-27"
    service = Service(hourly=_hours(day, "DK1", [float(h) for h in range(25)]))

    prices = _get(service, day, bidding_zones="DK1")

    assert len(prices) == 100
    assert prices["DK1"].tolist() == [float(h) for h in range(25) for _ in range(4)]


def test_day_ahead_values_are_floats_even_when_the_api_sends_integers() -> None:
    service = Service(quarter=_quarters(QUARTER_DAY, "DK1", [7] * 96))  # type: ignore[list-item]  # ints on purpose

    prices = _get(service, QUARTER_DAY, bidding_zones="DK1")

    assert prices["DK1"].dtype == "float64"
    assert prices["DK1"].iloc[0] == 7.0


# --- the hourly era: A2 -------------------------------------------------------


def test_day_ahead_hourly_era_repeats_each_hour_over_its_quarters() -> None:
    service = Service(hourly=_hours(HOURLY_DAY, "DK1", [float(h) for h in range(24)]))

    prices = _get(service, HOURLY_DAY, bidding_zones="DK1")

    assert len(prices) == 96
    assert prices["DK1"].tolist() == [float(h) for h in range(24) for _ in range(4)]


def test_day_ahead_hourly_era_requests_only_the_hourly_dataset() -> None:
    service = Service()

    _get(service, HOURLY_DAY)

    assert service.datasets() == ["Elspotprices"]


def test_day_ahead_hourly_gap_stays_nan_never_filled_from_a_neighbour() -> None:
    service = Service(
        hourly=[
            _hourly(_utc("2025-01-15 10:00"), "DK1", 10.0),
            _hourly(_utc("2025-01-15 12:00"), "DK1", 12.0),
        ]
    )

    prices = _get(service, "2025-01-15T10:00", "2025-01-15T13:00", ["DK1"])

    assert prices["DK1"].tolist()[:4] == [10.0] * 4
    assert prices["DK1"].iloc[4:8].isna().all()
    assert prices["DK1"].tolist()[8:] == [12.0] * 4


def test_day_ahead_hourly_null_price_is_nan() -> None:
    service = Service(hourly=[_hourly(_utc("2025-01-15 10:00"), "DK1", None)])

    prices = _get(service, "2025-01-15T10:00", "2025-01-15T11:00", ["DK1"])

    assert prices["DK1"].isna().all()


def test_day_ahead_mid_hour_start_gets_its_hours_price_and_widens_the_request() -> None:
    service = Service(hourly=[_hourly(_utc("2025-01-15 12:00"), "DK1", 50.0)])

    prices = _get(service, "2025-01-15T12:30", "2025-01-15T12:45", "DK1")

    assert prices["DK1"].tolist() == [50.0]
    assert service.params("Elspotprices")["start"] == "2025-01-15T11:00"
    assert service.params("Elspotprices")["end"] == "2025-01-15T12:00"


# --- the 15-minute era: A2 ----------------------------------------------------


def test_day_ahead_quarter_era_requests_only_the_quarter_dataset() -> None:
    service = Service()

    _get(service, QUARTER_DAY)

    assert service.datasets() == ["DayAheadPrices"]


def test_day_ahead_null_quarter_value_is_nan_even_when_hourly_data_exists() -> None:
    # Hourly data for the same hour sits in the mock; it must never be asked for.
    quarter = _quarters("2026-01-15", "DK1", [None] * 96)
    hourly = _hours("2026-01-15", "DK1", [99.0] * 24)
    service = Service(hourly=hourly, quarter=quarter)

    prices = _get(service, QUARTER_DAY, bidding_zones="DK1")

    assert prices["DK1"].isna().all()
    assert service.datasets() == ["DayAheadPrices"]


def test_day_ahead_missing_quarter_slot_is_nan_even_when_hourly_data_exists() -> None:
    quarter = _quarters("2026-01-15", "DK1", [float(i) for i in range(96)])
    del quarter[10]
    service = Service(hourly=_hours("2026-01-15", "DK1", [99.0] * 24), quarter=quarter)

    prices = _get(service, QUARTER_DAY, bidding_zones="DK1")

    assert prices["DK1"].isna().sum() == 1
    assert pd.isna(prices["DK1"].iloc[10])
    assert prices["DK1"].iloc[9] == 9.0
    assert prices["DK1"].iloc[11] == 11.0


def test_day_ahead_partial_publication_pads_the_unpublished_slots() -> None:
    service = Service(quarter=_quarters(QUARTER_DAY, "DK1", [1.0] * 48))

    prices = _get(service, QUARTER_DAY, bidding_zones="DK1")

    assert prices["DK1"].iloc[:48].tolist() == [1.0] * 48
    assert prices["DK1"].iloc[48:].isna().all()
    assert len(prices) == 96


def test_day_ahead_future_period_is_all_nan_of_the_right_shape() -> None:
    service = Service()

    prices = _get(service, "2030-01-01")

    assert prices.shape == (96, 2)
    assert prices.isna().all().all()
    assert list(prices.columns) == ["DK1", "DK2"]
    assert str(prices.index.dtype) == "datetime64[ns, Europe/Copenhagen]"


# --- the switch ---------------------------------------------------------------


def test_day_ahead_switch_is_the_first_of_october_2025_danish_midnight() -> None:
    assert pd.Timestamp("2025-09-30T22:00:00", tz="UTC") == SWITCH
    assert str(SWITCH.tz) == CPH


def _spanning_service() -> Service:
    return Service(
        hourly=[
            _hourly("2025-09-30T21:00:00", "DK1", 50.0),  # 23:00 local, last hourly
            _hourly(
                "2025-09-30T22:00:00", "DK1", 99.0
            ),  # 00:00 local: after the switch
        ],
        quarter=[
            _quarter("2025-09-30T22:00:00", "DK1", 60.0),
            _quarter("2025-09-30T22:15:00", "DK1", 61.0),
            _quarter("2025-09-30T22:30:00", "DK1", 62.0),
            _quarter("2025-09-30T22:45:00", "DK1", 63.0),
            _quarter(
                "2025-09-30T21:00:00", "DK1", 7.0
            ),  # 15-min data before the switch
        ],
    )


def test_day_ahead_period_spanning_the_switch_combines_each_side_by_era() -> None:
    service = _spanning_service()

    prices = _get(service, "2025-10-01T00:00", "2025-10-01T01:00", "DK1")
    before = _get(service, "2025-09-30T23:00", "2025-10-01T01:00", "DK1")

    assert prices["DK1"].tolist()[:4] == [60.0, 61.0, 62.0, 63.0]
    assert before["DK1"].tolist()[:4] == [50.0] * 4  # hourly before the switch
    assert before["DK1"].tolist()[4:8] == [60.0, 61.0, 62.0, 63.0]  # not the hourly 99
    assert len(before) == 8  # two hours of quarter-hours


def test_day_ahead_spanning_period_requests_each_dataset_over_its_own_side() -> None:
    service = _spanning_service()

    _get(service, "2025-09-30T23:00", "2025-10-01T01:00", "DK1")

    assert sorted(service.datasets()) == ["DayAheadPrices", "Elspotprices"]
    assert service.params("Elspotprices")["start"] == "2025-09-30T21:00"
    assert service.params("Elspotprices")["end"] == "2025-09-30T22:00"
    assert service.params("DayAheadPrices")["start"] == "2025-09-30T22:00"
    assert service.params("DayAheadPrices")["end"] == "2025-09-30T23:00"


def test_day_ahead_null_at_the_switch_slot_is_nan_not_the_hourly_price() -> None:
    service = Service(
        hourly=[
            _hourly("2025-09-30T21:00:00", "DK1", 50.0),
            _hourly("2025-09-30T22:00:00", "DK1", 99.0),
        ],
        quarter=[
            _quarter("2025-09-30T22:00:00", "DK1", None),
            _quarter("2025-09-30T22:15:00", "DK1", 5.0),
        ],
    )

    prices = _get(service, "2025-09-30T23:30", "2025-10-01T00:30", "DK1")

    values = prices["DK1"].tolist()
    assert values[:2] == [50.0, 50.0]  # 23:30 and 23:45, still hourly
    assert pd.isna(values[2])  # 00:00, the null 15-minute value
    assert values[3] == 5.0


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        ("2025-09-30", "2025-10-01", ["Elspotprices"]),  # ends exactly at the switch
        ("2025-10-01", "2025-10-02", ["DayAheadPrices"]),  # starts exactly at it
        ("2025-09-30T23:45", "2025-10-01T00:00", ["Elspotprices"]),  # last hourly slot
        (
            "2025-10-01T00:00",
            "2025-10-01T00:15",
            ["DayAheadPrices"],
        ),  # first 15-min slot
        ("2025-09-30T23:45", "2025-10-01T00:15", ["DayAheadPrices", "Elspotprices"]),
        ("2025-09-30", "2025-10-02", ["DayAheadPrices", "Elspotprices"]),
        ("2024-01-01", "2024-01-02", ["Elspotprices"]),
        ("2027-01-01", "2027-01-02", ["DayAheadPrices"]),
    ],
)
def test_day_ahead_requests_a_dataset_only_when_the_period_overlaps_its_side(
    start: str, end: str, expected: list[str]
) -> None:
    service = Service()

    _get(service, start, end)

    assert sorted(service.datasets()) == expected


def test_day_ahead_period_across_the_switch_has_one_unbroken_index() -> None:
    service = _spanning_service()

    prices = _get(service, "2025-09-30", "2025-10-02", "DK1")

    expected = period_index(_stamp("2025-09-30"), _stamp("2025-10-02"), QUARTER)
    pd.testing.assert_index_equal(prices.index, expected)
    assert len(prices) == 192
    assert list(prices.columns) == ["DK1"]
    assert prices["DK1"].dtype == "float64"


# --- period handling: A3 ------------------------------------------------------


def test_day_ahead_lone_mid_hour_timestamp_in_the_hourly_era_is_one_row() -> None:
    service = Service(hourly=[_hourly(_utc("2025-01-15 12:00"), "DK1", 41.0)])

    prices = _get(service, "2025-01-15T12:30", bidding_zones="DK1")

    assert prices.index.tolist() == [_stamp("2025-01-15 12:30")]
    assert prices["DK1"].tolist() == [41.0]
    assert service.params("Elspotprices")["start"] == "2025-01-15T11:00"
    assert service.params("Elspotprices")["end"] == "2025-01-15T12:00"


def test_day_ahead_lone_timestamp_in_the_quarter_era_is_one_row() -> None:
    service = Service(quarter=[_quarter(_utc("2026-01-15 12:30"), "DK2", 33.0)])

    prices = _get(service, "2026-01-15T12:30", bidding_zones="DK2")

    assert prices["DK2"].tolist() == [33.0]
    assert service.params("DayAheadPrices")["start"] == "2026-01-15T11:30"
    assert service.params("DayAheadPrices")["end"] == "2026-01-15T11:45"


def test_day_ahead_end_is_exclusive_and_records_outside_the_period_are_dropped() -> (
    None
):
    service = Service(
        quarter=_quarters("2026-01-15", "DK1", [float(i) for i in range(96)])
    )
    service.data["DayAheadPrices"] += _quarters("2026-01-14", "DK1", [-1.0] * 96)

    prices = _get(service, "2026-01-15T01:00", "2026-01-15T02:00", "DK1")

    assert prices["DK1"].tolist() == [4.0, 5.0, 6.0, 7.0]
    assert prices.index[-1] == _stamp("2026-01-15 01:45")


def test_day_ahead_an_api_that_ignores_the_window_never_leaks_records_in() -> None:
    stored = _quarters("2026-01-15", "DK1", [1.0] * 96)

    def ignores_window(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(200, json={"records": stored})

    with EnergiDataServiceClient(
        transport=httpx.MockTransport(ignores_window), policy=FAST
    ) as client:
        prices = get_day_ahead_prices(
            "2026-01-15T00:00", "2026-01-15T01:00", "DK1", client=client
        )

    assert len(prices) == 4
    assert prices["DK1"].tolist() == [1.0] * 4


def test_day_ahead_accepts_aware_and_datetime_input_in_another_zone() -> None:
    service = Service(
        quarter=_quarters(QUARTER_DAY, "DK1", [float(i) for i in range(96)])
    )

    prices = _get(
        service,
        datetime(2026, 1, 14, 23, 0, tzinfo=UTC),  # local midnight
        datetime(2026, 1, 15, 0, 0, tzinfo=UTC) + timedelta(hours=1),  # 02:00 local
        "DK1",
    )

    assert prices["DK1"].tolist() == [float(i) for i in range(8)]
    assert str(prices["DK1"].index.dtype) == "datetime64[ns, Europe/Copenhagen]"


# --- zones: A1 ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("zones", "columns"),
    [
        ("DK2", ["DK2"]),
        ("DK1", ["DK1"]),
        (["DK2", "DK1"], ["DK2", "DK1"]),
        (("DK1",), ["DK1"]),
    ],
)
def test_day_ahead_zone_subset_and_order_follow_the_argument(
    zones: BiddingZone | Sequence[BiddingZone], columns: list[str]
) -> None:
    service = Service(
        quarter=_quarters(QUARTER_DAY, "DK1", [1.0] * 96)
        + _quarters(QUARTER_DAY, "DK2", [2.0] * 96)
    )
    service.honour_filter = False  # the frame must still hold only what was asked

    prices = _get(service, QUARTER_DAY, bidding_zones=zones)

    assert list(prices.columns) == columns
    assert json.loads(service.params("DayAheadPrices")["filter"]) == {
        "PriceArea": columns
    }


def test_day_ahead_default_zones_are_both_in_default_order() -> None:
    service = Service()

    prices = _get(service, QUARTER_DAY)

    assert list(prices.columns) == ["DK1", "DK2"]


def test_day_ahead_a_zone_with_no_data_is_an_all_nan_column() -> None:
    service = Service(quarter=_quarters(QUARTER_DAY, "DK1", [1.0] * 96))

    prices = _get(service, QUARTER_DAY)

    assert prices["DK1"].notna().all()
    assert prices["DK2"].isna().all()


def test_day_ahead_zone_list_is_not_mutated() -> None:
    zones: list[BiddingZone] = ["DK2", "DK1"]

    _get(Service(), QUARTER_DAY, bidding_zones=zones)

    assert zones == ["DK2", "DK1"]


# --- request contents ---------------------------------------------------------


def test_day_ahead_requests_carry_the_filter_columns_and_sort() -> None:
    service = Service()

    _get(service, "2025-09-30", "2025-10-02", ["DK2", "DK1"])

    hourly = service.params("Elspotprices")
    quarter = service.params("DayAheadPrices")
    assert hourly["columns"] == "HourUTC,PriceArea,SpotPriceEUR"
    assert hourly["sort"] == "HourUTC asc"
    assert quarter["columns"] == "TimeUTC,PriceArea,DayAheadPriceEUR"
    assert quarter["sort"] == "TimeUTC asc"
    for params in (hourly, quarter):
        assert json.loads(params["filter"]) == {"PriceArea": ["DK2", "DK1"]}
        assert params["limit"] == "0"
        assert params["timezone"] == "UTC"


# --- validation ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end", "zones", "fragment"),
    [
        ("2026-01-15", "2026-01-15", ("DK1", "DK2"), "end"),
        ("2026-01-15", "2026-01-14", ("DK1", "DK2"), "end"),
        ("2026-01-15T12:07", None, ("DK1", "DK2"), "12:07"),
        ("2026-01-15T12:00", "2026-01-15T13:01", ("DK1", "DK2"), "13:01"),
        ("2026-03-29T02:30", None, ("DK1", "DK2"), "2026-03-29T02:30"),
        ("2026-10-25T02:30", None, ("DK1", "DK2"), "2026-10-25T02:30"),
        ("yesterday", None, ("DK1", "DK2"), "yesterday"),
        ("2026-01-15", None, ["DK3"], "DK3"),
        ("2026-01-15", None, "dk1", "dk1"),
        ("2026-01-15", None, [], "empty"),
        ("2026-01-15", None, ["DK1", "DK1"], "DK1"),
    ],
)
def test_day_ahead_rejects_bad_input_naming_the_value_and_makes_no_request(
    start: str, end: str | None, zones: object, fragment: str
) -> None:
    service = Service()

    with pytest.raises(ValueError, match=fragment):
        _get(service, start, end, cast(Any, zones))

    assert service.requests == []


def test_day_ahead_invalid_input_leaves_a_passed_client_open() -> None:
    service = Service()
    with _served(service) as client:
        with pytest.raises(ValueError):
            get_day_ahead_prices("2026-01-15", "2026-01-15", client=client)
        assert client.closed is False


# --- clients ------------------------------------------------------------------


def test_day_ahead_passed_client_is_left_open_and_reusable() -> None:
    service = Service(quarter=_quarters(QUARTER_DAY, "DK1", [1.0] * 96))
    with _served(service) as client:
        first = get_day_ahead_prices(QUARTER_DAY, bidding_zones="DK1", client=client)
        assert client.closed is False
        second = get_day_ahead_prices(QUARTER_DAY, bidding_zones="DK1", client=client)

    pd.testing.assert_frame_equal(first, second)


def test_day_ahead_is_idempotent_and_does_not_share_frames_between_calls() -> None:
    service = Service(quarter=_quarters(QUARTER_DAY, "DK1", [1.0] * 96))

    first = _get(service, QUARTER_DAY, bidding_zones="DK1")
    first.iloc[0, 0] = -5.0
    second = _get(service, QUARTER_DAY, bidding_zones="DK1")

    assert second["DK1"].iloc[0] == 1.0


def _patched_factory(
    monkeypatch: pytest.MonkeyPatch, service: Callable[[httpx.Request], httpx.Response]
) -> list[EnergiDataServiceClient]:
    made: list[EnergiDataServiceClient] = []

    def factory() -> EnergiDataServiceClient:
        client = EnergiDataServiceClient(
            transport=httpx.MockTransport(service), policy=FAST
        )
        made.append(client)
        return client

    monkeypatch.setattr(day_ahead, "EnergiDataServiceClient", factory)
    return made


def test_day_ahead_closes_a_client_it_created(monkeypatch: pytest.MonkeyPatch) -> None:
    made = _patched_factory(monkeypatch, Service())

    prices = get_day_ahead_prices(QUARTER_DAY)

    assert prices.shape == (96, 2)
    assert [client.closed for client in made] == [True]


def test_day_ahead_closes_its_own_client_when_a_dataset_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(400, json={"error": "bad"})

    made = _patched_factory(monkeypatch, refuse)

    with pytest.raises(httpx.HTTPStatusError):
        get_day_ahead_prices("2025-09-30", "2025-10-02")

    assert [client.closed for client in made] == [True]


def test_day_ahead_closes_its_own_client_when_validation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made = _patched_factory(monkeypatch, Service())

    with pytest.raises(ValueError):
        get_day_ahead_prices("2026-01-15", bidding_zones=cast(Any, ["DK9"]))

    assert all(client.closed for client in made)


@pytest.mark.parametrize("failing", ["Elspotprices", "DayAheadPrices"])
def test_day_ahead_one_failing_dataset_cancels_the_other_and_leaves_the_client_usable(
    failing: str,
) -> None:
    state = {"cancelled": False}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(failing):
            return httpx.Response(400, json={"error": "bad"})
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise
        return httpx.Response(200, json={"records": []})

    with EnergiDataServiceClient(
        transport=httpx.MockTransport(handler), policy=FAST
    ) as client:
        with pytest.raises(httpx.HTTPStatusError):
            get_day_ahead_prices("2025-09-30", "2025-10-02", client=client)

        assert state["cancelled"] is True
        assert client.closed is False
        assert client.run(_noop) == "ok"


async def _noop() -> str:
    return "ok"


def test_day_ahead_unexpected_payload_raises_the_service_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(200, json={"total": 5, "records": []})

    with (
        EnergiDataServiceClient(
            transport=httpx.MockTransport(handler), policy=FAST
        ) as client,
        pytest.raises(EnergiDataServiceError, match="DayAheadPrices"),
    ):
        get_day_ahead_prices(QUARTER_DAY, client=client)


def test_day_ahead_duplicate_records_raise_a_value_error_naming_the_zone() -> None:
    record = _quarter(_utc("2026-01-15 00:00"), "DK1", 1.0)
    service = Service(quarter=[record, dict(record)])

    with pytest.raises(ValueError, match="DK1"):
        _get(service, QUARTER_DAY, bidding_zones="DK1")


def test_day_ahead_works_inside_a_running_loop_and_across_windows() -> None:
    prices_in = [float(i) for i in range(96 * 3)]
    service = Service(quarter=_quarters("2026-01-15", "DK1", prices_in))

    async def inside() -> pd.DataFrame:
        with service.client(max_span=timedelta(days=1)) as client:
            return get_day_ahead_prices(
                "2026-01-15", "2026-01-18", "DK1", client=client
            )

    prices = asyncio.run(inside())

    assert len(prices) == 288
    assert prices["DK1"].tolist() == prices_in
    assert service.datasets() == ["DayAheadPrices"] * 3


def test_day_ahead_long_hourly_period_is_split_and_joined() -> None:
    hours = [float(i) for i in range(72)]
    service = Service(hourly=_hours("2025-01-15", "DK1", hours))

    with _served(service, max_span=timedelta(days=1)) as client:
        prices = get_day_ahead_prices("2025-01-15", "2025-01-18", "DK1", client=client)

    assert prices["DK1"].tolist() == [h for h in hours for _ in range(4)]
    assert len(service.requests) >= 3


# --- documentation: A8 --------------------------------------------------------


def test_day_ahead_docstring_names_the_output_contract() -> None:
    doc = get_day_ahead_prices.__doc__ or ""

    for fragment in (
        "EUR/MWh",
        "15 minutes",
        "wide",
        "DK1",
        "DK2",
        "DayAheadPrices",
        "Elspotprices",
        "2025-10-01",
        "NaN",
    ):
        assert fragment in doc


def test_readme_endpoint_row_names_the_output_contract() -> None:
    readme = (Path(__file__).parent.parent / "README.md").read_text(encoding="utf-8")
    row = next(
        line
        for line in readme.splitlines()
        if "get_day_ahead_prices(" in line and "|" in line
    )

    for fragment in (
        "EUR/MWh",
        "15 minutes",
        "wide",
        "DK1",
        "DK2",
        "DayAheadPrices",
        "Elspotprices",
    ):
        assert fragment in row


def test_utils_modules_name_no_energi_data_service_specifics() -> None:
    root = Path(__file__).parent.parent / "src" / "energydata" / "utils"
    banned = (
        "energidataservice",
        "Elspotprices",
        "DayAheadPrices",
        "SpotPriceEUR",
        "HourUTC",
        "PriceArea",
    )
    sources = {p: p.read_text(encoding="utf-8") for p in root.glob("*.py")}

    assert sources
    for path, text in sources.items():
        assert not [w for w in banned if w.lower() in text.lower()], path.name


def test_day_ahead_main_showcase_runs_against_a_stub(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patched_factory(monkeypatch, Service())

    day_ahead.main()

    out = capsys.readouterr().out
    assert "96 quarter-hours" in out
    assert "across the switch" in out
