"""Resolve a caller's `start` and `end` arguments into a half-open period."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd

type TimeLike = date | datetime | str

DANISH_TZ: str = "Europe/Copenhagen"

_HOUR = timedelta(hours=1)
_ONE_DAY = timedelta(days=1)


def _check_resolution(resolution: timedelta) -> None:
    """Raise unless `resolution` is a positive whole divisor of one hour."""
    if resolution <= timedelta(0) or _HOUR % resolution != timedelta(0):
        raise ValueError(
            f"resolution must be a positive whole divisor of one hour, "
            f"got {resolution!r}"
        )


def _localise(naive: pd.Timestamp, name: str, tz: str) -> pd.Timestamp:
    """Attach `tz` to a naive timestamp, refusing a missing or repeated time."""
    try:
        return naive.tz_localize(tz, nonexistent="raise", ambiguous="raise")
    except Exception as exc:  # pytz raises its own non-ValueError error types
        raise ValueError(
            f"{name} {naive.isoformat()!r} does not exist or is ambiguous in {tz}: "
            f"{exc}"
        ) from exc


def _bound(value: TimeLike, name: str, tz: str) -> tuple[pd.Timestamp, bool]:
    """Resolve one argument to a timestamp in `tz`; also say if it was a date."""
    is_date = False
    if isinstance(value, str):
        text = value
        try:
            value = date.fromisoformat(text)
        except ValueError:
            try:
                value = datetime.fromisoformat(text)
            except ValueError as exc:
                raise ValueError(
                    f"{name} is not an ISO 8601 date or time: {text!r}"
                ) from exc
    if isinstance(value, datetime):  # before `date`: a datetime is a date
        stamp = pd.Timestamp(value)
        if pd.isna(stamp):
            raise ValueError(f"{name} must be a real date or time, got {value!r}")
    elif isinstance(value, date):
        stamp = pd.Timestamp(value.year, value.month, value.day)
        is_date = True
    else:
        raise ValueError(
            f"{name} must be a date, datetime or ISO string, got {value!r}"
        )
    local = _localise(stamp, name, tz) if stamp.tzinfo is None else stamp.tz_convert(tz)
    return local.as_unit("ns"), is_date


def _check_aligned(stamp: pd.Timestamp, name: str, resolution: timedelta) -> None:
    """Raise unless `stamp` sits on a `resolution` boundary."""
    since_epoch = stamp.tz_convert("UTC") - pd.Timestamp(0, tz="UTC")
    if since_epoch % pd.Timedelta(resolution) != pd.Timedelta(0):
        raise ValueError(
            f"{name} {stamp.isoformat()!r} is not on a {resolution} boundary"
        )


def resolve_period(
    start: TimeLike,
    end: TimeLike | None = None,
    *,
    resolution: timedelta = timedelta(minutes=15),
    tz: str = DANISH_TZ,
) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Resolve `start` and `end` into a half-open period `[start, end)` in `tz`.

    A naive value is read as local time in `tz`; an aware one is converted. A
    date means local midnight. With no `end`, a date selects the whole local
    day and a timestamp selects one `resolution` slot.

    Args:
        start: First moment of the period, inclusive.
        end: First moment after the period, exclusive. `None` derives it from
            `start` as described above.
        resolution: The slot length. Every timestamp bound must lie on a
            multiple of it. Must be a positive whole divisor of one hour.
        tz: IANA name of the zone naive values are read in.

    Returns:
        The start and end as tz-aware timestamps in `tz`.

    Raises:
        ValueError: If `end <= start`, a timestamp bound is not on a
            `resolution` boundary, a naive time does not exist or is
            ambiguous in `tz`, a string does not parse, `tz` is not a known zone,
            or `resolution` is not a positive whole divisor of one hour.
    """
    _check_resolution(resolution)
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown time zone {tz!r}") from exc
    first, start_is_date = _bound(start, "start", tz)
    if end is None:
        if start_is_date:
            day = pd.Timestamp(first.year, first.month, first.day) + _ONE_DAY
            last = _localise(day, "end", tz).as_unit("ns")
        else:
            _check_aligned(first, "start", resolution)
            last = first + pd.Timedelta(resolution)
    else:
        last, end_is_date = _bound(end, "end", tz)
        if not start_is_date:
            _check_aligned(first, "start", resolution)
        if not end_is_date:
            _check_aligned(last, "end", resolution)
    if last <= first:
        raise ValueError(
            f"end must be after start, got start={first.isoformat()!r} "
            f"end={last.isoformat()!r}"
        )
    return first, last


def main() -> None:
    """Showcase this module's functionality."""
    start: TimeLike = "2026-03-29"  # a date, a datetime or an ISO 8601 string
    end = None

    first, last = resolve_period(start, end)

    print(f"lone date {start}: {first.isoformat()} -> {last.isoformat()}")
    print(f"  that day is {(last - first) / pd.Timedelta(minutes=15):.0f} slots long")

    # A lone timestamp is one slot; a naive one is read as Danish time.
    start = "2026-06-01T12:30"

    first, last = resolve_period(start, end)

    print(f"lone timestamp {start}: {first.isoformat()} -> {last.isoformat()}")

    # An explicit end is exclusive.
    start = date(2026, 6, 1)
    end = date(2026, 6, 3)

    first, last = resolve_period(start, end)

    print(f"{start} to {end}: {first.isoformat()} -> {last.isoformat()}")


if __name__ == "__main__":
    main()
