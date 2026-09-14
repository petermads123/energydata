"""Shared low-level building blocks used across ``energydata`` subpackages.

Includes the exception hierarchy, time-handling helpers, the Energi Data
Service HTTP client, and hourly-to-15-minute resampling.
"""

from energydata.common.client import fetch_dataset
from energydata.common.errors import (
    EnergyDataError,
    InvalidPeriodError,
    NoDataError,
    UnknownAreaError,
)
from energydata.common.resample import upsample_hourly_to_15min
from energydata.common.timeutils import (
    DK_TZ,
    SPOT_TRANSITION_UTC,
    TimeInput,
    build_utc_15min_index,
    to_utc_timestamp,
)

__all__ = [
    "DK_TZ",
    "SPOT_TRANSITION_UTC",
    "EnergyDataError",
    "InvalidPeriodError",
    "NoDataError",
    "TimeInput",
    "UnknownAreaError",
    "build_utc_15min_index",
    "fetch_dataset",
    "to_utc_timestamp",
    "upsample_hourly_to_15min",
]
