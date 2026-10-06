"""Shape time series into frames: a full period index, wide pivots and gap padding."""

from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import cast

import pandas as pd

from .periods import DANISH_TZ

_HOUR = timedelta(hours=1)


def _check_resolution(resolution: timedelta, name: str = "resolution") -> None:
    """Raise unless `resolution` is a positive whole divisor of one hour."""
    if resolution <= timedelta(0) or _HOUR % resolution != timedelta(0):
        raise ValueError(
            f"{name} must be a positive whole divisor of one hour, got {resolution!r}"
        )


def period_index(
    start: pd.Timestamp, end: pd.Timestamp, resolution: timedelta
) -> pd.DatetimeIndex:
    """Build the index of every slot in `[start, end)`.

    Slots are stepped in elapsed time, so a DST change shortens or lengthens
    the local day rather than shifting the slots.

    Args:
        start: First slot, inclusive. Must be timezone-aware.
        end: End of the period, exclusive. Must be timezone-aware.
        resolution: Slot length. Must be a positive whole divisor of one hour.

    Returns:
        A `datetime64[ns, tz]` index named `"time"`, in `start`'s timezone.

    Raises:
        ValueError: If a bound is naive, `start >= end`, `resolution` is not a
            positive whole divisor of one hour, or the span is not a whole
            multiple of `resolution`.
    """
    for name, stamp in (("start", start), ("end", end)):
        if stamp.tzinfo is None:
            raise ValueError(f"{name} must be timezone-aware, got {stamp!r}")
    _check_resolution(resolution)
    end = end.tz_convert(start.tz)  # bounds in different zones are one instant each
    if start >= end:
        raise ValueError(f"start must be before end, got start={start!r} end={end!r}")
    if (end - start) % pd.Timedelta(resolution) != pd.Timedelta(0):
        raise ValueError(
            f"the span {end - start} is not a whole multiple of {resolution}"
        )
    index = pd.date_range(
        start, end, freq=pd.Timedelta(resolution), inclusive="left", name="time"
    )
    return index.as_unit("ns")


def records_to_wide(
    records: Sequence[Mapping[str, object]],
    *,
    time: str,
    column: str,
    value: str,
    tz: str = DANISH_TZ,
) -> pd.DataFrame:
    """Pivot long records into a float frame with one column per category.

    Args:
        records: Rows, each a mapping holding at least the three named fields.
        time: Field holding an ISO 8601 string. A naive one is read as UTC.
        column: Field whose distinct values become the column names.
        value: Field holding the number. `None` becomes NaN.
        tz: IANA name of the zone the index is converted to.

    Returns:
        A frame indexed by the sorted times (`datetime64[ns, tz]`, named
        `"time"`), with the columns sorted. No records give an empty frame
        with a timezone-aware index.

    Raises:
        ValueError: If a record lacks one of the fields, a time or value cannot
            be parsed, or the same (`time`, `column`) pair appears twice.
    """
    times: list[object] = []
    names: list[object] = []
    values: list[object] = []
    for position, record in enumerate(records):
        for field in (time, column, value):
            if field not in record:
                raise ValueError(f"record {position} is missing field {field!r}")
        times.append(record[time])
        names.append(record[column])
        values.append(record[value])
    if not records:
        empty = pd.DatetimeIndex([], tz=tz, name="time").as_unit("ns")
        return pd.DataFrame(index=empty, dtype="float64")
    try:
        stamps = pd.DatetimeIndex(
            pd.to_datetime(cast(list[str], times), utc=True, format="ISO8601")
        ).tz_convert(tz)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"field {time!r} holds an unparseable time: {exc}") from exc
    unset = pd.Series(stamps).isna()
    if unset.any():
        missing = int(unset.argmax())
        raise ValueError(
            f"field {time!r} holds no time in record {missing}: {times[missing]!r}"
        )
    try:
        numbers = pd.Series(values, dtype="float64")
    except (ValueError, TypeError) as exc:
        raise ValueError(f"field {value!r} holds a non-numeric value: {exc}") from exc
    long = pd.DataFrame(
        {"time": stamps.as_unit("ns"), "column": names, "value": numbers.to_numpy()}
    )
    repeated = long.duplicated(subset=["time", "column"], keep=False)
    if repeated.any():
        first = long[repeated].iloc[0]
        raise ValueError(
            f"duplicate record for {time}={first['time'].isoformat()!r} "
            f"and {column}={first['column']!r}"
        )
    wide = long.pivot(index="time", columns="column", values="value")
    wide.columns.name = None
    wide.index = pd.DatetimeIndex(wide.index).as_unit("ns")
    return wide.astype("float64")


def expand_to_resolution(
    frame: pd.DataFrame, source: timedelta, target: timedelta
) -> pd.DataFrame:
    """Repeat each coarse row across the finer slots it covers.

    A row at `t` covers `[t, t + source)` and so appears at `t`,
    `t + target`, and so on. A slot whose own source row is missing stays
    missing: values are never carried over a gap.

    Args:
        frame: Rows indexed by the start of each `source` slot.
        source: The frame's slot length.
        target: The finer slot length wanted.

    Returns:
        A new frame at `target` resolution.

    Raises:
        ValueError: Unless `source` is a positive whole multiple of `target`.
    """
    if target <= timedelta(0) or source <= timedelta(0) or source % target:
        raise ValueError(
            f"source must be a positive whole multiple of target, "
            f"got source={source!r} target={target!r}"
        )
    steps = source // target
    positions = [row for row in range(len(frame)) for _ in range(steps)]
    offsets = pd.to_timedelta(
        [target * step for _ in range(len(frame)) for step in range(steps)]
    )
    expanded = frame.iloc[positions].copy()
    index = pd.DatetimeIndex(frame.index[positions]) + offsets
    index.name = frame.index.name
    expanded.index = index.as_unit("ns")
    return expanded


def conform(
    frame: pd.DataFrame, index: pd.DatetimeIndex, columns: Sequence[str]
) -> pd.DataFrame:
    """Reindex a frame to exactly `index` and `columns`, padding with NaN.

    Nothing is filled from a neighbour: a slot or column the frame lacks is
    NaN. Rows outside `index` are dropped.

    Args:
        frame: The data, with a unique index.
        index: The slots the result must hold, in order.
        columns: The columns the result must hold, in order.

    Returns:
        A `float64` frame whose index is exactly `index`.

    Raises:
        ValueError: If `frame` has a duplicate index entry.
    """
    if frame.index.has_duplicates:
        repeated = frame.index[frame.index.duplicated()][0]
        raise ValueError(f"frame has a duplicate index entry: {repeated!r}")
    return frame.reindex(index=index, columns=list(columns)).astype("float64")


def main() -> None:
    """Showcase this module's functionality."""
    start = pd.Timestamp("2026-03-29", tz="Europe/Copenhagen")
    end = pd.Timestamp("2026-03-30", tz="Europe/Copenhagen")
    resolution = timedelta(minutes=15)

    index = period_index(start, end, resolution)

    print(
        f"{len(index)} slots on the spring DST day, first {index[0]}, last {index[-1]}"
    )

    # Long records to wide: two areas, one missing value, naive times read as UTC.
    records: list[dict[str, object]] = [
        {"hour": "2026-03-28T23:00:00", "area": "DK1", "price": 41.5},
        {"hour": "2026-03-28T23:00:00", "area": "DK2", "price": None},
        {"hour": "2026-03-29T00:00:00", "area": "DK1", "price": 38.0},
    ]
    time = "hour"
    column = "area"
    value = "price"

    wide = records_to_wide(records, time=time, column=column, value=value)

    print(wide)

    # Hourly data spread over the quarter-hours each hour covers.
    source = timedelta(hours=1)
    target = timedelta(minutes=15)

    quarters = expand_to_resolution(wide, source, target)

    print(quarters.head(5))

    # Pad to the full period: slots with no data are NaN, not filled.
    columns = ["DK1", "DK2"]

    full = conform(quarters, index, columns)

    print(f"{full.shape} frame, {int(full['DK1'].notna().sum())} DK1 slots have data")


if __name__ == "__main__":
    main()
