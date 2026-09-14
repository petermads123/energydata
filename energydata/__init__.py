"""energydata — collection of Danish energy data.

Collects data such as energy prices and energy consumption from the
Energi Data Service and Eloverblik APIs. See the individual subpackages
(``energydata.dayahead``, ``energydata.tariffs``, ...) for details.
"""

from energydata.common.errors import (
    EnergyDataError,
    InvalidPeriodError,
    NoDataError,
    UnknownAreaError,
)
from energydata.dayahead.dayahead import get_dayahead_prices
from energydata.tariffs.dso import get_dso_tariffs
from energydata.tariffs.elafgift import get_elafgift
from energydata.tariffs.energinet import get_energinet_tariffs

__all__ = [
    "EnergyDataError",
    "InvalidPeriodError",
    "NoDataError",
    "UnknownAreaError",
    "get_dayahead_prices",
    "get_dso_tariffs",
    "get_elafgift",
    "get_energinet_tariffs",
]
