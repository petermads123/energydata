from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import NamedTuple

import pandas as pd
import pytest
from conftest import ADDRESS_ID, MinStroemService

from energydata.minstroem import (
    AGGREGATIONS,
    API_KEY_ENV,
    Aggregation,
    _series,
    get_consumption,
    get_solar_export,
    usage,
)
from energydata.utils import TimeLike

CPH = "Europe/Copenhagen"
FORMAT = "%Y-%m-%dT%H:%M:%SZ"

type Fetch = Callable[..., pd.DataFrame]


class Case(NamedTuple):
    """One of the two usage endpoints."""

    fn: Fetch
    suffix: str
    capability: str


CASES = [
    pytest.param(
        Case(get_consumption, "/consumption", "consumption"), id="consumption"
    ),
    pytest.param(
        Case(get_solar_export, "/solar-export", "solarExport"), id="solar-export"
    ),
]


def _row(
    start: str, energy: float, *, estimate: bool, hours: int = 1
) -> dict[str, object]:
    begin = datetime.strptime(start, FORMAT).replace(tzinfo=UTC)
    return {
        "start": start,
        "end": (begin + timedelta(hours=hours)).strftime(FORMAT),
        "energyKWh": energy,
        "isEstimate": estimate,
    }


@pytest.mark.parametrize("case", CASES)
def test_columns_dtypes_index_and_attrs_are_the_documented_ones(
    minstroem_service: MinStroemService, case: Case
) -> None:
    with minstroem_service.client() as c:
        frame = case.fn("2026-11-02", address_id=ADDRESS_ID, client=c)

    assert list(frame.columns) == ["energy_kwh", "is_estimate"]
    assert str(frame["energy_kwh"].dtype) == "float64"
    assert str(frame["is_estimate"].dtype) == "boolean"
    assert str(frame.index.dtype) == f"datetime64[ns, {CPH}]"
    assert frame.index.name == "time"
    assert frame.attrs == {"address_id": ADDRESS_ID, "aggregation": "hour"}
    assert len(frame) == 24
    assert frame["energy_kwh"].notna().all()
    assert (frame["is_estimate"] == False).all()  # noqa: E712 - a boolean-array compare


@pytest.mark.parametrize("case", CASES)
def test_values_and_estimate_flags_land_on_their_own_interval(
    minstroem_service: MinStroemService, case: Case
) -> None:
    minstroem_service.rows[case.suffix.strip("/")] = [
        _row("2026-11-01T23:00:00Z", 0.5, estimate=False),
        _row("2026-11-02T00:00:00Z", 1.5, estimate=True),
        _row("2026-11-02T02:00:00Z", 2.5, estimate=False),
    ]

    with minstroem_service.client() as c:
        frame = case.fn(
            "2026-11-02", "2026-11-02T05:00", address_id=ADDRESS_ID, client=c
        )

    assert frame["energy_kwh"].iloc[:4].tolist()[:2] == [0.5, 1.5]
    assert pd.isna(frame["energy_kwh"].iloc[2])
    assert frame["energy_kwh"].iloc[3] == 2.5
    assert frame["is_estimate"].tolist() == [False, True, pd.NA, False, pd.NA]


@pytest.mark.parametrize("case", CASES)
def test_no_rows_at_all_is_nan_and_na_never_zero(
    minstroem_service: MinStroemService, case: Case
) -> None:
    minstroem_service.rows[case.suffix.strip("/")] = []

    with minstroem_service.client() as c:
        frame = case.fn("2026-11-02", address_id=ADDRESS_ID, client=c)

    assert len(frame) == 24
    assert frame["energy_kwh"].isna().all()
    assert frame["is_estimate"].isna().all()
    assert str(frame["energy_kwh"].dtype) == "float64"
    assert str(frame["is_estimate"].dtype) == "boolean"


@pytest.mark.parametrize("case", CASES)
def test_a_zero_reading_is_kept_as_zero(
    minstroem_service: MinStroemService, case: Case
) -> None:
    minstroem_service.rows[case.suffix.strip("/")] = [
        _row("2026-11-01T23:00:00Z", 0.0, estimate=False)
    ]

    with minstroem_service.client() as c:
        frame = case.fn("2026-11-02", address_id=ADDRESS_ID, client=c)

    assert frame["energy_kwh"].iloc[0] == 0.0
    assert frame["is_estimate"].iloc[0] == False  # noqa: E712 - a boolean-array compare
    assert frame["energy_kwh"].iloc[1:].isna().all()


@pytest.mark.parametrize("case", CASES)
def test_each_endpoint_requests_only_its_own_path(
    minstroem_service: MinStroemService, case: Case
) -> None:
    with minstroem_service.client() as c:
        case.fn("2026-11-02", address_id=ADDRESS_ID, client=c)

    other = "/solar-export" if case.suffix == "/consumption" else "/consumption"
    assert len(minstroem_service.calls(case.suffix)) == 1
    assert minstroem_service.calls(other) == []


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize(
    ("aggregation", "start", "end", "rows", "step_hours"),
    [
        ("hour", "2026-11-02", "2026-11-03", 24, [1]),
        ("day", "2026-03-28", "2026-03-31", 3, [24, 23]),
        ("day", "2026-10-24", "2026-10-27", 3, [24, 25]),
        ("month", "2026-01-01", "2027-01-01", 12, None),
        ("year", "2024-01-01", "2027-01-01", 3, None),
    ],
)
def test_every_aggregation_gives_one_row_per_calendar_interval(
    minstroem_service: MinStroemService,
    case: Case,
    aggregation: Aggregation,
    start: str,
    end: str,
    rows: int,
    step_hours: list[int] | None,
) -> None:
    with minstroem_service.client() as c:
        frame = case.fn(
            start, end, aggregation=aggregation, address_id=ADDRESS_ID, client=c
        )

    assert len(frame) == rows
    assert frame["energy_kwh"].notna().all()
    assert frame.attrs["aggregation"] == aggregation
    assert minstroem_service.calls(case.suffix) == [
        {
            "from": pd.Timestamp(start, tz=CPH).tz_convert("UTC").strftime(FORMAT),
            "to": pd.Timestamp(end, tz=CPH).tz_convert("UTC").strftime(FORMAT),
            "aggregation": aggregation,
        }
    ]
    index = pd.DatetimeIndex(frame.index)
    if aggregation != "hour":
        assert list(index.hour) == [0] * rows
        assert list(index.minute) == [0] * rows
    if step_hours is not None and aggregation == "day":
        gaps = [
            (b - a) // timedelta(hours=1)
            for a, b in zip(index, index[1:], strict=False)
        ]
        assert gaps == step_hours


def test_a_month_starts_on_the_first_in_local_time_across_the_summer_change(
    minstroem_service: MinStroemService,
) -> None:
    with minstroem_service.client() as c:
        frame = get_consumption(
            "2026-01-01",
            "2027-01-01",
            aggregation="month",
            address_id=ADDRESS_ID,
            client=c,
        )

    assert frame.index[3] == pd.Timestamp("2026-04-01", tz=CPH)
    assert frame.index[3].utcoffset() == timedelta(hours=2)
    assert frame.index[10] == pd.Timestamp("2026-11-01", tz=CPH)
    assert frame.index[10].utcoffset() == timedelta(hours=1)


@pytest.mark.parametrize("case", CASES)
def test_the_aggregation_defaults_to_hour_and_is_keyword_only(
    minstroem_service: MinStroemService, case: Case
) -> None:
    with minstroem_service.client() as c:
        frame = case.fn("2026-11-02", address_id=ADDRESS_ID, client=c)
        with pytest.raises(TypeError):
            case.fn("2026-11-02", "2026-11-03", "hour", client=c)

    assert frame.attrs["aggregation"] == "hour"
    assert minstroem_service.calls(case.suffix)[0]["aggregation"] == "hour"


def test_the_public_aggregation_values_are_the_apis_spelling() -> None:
    assert AGGREGATIONS == ("hour", "day", "month", "year")


@pytest.mark.parametrize(
    ("start", "end", "rows", "first"),
    [
        ("2026-11-02", None, 24, "2026-11-02 00:00"),
        (date(2026, 11, 2), None, 24, "2026-11-02 00:00"),
        ("2026-11-02T10:00", None, 1, "2026-11-02 10:00"),
        ("2026-11-02", "2026-11-02T06:00", 6, "2026-11-02 00:00"),
        ("2026-11-02T22:00", "2026-11-03T02:00", 4, "2026-11-02 22:00"),
        # Aware inputs are converted: 00:00 UTC is 01:00 in Copenhagen in November.
        (
            datetime(2026, 11, 2, tzinfo=UTC),
            datetime(2026, 11, 2, 3, tzinfo=UTC),
            3,
            "2026-11-02 01:00",
        ),
        (pd.Timestamp("2026-11-02 05:00", tz=CPH), None, 1, "2026-11-02 05:00"),
    ],
)
def test_start_and_end_are_half_open_and_naive_means_danish_time(
    minstroem_service: MinStroemService,
    start: TimeLike,
    end: TimeLike | None,
    rows: int,
    first: str,
) -> None:
    with minstroem_service.client() as c:
        frame = get_consumption(start, end, address_id=ADDRESS_ID, client=c)

    assert len(frame) == rows
    assert frame.index[0] == pd.Timestamp(first, tz=CPH)
    assert frame.index[-1] == frame.index[0] + pd.Timedelta(hours=rows - 1)


def test_the_end_hour_itself_is_not_in_the_result(
    minstroem_service: MinStroemService,
) -> None:
    with minstroem_service.client() as c:
        frame = get_consumption(
            "2026-11-02", "2026-11-03", address_id=ADDRESS_ID, client=c
        )

    assert frame.index[-1] == pd.Timestamp("2026-11-02 23:00", tz=CPH)
    assert pd.Timestamp("2026-11-03 00:00", tz=CPH) not in frame.index


@pytest.mark.parametrize(
    ("aggregation", "start", "end", "windows"),
    [
        ("hour", "2025-01-01", "2026-03-01", 2),
        ("day", "2020-01-01", "2026-01-01", 2),
        ("month", "2020-01-01", "2026-01-01", 2),
        ("year", "2019-01-01", "2026-01-01", 2),
    ],
)
def test_a_long_period_is_split_and_joined_without_a_hole_or_a_repeat(
    minstroem_service: MinStroemService,
    aggregation: Aggregation,
    start: str,
    end: str,
    windows: int,
) -> None:
    with minstroem_service.client() as c:
        frame = get_solar_export(
            start, end, aggregation=aggregation, address_id=ADDRESS_ID, client=c
        )

    calls = sorted(minstroem_service.calls("/solar-export"), key=lambda q: q["from"])
    assert len(calls) == windows
    assert calls[0]["to"] == calls[1]["from"]
    assert frame.index.is_unique
    assert frame.index.is_monotonic_increasing
    assert frame["energy_kwh"].notna().all()


def test_main_without_a_key_prints_one_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(API_KEY_ENV, raising=False)

    usage.main()

    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    assert API_KEY_ENV in lines[0]


def test_main_with_a_key_shows_daily_consumption_and_hourly_solar_export(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    minstroem_service: MinStroemService,
) -> None:
    monkeypatch.setenv(API_KEY_ENV, "test-key")
    monkeypatch.setattr(_series, "MinStroemClient", minstroem_service.client)

    usage.main()

    out = capsys.readouterr().out
    assert "consumption per day: (7, 2)" in out
    assert "solar export per hour: (24, 2)" in out
