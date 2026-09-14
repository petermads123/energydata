"""Time handling helpers shared across the ``energydata`` package.

Energi Data Service datasets mix two time conventions: some fields are
Danish local wall-clock time (``HourDK``/``TimeDK``) and some are naive-UTC
strings (``HourUTC``/``TimeUTC``). To keep the rest of the package simple,
every public function in this package works internally in UTC and this
module is the single place that reasons about the Europe/Copenhagen
timezone and daylight-saving transitions.
"""

import datetime

import pandas as pd

from energydata.common.errors import InvalidPeriodError

# Accepted user-facing time inputs: an ISO-ish string, a naive/aware
# ``datetime.datetime``, or a (naive/aware) ``pandas.Timestamp``.
# Uses the PEP 695 `type` statement (ruff UP040) rather than the older
# `TypeAlias` annotation form; usage as a type annotation is identical.
type TimeInput = str | datetime.datetime | pd.Timestamp

# IANA timezone name for Danish local (wall-clock) time.
DK_TZ: str = "Europe/Copenhagen"

# The first timestamp published by the ``DayAheadPrices`` dataset (15-minute
# resolution). Everything strictly before this point is only available from
# the legacy hourly ``Elspotprices`` dataset; everything at/after it comes
# from ``DayAheadPrices``. Verified live against the API on 2026-07-10.
SPOT_TRANSITION_UTC: pd.Timestamp = pd.Timestamp("2025-09-30 22:00:00", tz="UTC")


def to_utc_timestamp(value: TimeInput) -> pd.Timestamp:
    """Convert a user-supplied time value to a tz-aware UTC timestamp.

    Naive input (no timezone information) is interpreted as Danish local
    wall-clock time (``Europe/Copenhagen``), matching how a human would type
    a date/time when thinking about Danish energy prices. Timezone-aware
    input is respected as-is and simply converted to UTC.

    Args:
        value: A date/time string, ``datetime.datetime``, or
            ``pandas.Timestamp``. See :data:`TimeInput`.

    Returns:
        A tz-aware ``pandas.Timestamp`` in UTC.

    Raises:
        InvalidPeriodError: If ``value`` cannot be parsed into a valid
            timestamp, or corresponds to an ambiguous/nonexistent local time
            during a DST transition.
    """
    try:
        ts = pd.Timestamp(value)
    except (ValueError, TypeError) as exc:
        raise InvalidPeriodError(f"could not parse timestamp: {value!r}") from exc

    # pd.Timestamp(None) and similar inputs silently produce NaT instead of
    # raising; treat that as an invalid period too.
    if pd.isna(ts):
        raise InvalidPeriodError(f"could not parse timestamp: {value!r}")

    if ts.tzinfo is None:
        # Naive value: interpret as Danish local wall-clock time. This can
        # raise (e.g. pytz AmbiguousTimeError/NonExistentTimeError) for the
        # ~1 hour around DST transitions; surface that as InvalidPeriodError
        # rather than leaking a pytz-specific exception type.
        try:
            ts = ts.tz_localize(DK_TZ)
        except Exception as exc:  # deliberately broad: re-raised as our own error type below
            raise InvalidPeriodError(
                f"'{value}' is an ambiguous or nonexistent local time in {DK_TZ}"
            ) from exc

    return ts.tz_convert("UTC")


def build_utc_15min_index(start_utc: pd.Timestamp, end_utc: pd.Timestamp) -> pd.DatetimeIndex:
    """Build a uniform 15-minute UTC grid for the half-open interval [start, end).

    The grid is constructed directly in UTC (never by localizing a
    local-time grid), which avoids the classic DST bug where a naive
    local-time grid resampled across a spring-forward/fall-back boundary
    produces duplicate or missing local hours.

    Args:
        start_utc: Tz-aware UTC timestamp marking the (inclusive) start of
            the period. Floored down to the nearest 15-minute boundary.
        end_utc: Tz-aware UTC timestamp marking the (exclusive) end of the
            period.

    Returns:
        A tz-aware (UTC) ``pandas.DatetimeIndex`` at 15-minute steps,
        covering ``[start, end)``.

    Raises:
        InvalidPeriodError: If ``start_utc >= end_utc``.
    """
    if start_utc >= end_utc:
        raise InvalidPeriodError(f"start ({start_utc}) must be strictly before end ({end_utc})")

    floored_start = start_utc.floor("15min")
    # inclusive="left" enforces the half-open [start, end) contract even
    # when end_utc happens to land exactly on a 15-minute grid point.
    return pd.date_range(start=floored_start, end=end_utc, freq="15min", tz="UTC", inclusive="left")


def main() -> None:
    """Demonstrate every function/constant in this module."""
    print(f"DK_TZ = {DK_TZ}")
    print(f"SPOT_TRANSITION_UTC = {SPOT_TRANSITION_UTC}")

    # Naive string -> interpreted as Danish local time, returned as UTC.
    naive_ts = to_utc_timestamp("2026-07-10T12:00")
    print(f"to_utc_timestamp('2026-07-10T12:00') -> {naive_ts}")

    # Already tz-aware input is respected and just converted to UTC.
    aware_ts = to_utc_timestamp(pd.Timestamp("2026-07-10T12:00", tz="UTC"))
    print(f"to_utc_timestamp(aware UTC ts) -> {aware_ts}")

    try:
        to_utc_timestamp("not a real timestamp")
    except InvalidPeriodError as exc:
        print(f"Caught InvalidPeriodError for unparseable input: {exc}")

    # Build a small 1-hour grid (4 quarters) starting mid-quarter, to show
    # that the start gets floored to the nearest 15-minute boundary.
    start_utc = pd.Timestamp("2026-07-10T00:07", tz="UTC")
    end_utc = pd.Timestamp("2026-07-10T01:00", tz="UTC")
    index = build_utc_15min_index(start_utc, end_utc)
    print(f"build_utc_15min_index({start_utc}, {end_utc}) -> {list(index)}")

    try:
        build_utc_15min_index(end_utc, start_utc)
    except InvalidPeriodError as exc:
        print(f"Caught InvalidPeriodError for start >= end: {exc}")


if __name__ == "__main__":
    main()
