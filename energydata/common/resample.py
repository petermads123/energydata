"""Resampling helpers shared across the ``energydata`` package.

Currently home to the hourly -> 15-minute upsampling used to bridge legacy
hourly price/tariff datasets onto the modern 15-minute settlement grid.
"""

import pandas as pd


def upsample_hourly_to_15min(hourly: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
    """Upsample an hourly series onto a 15-minute UTC grid via forward fill.

    Each hourly value is forward-filled into its own three trailing
    quarter-hour slots (``:15``, ``:30``, ``:45``) only; the next real hourly
    value (at ``:00``) naturally overwrites the fill for the following hour.
    A genuine gap in the source data (an hour with no value at all, and thus
    nothing to fill from within the following 3 slots once the fill budget
    of the previous hour is exhausted) is left as ``NaN`` rather than being
    silently bridged.

    Args:
        hourly: Float series indexed by tz-aware UTC hour timestamps
            (one value per hour).
        index: Target tz-aware UTC ``DatetimeIndex`` at 15-minute steps.

    Returns:
        A float series reindexed onto ``index``, with each hourly value
        forward-filled across at most 3 consecutive 15-minute slots.
    """
    # reindex requires a monotonic source index for method="ffill" to behave
    # correctly; the API generally returns time-sorted records, but sort
    # defensively in case that ordering assumption is ever violated.
    sorted_hourly = hourly.sort_index()
    return sorted_hourly.reindex(index=index, method="ffill", limit=3)


def main() -> None:
    """Demonstrate upsample_hourly_to_15min with a small worked example."""
    # Two consecutive hours of source data, deliberately unsorted to show
    # the defensive sort_index() call in action.
    hourly_index = pd.DatetimeIndex(
        ["2026-07-10T01:00", "2026-07-10T00:00"], tz="UTC"
    )
    hourly = pd.Series([20.0, 10.0], index=hourly_index)

    target_index = pd.date_range(
        "2026-07-10T00:00", "2026-07-10T02:00", freq="15min", tz="UTC", inclusive="left"
    )
    upsampled = upsample_hourly_to_15min(hourly, target_index)
    print(upsampled)


if __name__ == "__main__":
    main()
