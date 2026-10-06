import copy
from datetime import timedelta
from typing import Literal

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from energydata.utils import (
    block_index,
    combine_levels,
    conform,
    expand_to_resolution,
    period_index,
    records_to_wide,
)

CPH = "Europe/Copenhagen"
Q = timedelta(minutes=15)
H = timedelta(hours=1)
NS_CPH = "datetime64[ns, Europe/Copenhagen]"


def _ts(text: str, tz: str = CPH) -> pd.Timestamp:
    return pd.Timestamp(text, tz=tz)


def _wide(rows: list[tuple[str, float | None]], column: str = "DK1") -> pd.DataFrame:
    return records_to_wide(
        [{"t": t, "a": column, "v": v} for t, v in rows],
        time="t",
        column="a",
        value="v",
    )


# --- period_index -------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "slots"),
    [("2025-01-15", 96), ("2025-03-30", 92), ("2025-10-26", 100)],
)
def test_period_index_counts_slots_across_dst(day: str, slots: int) -> None:
    start = _ts(day)
    end = pd.Timestamp(pd.Timestamp(day) + pd.Timedelta(days=1), tz=CPH)

    index = period_index(start, end, Q)

    assert len(index) == slots
    assert index.is_unique
    assert index.is_monotonic_increasing
    steps = index.tz_convert("UTC").to_series().diff().dropna()
    assert set(steps) == {pd.Timedelta(Q)}


def test_period_index_is_half_open_named_and_nanosecond() -> None:
    index = period_index(_ts("2025-01-15 00:00"), _ts("2025-01-15 01:00"), Q)

    assert len(index) == 4
    assert index[0] == _ts("2025-01-15 00:00")
    assert index[-1] == _ts("2025-01-15 00:45")
    assert index.name == "time"
    assert str(index.dtype) == NS_CPH


def test_period_index_one_slot_minimum_excludes_end() -> None:
    start = _ts("2025-01-01")

    index = period_index(start, start + pd.Timedelta(Q), Q)

    assert list(index) == [start]


def test_period_index_does_not_require_an_aligned_start() -> None:
    start = _ts("2025-01-01 00:05")

    index = period_index(start, start + pd.Timedelta(minutes=30), Q)

    assert list(index) == [start, start + pd.Timedelta(Q)]


def test_period_index_uses_start_zone_when_end_zone_differs() -> None:
    start = _ts("2025-01-15 00:00")
    end = pd.Timestamp("2025-01-15 00:00", tz="UTC")  # one hour later than start

    index = period_index(start, end, Q)

    assert len(index) == 4
    assert str(index.dtype) == NS_CPH


@pytest.mark.parametrize(
    ("start", "end", "resolution", "named"),
    [
        (pd.Timestamp("2025-01-01"), _ts("2025-01-02"), Q, "start"),
        (_ts("2025-01-01"), pd.Timestamp("2025-01-02"), Q, "end"),
        (_ts("2025-01-01"), _ts("2025-01-01"), Q, "start must be before end"),
        (_ts("2025-01-02"), _ts("2025-01-01"), Q, "start must be before end"),
        (_ts("2025-01-01"), _ts("2025-01-01 01:00"), timedelta(minutes=7), "divisor"),
        (_ts("2025-01-01"), _ts("2025-01-01 04:00"), timedelta(hours=2), "divisor"),
        (_ts("2025-01-01"), _ts("2025-01-01 01:00"), timedelta(0), "divisor"),
        (_ts("2025-01-01"), _ts("2025-01-01 01:00"), -Q, "divisor"),
        (_ts("2025-01-01"), _ts("2025-01-01 00:10"), Q, "whole multiple"),
    ],
)
def test_period_index_rejects_invalid_arguments(
    start: pd.Timestamp, end: pd.Timestamp, resolution: timedelta, named: str
) -> None:
    with pytest.raises(ValueError, match=named):
        period_index(start, end, resolution)


def test_period_index_is_idempotent() -> None:
    start, end = _ts("2025-01-01"), _ts("2025-01-02")

    assert period_index(start, end, Q).equals(period_index(start, end, Q))


# --- records_to_wide ----------------------------------------------------------


def test_records_to_wide_reads_naive_strings_as_utc_and_converts() -> None:
    wide = records_to_wide(
        [
            {"t": "2025-01-15T11:00:00", "a": "DK1", "v": 10},
            {"t": "2025-01-15T11:00:00", "a": "DK2", "v": None},
        ],
        time="t",
        column="a",
        value="v",
    )

    assert list(wide.index) == [_ts("2025-01-15 12:00")]
    assert wide.index.name == "time"
    assert str(wide.index.dtype) == NS_CPH
    assert list(wide.columns) == ["DK1", "DK2"]
    assert (wide.dtypes == "float64").all()
    assert wide.loc[wide.index[0], "DK1"] == 10.0
    assert np.isnan(wide.loc[wide.index[0], "DK2"])


def test_records_to_wide_accepts_offsets_and_z_suffix() -> None:
    wide = _wide([("2025-01-15T11:00:00Z", 1.0), ("2025-01-15T13:00:00+01:00", 2.0)])

    assert list(wide.index) == [_ts("2025-01-15 12:00"), _ts("2025-01-15 13:00")]


def test_records_to_wide_accepts_mixed_iso_precision() -> None:
    wide = _wide([("2025-01-15T11:00:00", 1.0), ("2025-01-15T12:00", 2.0)])

    assert len(wide) == 2


def test_records_to_wide_none_value_and_missing_cell_are_nan() -> None:
    records: list[dict[str, object]] = [
        {"t": "2025-01-15T00:00:00", "a": "DK1", "v": None},
        {"t": "2025-01-15T01:00:00", "a": "DK2", "v": 2},
    ]

    wide = records_to_wide(records, time="t", column="a", value="v")

    assert wide.shape == (2, 2)
    assert wide.isna().to_numpy().tolist() == [[True, True], [True, False]]


def test_records_to_wide_sorts_index_and_columns() -> None:
    records: list[dict[str, object]] = [
        {"t": "2025-01-15T02:00:00", "a": "DK2", "v": 1},
        {"t": "2025-01-15T00:00:00", "a": "DK1", "v": 2},
    ]

    wide = records_to_wide(records, time="t", column="a", value="v")

    assert wide.index.is_monotonic_increasing
    assert list(wide.columns) == ["DK1", "DK2"]


def test_records_to_wide_empty_gives_a_tz_aware_empty_frame() -> None:
    wide = records_to_wide([], time="t", column="a", value="v")

    assert wide.shape == (0, 0)
    assert str(wide.index.dtype) == NS_CPH
    assert wide.index.name == "time"


def test_records_to_wide_converts_to_the_requested_zone() -> None:
    wide = records_to_wide(
        [{"t": "2025-01-15T11:00:00", "a": "DK1", "v": 1}],
        time="t",
        column="a",
        value="v",
        tz="UTC",
    )

    assert str(wide.index.dtype) == "datetime64[ns, UTC]"


def test_records_to_wide_rejects_a_duplicate_pair_naming_it() -> None:
    with pytest.raises(ValueError, match="duplicate") as caught:
        _wide([("2025-01-15T11:00:00", 1.0), ("2025-01-15T11:00:00", 2.0)])

    assert "'DK1'" in str(caught.value)


def test_records_to_wide_detects_duplicates_after_parsing_not_by_string() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        _wide([("2025-01-01T00:00:00Z", 1.0), ("2025-01-01T01:00:00+01:00", 2.0)])


def test_records_to_wide_same_time_in_two_columns_is_not_a_duplicate() -> None:
    records: list[dict[str, object]] = [
        {"t": "2025-01-15T11:00:00", "a": "DK1", "v": 1},
        {"t": "2025-01-15T11:00:00", "a": "DK2", "v": 2},
    ]

    assert records_to_wide(records, time="t", column="a", value="v").shape == (1, 2)


@pytest.mark.parametrize("field", ["t", "a", "v"])
def test_records_to_wide_rejects_a_missing_field_naming_it_and_the_record(
    field: str,
) -> None:
    good: dict[str, object] = {"t": "2025-01-15T11:00:00", "a": "DK1", "v": 1}
    bad = {key: val for key, val in good.items() if key != field}

    with pytest.raises(ValueError) as caught:
        records_to_wide([good, bad], time="t", column="a", value="v")

    assert "record 1" in str(caught.value)
    assert repr(field) in str(caught.value)


@pytest.mark.parametrize("missing", [None, ""])
def test_records_to_wide_rejects_an_empty_time_naming_the_field(
    missing: str | None,
) -> None:
    with pytest.raises(ValueError, match="'t'"):
        _wide([("2025-01-15T11:00:00", 1.0), (missing, 2.0)])  # type: ignore[list-item]  # malformed on purpose


@pytest.mark.parametrize("text", ["not a time", "2025-13-45T00:00:00", 12345])
def test_records_to_wide_rejects_an_unparseable_time(text: object) -> None:
    records: list[dict[str, object]] = [{"t": text, "a": "DK1", "v": 1.0}]

    with pytest.raises(ValueError, match="'t'"):
        records_to_wide(records, time="t", column="a", value="v")


def test_records_to_wide_rejects_a_non_numeric_value_naming_the_field() -> None:
    records: list[dict[str, object]] = [
        {"t": "2025-01-15T11:00:00", "a": "DK1", "v": "abc"}
    ]

    with pytest.raises(ValueError, match="'v'"):
        records_to_wide(records, time="t", column="a", value="v")


def test_records_to_wide_does_not_mutate_its_records() -> None:
    records: list[dict[str, object]] = [
        {"t": "2025-01-15T11:00:00", "a": "DK1", "v": 1},
        {"t": "2025-01-15T12:00:00", "a": "DK2", "v": None},
    ]
    before = copy.deepcopy(records)

    records_to_wide(records, time="t", column="a", value="v")

    assert records == before


def test_records_to_wide_ignores_extra_fields_and_keeps_non_ascii_column_names() -> (
    None
):
    records: list[dict[str, object]] = [
        {"t": "2025-01-15T11:00:00", "a": "Århus", "v": 1, "other": "x"}
    ]

    wide = records_to_wide(records, time="t", column="a", value="v")

    assert list(wide.columns) == ["Århus"]


# --- expand_to_resolution -----------------------------------------------------


def test_expand_to_resolution_repeats_each_hour_into_four_quarters() -> None:
    hourly = _wide([("2025-01-15T00:00:00", 1.0), ("2025-01-15T01:00:00", 2.0)])

    quarters = expand_to_resolution(hourly, H, Q)

    assert len(quarters) == 8
    assert quarters["DK1"].tolist() == [1.0] * 4 + [2.0] * 4
    assert quarters.index[0] == _ts("2025-01-15 01:00")
    assert quarters.index[-1] == _ts("2025-01-15 02:45")
    assert quarters.index.name == "time"
    assert str(quarters.index.dtype) == NS_CPH


def test_expand_to_resolution_a_gap_stays_a_gap() -> None:
    hourly = _wide([("2025-01-14T23:00:00", 1.0), ("2025-01-15T01:00:00", 3.0)])

    quarters = expand_to_resolution(hourly, H, Q)
    full = conform(
        quarters,
        period_index(_ts("2025-01-15 00:00"), _ts("2025-01-15 03:00"), Q),
        ["DK1"],
    )

    assert len(quarters) == 8
    assert full["DK1"].iloc[4:8].isna().all()
    assert full["DK1"].iloc[:4].tolist() == [1.0] * 4
    assert full["DK1"].iloc[8:].tolist() == [3.0] * 4


def test_expand_to_resolution_across_the_autumn_dst_hour_gives_every_slot_once() -> (
    None
):
    hours = pd.date_range("2025-10-25T22:00Z", periods=25, freq="h")
    hourly = _wide([(t.isoformat(), float(i)) for i, t in enumerate(hours)])

    quarters = expand_to_resolution(hourly, H, Q)

    expected = period_index(_ts("2025-10-26"), _ts("2025-10-27"), Q)
    assert quarters.index.equals(expected)
    assert quarters["DK1"].iloc[8:12].tolist() == [2.0] * 4  # first 02:00-02:45
    assert quarters["DK1"].iloc[12:16].tolist() == [3.0] * 4  # repeated 02:00-02:45


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (Q, H),
        (H, timedelta(minutes=25)),
        (H, timedelta(0)),
        (timedelta(0), Q),
        (-H, Q),
        (H, -Q),
    ],
)
def test_expand_to_resolution_rejects_a_bad_ratio(
    source: timedelta, target: timedelta
) -> None:
    hourly = _wide([("2025-01-15T00:00:00", 1.0)])

    with pytest.raises(ValueError, match="whole multiple"):
        expand_to_resolution(hourly, source, target)


def test_expand_to_resolution_equal_source_and_target_is_the_identity() -> None:
    frame = _wide([("2025-01-15T00:00:00", 1.0), ("2025-01-15T00:15:00", 2.0)])

    assert_frame_equal(expand_to_resolution(frame, Q, Q), frame)


def test_expand_to_resolution_empty_frame_stays_empty_and_tz_aware() -> None:
    empty = records_to_wide([], time="t", column="a", value="v")

    result = expand_to_resolution(empty, H, Q)

    assert len(result) == 0
    assert str(result.index.dtype) == NS_CPH


def test_expand_to_resolution_keeps_nan_values_in_their_hours() -> None:
    frame = _wide([("2025-01-15T00:00:00", None), ("2025-01-15T01:00:00", 5.0)])

    result = expand_to_resolution(frame, H, Q)

    assert result["DK1"].isna().tolist() == [True] * 4 + [False] * 4


def test_expand_to_resolution_does_not_mutate_its_input() -> None:
    frame = _wide([("2025-01-15T00:00:00", 1.0)])
    before = frame.copy()

    expand_to_resolution(frame, H, Q)

    assert_frame_equal(frame, before)
    assert len(frame) == 1


# --- conform ------------------------------------------------------------------


def test_conform_pads_orders_and_drops() -> None:
    frame = pd.DataFrame(
        {"DK1": [1.0, 2.0, 9.0], "DK2": [3.0, 4.0, 9.0], "X": [7.0, 7.0, 7.0]},
        index=pd.DatetimeIndex(
            [_ts("2025-01-15 00:00"), _ts("2025-01-15 00:15"), _ts("2025-01-15 05:00")],
            name="time",
        ).as_unit("ns"),
    )
    index = period_index(_ts("2025-01-15 00:00"), _ts("2025-01-15 01:00"), Q)

    result = conform(frame, index, ["DK2", "DK1"])

    assert result.index.equals(index)
    assert str(result.index.dtype) == NS_CPH
    assert list(result.columns) == ["DK2", "DK1"]
    assert (result.dtypes == "float64").all()
    assert result["DK2"].tolist()[:2] == [3.0, 4.0]
    assert result.iloc[2:].isna().all().all()
    assert "X" not in result.columns


def test_conform_adds_a_missing_column_as_all_nan() -> None:
    frame = _wide([("2025-01-15T11:00:00", 1.0)])
    index = period_index(_ts("2025-01-15 12:00"), _ts("2025-01-15 13:00"), Q)

    result = conform(frame, index, ["DK1", "DK2"])

    assert result["DK2"].isna().all()


def test_conform_never_fills_from_a_neighbour() -> None:
    frame = pd.DataFrame(
        {"DK1": [1.0, 4.0]},
        index=pd.DatetimeIndex(
            [_ts("2025-01-15 00:00"), _ts("2025-01-15 00:45")], name="time"
        ).as_unit("ns"),
    )
    index = period_index(_ts("2025-01-15 00:00"), _ts("2025-01-15 01:00"), Q)

    result = conform(frame, index, ["DK1"])

    assert result["DK1"].isna().tolist() == [False, True, True, False]


def test_conform_empty_frame_gives_all_nan_of_the_right_shape() -> None:
    empty = records_to_wide([], time="t", column="a", value="v")
    index = period_index(_ts("2030-01-01"), _ts("2030-01-02"), Q)

    result = conform(empty, index, ["DK1", "DK2"])

    assert result.shape == (96, 2)
    assert result.isna().all().all()
    assert (result.dtypes == "float64").all()
    assert result.index.equals(index)


@pytest.mark.parametrize("unit", ["s", "us"])
def test_conform_matches_across_index_units(unit: Literal["s", "us"]) -> None:
    index = period_index(_ts("2025-01-15 00:00"), _ts("2025-01-15 01:00"), Q)
    frame = pd.DataFrame({"DK1": [1.0, 2.0, 3.0, 4.0]}, index=index.as_unit(unit))

    result = conform(frame, index, ["DK1"])

    assert result["DK1"].tolist() == [1.0, 2.0, 3.0, 4.0]


def test_conform_matches_across_time_zones() -> None:
    index = period_index(_ts("2025-01-15 00:00"), _ts("2025-01-15 01:00"), Q)
    frame = pd.DataFrame({"DK1": [1.0, 2.0, 3.0, 4.0]}, index=index.tz_convert("UTC"))

    result = conform(frame, index, ["DK1"])

    assert result["DK1"].tolist() == [1.0, 2.0, 3.0, 4.0]


def test_conform_rejects_a_duplicate_index_entry_naming_it() -> None:
    stamp = _ts("2025-01-15 00:00")
    frame = pd.DataFrame({"DK1": [1.0, 2.0]}, index=pd.DatetimeIndex([stamp, stamp]))
    index = period_index(stamp, _ts("2025-01-15 01:00"), Q)

    with pytest.raises(ValueError, match="duplicate") as caught:
        conform(frame, index, ["DK1"])

    assert "2025-01-15" in str(caught.value)


def test_conform_casts_integer_columns_to_float64() -> None:
    index = period_index(_ts("2025-01-15 00:00"), _ts("2025-01-15 00:30"), Q)
    frame = pd.DataFrame({"DK1": [1, 2]}, index=index)

    result = conform(frame, index, ["DK1"])

    assert result["DK1"].dtype == "float64"


def test_conform_with_no_columns_keeps_the_index() -> None:
    index = period_index(_ts("2025-01-15 00:00"), _ts("2025-01-15 00:30"), Q)

    result = conform(_wide([("2025-01-15T00:00:00", 1.0)]), index, [])

    assert result.shape == (2, 0)


def test_conform_does_not_mutate_its_input_and_is_idempotent() -> None:
    index = period_index(_ts("2025-01-15 00:00"), _ts("2025-01-15 01:00"), Q)
    frame = pd.DataFrame({"DK1": [1.0, 2.0, 3.0, 4.0], "X": 0.0}, index=index)
    before = frame.copy()

    once = conform(frame, index, ["DK1"])
    twice = conform(once, index, ["DK1"])

    assert_frame_equal(frame, before)
    assert_frame_equal(once, twice)


def test_conform_naive_frame_index_against_aware_index_is_refused_or_all_nan() -> None:
    index = period_index(_ts("2025-01-15 00:00"), _ts("2025-01-15 00:30"), Q)
    frame = pd.DataFrame(
        {"DK1": [1.0, 2.0]},
        index=pd.DatetimeIndex(["2025-01-15 00:00", "2025-01-15 00:15"]),
    )

    try:
        result = conform(frame, index, ["DK1"])
    except (TypeError, ValueError):
        return

    assert result["DK1"].isna().all()  # never a silent wrong match


# --- block_index --------------------------------------------------------------


def _next_midnight(day: str) -> pd.Timestamp:
    """Local midnight after `day`, whatever the day's length."""
    return pd.Timestamp(
        (pd.Timestamp(day) + pd.Timedelta(days=1)).date().isoformat(), tz=CPH
    )


def _hours(index: pd.DatetimeIndex) -> list[int]:
    return [stamp.hour for stamp in index]


@pytest.mark.parametrize(
    ("day", "elapsed"),
    [("2026-03-29", 3), ("2026-09-15", 4), ("2026-10-25", 5)],
)
def test_block_index_gives_six_wall_clock_blocks_on_every_kind_of_day(
    day: str, elapsed: int
) -> None:
    index = block_index(_ts(day), _next_midnight(day), 4)

    assert _hours(index) == [0, 4, 8, 12, 16, 20]
    assert index[1] - index[0] == pd.Timedelta(hours=elapsed)


def test_block_index_is_half_open_named_time_and_in_nanoseconds() -> None:
    index = block_index(_ts("2026-09-15"), _ts("2026-09-15T08:00"), 4)

    assert list(index) == [_ts("2026-09-15"), _ts("2026-09-15T04:00")]
    assert index.name == "time"
    assert str(index.dtype) == NS_CPH


def test_block_index_returns_nanoseconds_for_a_coarse_unit_input() -> None:
    start = _ts("2026-09-15").as_unit("s")
    end = _ts("2026-09-16").as_unit("s")

    index = block_index(start, end, 4)

    assert str(index.dtype) == NS_CPH
    assert index.name == "time"


def test_block_index_spans_several_days() -> None:
    index = block_index(_ts("2026-03-28"), _ts("2026-03-31"), 4)

    assert len(index) == 18


def test_block_index_converts_end_to_the_start_zone_before_checking_it() -> None:
    index = block_index(_ts("2026-09-15"), _ts("2026-09-15T22:00", "UTC"), 4)

    assert _hours(index) == [0, 4, 8, 12, 16, 20]
    assert str(index.tz) == CPH
    with pytest.raises(ValueError, match="end must be the start of a 4-hour block"):
        block_index(_ts("2026-09-15"), _ts("2026-09-16", "UTC"), 4)


@pytest.mark.parametrize(
    ("day", "hours", "count"),
    [
        ("2026-03-29", 1, 23),
        ("2026-10-25", 1, 24),
        ("2026-03-29", 2, 11),
        ("2026-10-25", 2, 12),
        ("2026-03-29", 4, 6),
        ("2026-10-25", 4, 6),
        ("2026-09-15", 24, 1),
    ],
)
def test_block_index_counts_blocks_across_dst_days(
    day: str, hours: int, count: int
) -> None:
    index = block_index(_ts(day), _next_midnight(day), hours)

    assert len(index) == count


def test_block_index_keeps_the_repeated_autumn_hour_once_at_the_earlier_stamp() -> None:
    index = block_index(_ts("2026-10-25"), _ts("2026-10-26"), 1)

    twos = [stamp for stamp in index if stamp.hour == 2]
    assert [stamp.utcoffset() for stamp in twos] == [timedelta(hours=2)]


def test_block_index_skips_a_start_inside_the_spring_gap() -> None:
    index = block_index(_ts("2026-03-29"), _ts("2026-03-30"), 2)

    assert 2 not in _hours(index)
    assert _hours(index)[:2] == [0, 4]


@pytest.mark.parametrize("offset", ["+01:00", "+02:00"])
def test_block_index_start_on_either_repeated_hour_survives(offset: str) -> None:
    start = pd.Timestamp(f"2026-10-25T02:00{offset}").tz_convert(CPH)

    index = block_index(start, _ts("2026-10-25T04:00"), 2)

    assert list(index) == [start]


@pytest.mark.parametrize("which", ["start", "end"])
@pytest.mark.parametrize("fraction", [".000001", ".5", ".000000001"])
def test_block_index_rejects_a_bound_with_a_sub_second_part(
    which: str, fraction: str
) -> None:
    bounds = {"start": _ts("2026-09-15"), "end": _ts("2026-09-16")}
    bounds[which] = pd.Timestamp(
        bounds[which].tz_localize(None).isoformat() + fraction, tz=CPH
    )

    with pytest.raises(ValueError, match=f"{which} must be the start of a 4-hour"):
        block_index(bounds["start"], bounds["end"], 4)


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        (pd.Timestamp("2026-09-15"), _ts("2026-09-16"), "start must be timezone-aware"),
        (_ts("2026-09-15"), pd.Timestamp("2026-09-16"), "end must be timezone-aware"),
        (_ts("2026-09-15"), _ts("2026-09-15"), "start must be before end"),
        (_ts("2026-09-16"), _ts("2026-09-15"), "start must be before end"),
        (_ts("2026-09-15T01:00"), _ts("2026-09-16"), "start must be the start of a"),
        (_ts("2026-09-15"), _ts("2026-09-15T06:00"), "end must be the start of a"),
        (_ts("2026-09-15T04:30"), _ts("2026-09-16"), "start must be the start of a"),
    ],
)
def test_block_index_rejects_bad_bounds(
    start: pd.Timestamp, end: pd.Timestamp, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        block_index(start, end, 4)


@pytest.mark.parametrize("hours", [0, -4, 5, 7, 25, 48])
def test_block_index_rejects_hours_that_do_not_divide_a_day(hours: int) -> None:
    with pytest.raises(ValueError, match=f"got {hours}"):
        block_index(_ts("2026-09-15"), _ts("2026-09-16"), hours)


def test_block_index_is_idempotent_and_leaves_its_bounds_alone() -> None:
    start, end = _ts("2026-09-15"), _ts("2026-09-16")

    first = block_index(start, end, 4)
    second = block_index(start, end, 4)

    assert first.equals(second)
    assert (start, end) == (_ts("2026-09-15"), _ts("2026-09-16"))


# --- combine_levels -----------------------------------------------------------


def _grid(hours: int = 3) -> pd.DatetimeIndex:
    return period_index(
        _ts("2026-09-15"), _ts("2026-09-15") + pd.Timedelta(hours=hours), H
    )


def _zones(values: dict[str, float | None], hours: int = 3) -> pd.DataFrame:
    """A wide frame over `_grid(hours)` with a constant column per zone."""
    index = _grid(hours)
    return pd.DataFrame(
        {zone: [value] * len(index) for zone, value in values.items()},
        index=index,
        dtype="float64",
    )


def test_combine_levels_orders_outer_first_then_parts() -> None:
    part = _zones({"DK1": 1.0, "DK2": 2.0})

    result = combine_levels({"up": part, "down": part}, _grid(), ["DK1", "DK2"])

    assert result.columns.tolist() == [
        ("DK1", "up"),
        ("DK1", "down"),
        ("DK2", "up"),
        ("DK2", "down"),
    ]


def test_combine_levels_keeps_given_order_and_never_sorts() -> None:
    part = _zones({"DK1": 1.0, "DK2": 2.0})

    result = combine_levels({"up": part, "down": part}, _grid(), ["DK2", "DK1"])

    assert result.columns.tolist() == [
        ("DK2", "up"),
        ("DK2", "down"),
        ("DK1", "up"),
        ("DK1", "down"),
    ]
    assert result.columns.tolist()[0] == ("DK2", "up")
    result = combine_levels({"z": part, "a": part}, _grid(), ["DK1"])
    assert result.columns.tolist() == [("DK1", "z"), ("DK1", "a")]


def test_combine_levels_pads_a_missing_zone_and_missing_slots_with_nan() -> None:
    up = _zones({"DK1": 1.0, "DK2": 2.0})
    down = _zones({"DK1": 5.0}, hours=2)

    result = combine_levels({"up": up, "down": down}, _grid(), ["DK1", "DK2"])

    assert result[("DK2", "down")].isna().all()
    assert result[("DK1", "down")].tolist()[:2] == [5.0, 5.0]
    assert np.isnan(result[("DK1", "down")].iloc[2])
    assert result[("DK2", "up")].tolist() == [2.0, 2.0, 2.0]


def test_combine_levels_drops_columns_that_are_not_in_outer() -> None:
    part = _zones({"DK1": 1.0, "FI": 3.0, "NO1": 4.0})

    result = combine_levels({"up": part}, _grid(), ["DK1", "DK2"])

    assert sorted(set(result.columns.get_level_values(0))) == ["DK1", "DK2"]
    assert result[("DK2", "up")].isna().all()


def test_combine_levels_casts_to_float64_and_matches_the_index_exactly() -> None:
    wide = _zones({"DK1": 1.0}, hours=5)
    ints = pd.DataFrame({"DK1": [1, 2, 3, 4, 5]}, index=wide.index)

    result = combine_levels({"up": ints}, _grid(), ["DK1"])

    assert result.index.equals(_grid())
    assert set(result.dtypes) == {np.dtype("float64")}
    assert result[("DK1", "up")].tolist() == [1.0, 2.0, 3.0]


def test_combine_levels_over_an_empty_index_keeps_the_columns() -> None:
    empty = pd.DatetimeIndex([], tz=CPH)
    part = pd.DataFrame({"DK1": [1, None]}, index=_grid(2))

    result = combine_levels({"up": part, "down": part}, empty, ["DK1", "DK2"])

    assert result.shape == (0, 4)


def test_combine_levels_gives_the_same_frame_under_two_keys_the_same_values() -> None:
    part = _zones({"DK1": 1.0, "DK2": 2.0})
    before = part.copy()

    result = combine_levels({"up": part, "down": part}, _grid(), ["DK1", "DK2"])

    for zone in ("DK1", "DK2"):
        assert_frame_equal(
            result[[(zone, "up")]].droplevel(1, axis=1),
            result[[(zone, "down")]].droplevel(1, axis=1),
        )
    assert_frame_equal(part, before)


def test_combine_levels_does_not_mutate_a_part_with_foreign_columns() -> None:
    part = pd.DataFrame({"DK1": [1, 2, 3], "FI": [4, 5, 6]}, index=_grid())

    combine_levels({"up": part}, _grid(), ["DK1"])

    assert list(part.columns) == ["DK1", "FI"]
    assert set(part.dtypes) == {np.dtype("int64")}


@pytest.mark.parametrize(
    ("parts", "outer", "message"),
    [
        ({}, ["DK1"], "parts must not be empty"),
        ({"up": _zones({"DK1": 1.0})}, [], "outer must not be empty"),
        ({"up": _zones({"DK1": 1.0})}, (), "outer must not be empty"),
    ],
)
def test_combine_levels_rejects_empty_parts_or_outer(
    parts: dict[str, pd.DataFrame], outer: list[str], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        combine_levels(parts, _grid(), outer)


def test_combine_levels_rejects_a_part_with_a_duplicate_index_entry() -> None:
    index = pd.DatetimeIndex([_ts("2026-09-15"), _ts("2026-09-15")])
    part = pd.DataFrame({"DK1": [1.0, 2.0]}, index=index)

    with pytest.raises(ValueError, match="duplicate"):
        combine_levels({"up": part}, _grid(), ["DK1"])


def test_combine_levels_rejects_a_repeated_outer_name_naming_it() -> None:
    with pytest.raises(ValueError, match="DK1"):
        combine_levels({"up": _zones({"DK1": 1.0})}, _grid(), ["DK1", "DK1"])
