"""Min Strøm: the Consumer API for Danish household electricity data."""

from ._series import (
    AGGREGATIONS,
    PRICE_RESOLUTIONS,
    Aggregation,
    PriceResolution,
)
from .addresses import get_addresses
from .client import (
    API_KEY_ENV,
    BASE_URL,
    MinStroemClient,
    MinStroemError,
    MissingApiKeyError,
)
from .costs import get_consumption_costs, get_solar_export_revenue
from .prices import get_forecast_prices, get_prices, get_prices_with_forecast
from .usage import get_consumption, get_solar_export
