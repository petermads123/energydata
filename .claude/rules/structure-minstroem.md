---
paths:
  - "src/energydata/minstroem/**"
  - "tests/test_minstroem_*.py"
  - "tests/fixtures/minstroem_openapi.json"
  - "tests/conftest.py"
---

# Structure: `src/energydata/minstroem/`

The Min Strøm **Consumer API** (`https://api.minstroem.app/consumer/v1`; read-only; the
Enterprise API is out of scope). Every call goes through `MinStroemClient`. `__init__.py`
re-exports `BASE_URL`, `API_KEY_ENV`, `MinStroemClient`, `MinStroemError`,
`MissingApiKeyError`, `Aggregation`, `AGGREGATIONS`, `PriceResolution`, `PRICE_RESOLUTIONS` and
the eight `get_*` functions. Every module has a `main()` showcase; with no `MINSTROEM_API_KEY`
it prints one line saying so and returns, otherwise it calls the live API.

Series frames: index `"time"`, `datetime64[ns, Europe/Copenhagen]`, one row per interval of
`[start, end)` on the Danish calendar; a row the API did not return is NaN (`<NA>` for the
`boolean` columns `is_estimate` / `is_forecast`), never zero. Currency, VAT inclusion and the
forecast's `fetched_at` live in `frame.attrs`. A bound not on the requested interval is a
`ValueError`.

## `src/energydata/minstroem/client.py`

| Signature | Description |
|---|---|
| `BASE_URL: str` | The Consumer API root. |
| `API_KEY_ENV: str` | `"MINSTROEM_API_KEY"`, read when no key is passed. |
| `MinStroemError(message: str, *, status=None, error_code=None, reason=None, request_id=None)` | An API error response, an exhausted retry or holdoff on a response, or a payload that is not what the spec promises (`status` then 200, `error_code` `None`). `str()` is the message only. |
| `MissingApiKeyError(MinStroemError)` | The first request of a client with no key; the message names `API_KEY_ENV`. |
| `MinStroemClient(api_key: str \| None = None, *, policy: RetryPolicy \| None = None, timeout: float = DEFAULT_TIMEOUT, max_concurrency: int = 2, transport: httpx.AsyncBaseTransport \| None = None)` | `ApiClient` on `BASE_URL`. Key stripped; one that is not printable ASCII without spaces is refused at the first request without being echoed. `repr` is `MinStroemClient(key=set \| missing)`. Property `has_api_key`. |
| `async MinStroemClient.fetch_json(path: str, params: Mapping[str, str] \| None = None) -> dict[str, JsonValue]` | One `GET` with the bearer key; maps error responses to `MinStroemError` (a `feature_unavailable` names the capability); a transport failure stays `RetriesExhaustedError`. |
| `MinStroemClient.get_json(path, params=None) -> dict[str, JsonValue]` | The sync form, through `run`. |
| `main() -> None` | Showcase (live API). |

## `src/energydata/minstroem/_series.py`

Private. The interval literals, period alignment, Copenhagen-calendar windows under each
endpoint's cap, address resolution (`address_id=None` takes the only address after checking its
capability flag), payload checks and the frame builder. Windows are gathered with
`utils.chunking._gather_ordered`.

| Signature | Description |
|---|---|
| `type Aggregation = Literal["hour", "day", "month", "year"]`, `AGGREGATIONS` | Usage, cost and revenue intervals, API spelling. |
| `type PriceResolution = Literal["hour", "fifteenMinutes"]`, `PRICE_RESOLUTIONS` | Historical price intervals. |
| `main() -> None` | Offline showcase of alignment and windows. |

## `src/energydata/minstroem/addresses.py`, `usage.py`, `costs.py`, `prices.py`

Each function takes `client: MinStroemClient | None = None` (created and closed when absent,
a passed one left open) and the address-scoped ones a keyword-only `address_id: str | None =
None` (a non-UUID is a `ValueError` before any request).

| Signature | Description |
|---|---|
| `get_addresses(*, client=None) -> pd.DataFrame` | `RangeIndex`; `id`, `label`, `formatted_address`, `region`, `time_zone`, `grid_operator_name`, `supplier_name` (object), then the six capability `bool` columns `consumption`, `solar_export`, `consumption_costs`, `solar_export_revenue`, `prices`, `forecast_prices`. |
| `get_consumption(start, end=None, *, aggregation="hour", address_id=None, client=None) -> pd.DataFrame` | kWh: `energy_kwh`, `is_estimate`. `attrs`: `address_id`, `aggregation`. |
| `get_solar_export(...)` | Same, from `/solar-export`. |
| `get_consumption_costs(...)` | `energy_kwh`, `total_price_per_kwh`, `fixed_fees`, `total_cost`, `is_estimate`; `attrs` add `currency`, `vat_included` (True). |
| `get_solar_export_revenue(...)` | `net_revenue` in place of `total_cost`; `vat_included` False. |
| `get_prices(start, end=None, *, resolution="hour", address_id=None, client=None) -> pd.DataFrame` | `total_price_per_kwh`, `variable_charges_per_kwh` (currency/kWh incl. VAT). `attrs`: `address_id`, `resolution`, `currency`, `vat_included`. |
| `get_forecast_prices(*, address_id=None, client=None) -> pd.DataFrame` | Hourly, first to last forecast hour (gaps NaN), same two columns; `attrs` add `fetched_at`. Empty forecast: empty frame, same columns and attrs. |
| `get_prices_with_forecast(start, end=None, *, address_id=None, client=None) -> pd.DataFrame` | Hourly, the two price columns and `is_forecast`; confirmed wins, forecast only after the last confirmed hour; confirmed prices never requested beyond local midnight two days ahead. |
| `main() -> None` | Showcase in each module (live API). |

Window sizes per request (Copenhagen calendar): usage hour 365 days, day/month/year 4 years;
costs hour/day 365 days, month 12 months, year 1 year; prices hour 365 days, quarter-hour 89 days.

## Tests

Written at step 5, none touching the network: `tests/test_minstroem_client.py`,
`_series.py`, `_addresses.py`, `_usage.py`, `_costs.py`, `_prices.py` and `_spec.py` (the
contract test against `tests/fixtures/minstroem_openapi.json`, the OpenAPI 3.1 spec as
published on 2026-10-26), all on `httpx.MockTransport`. `tests/test_minstroem_live.py` holds
the live suite: every test is marked `live`, skipped without `MINSTROEM_API_KEY`, and plain
`pytest` deselects it (`addopts = "-v -m 'not live'"`). `tests/conftest.py` carries the support:
`MinStroemService` (a mock API: bearer check, parameter validation, outward normalisation to the
Copenhagen calendar, caps on the normalised period, sparse generated or stored rows, `forecast`,
`overrides`, `gaps`, `requests`, `calls(suffix)`, `client(api_key=...)`), the helpers
`minstroem_address` and `minstroem_price_row`, the `minstroem_service` fixture, the
`live_report` fixture and the `pytest_terminal_summary` hook that prints the
`MIN STRØM LIVE SUMMARY` block. The autouse `_no_network` guard lets a test marked `live` through.
