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
the eight `get_*` functions. Every module but `__init__.py` has a `main()` showcase; with no
`MINSTROEM_API_KEY` the live ones print one line saying so and return, otherwise they call the
live API — except `_series.main()`, which runs offline and only adds an address lookup when a
key is set.

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
| `MinStroemError(message: str, *, status: int \| None = None, error_code: str \| None = None, reason: str \| None = None, request_id: str \| None = None)` | Attributes `status`, `error_code`, `reason`, `request_id`; `str()` is the message only. An API error response, or an exhausted retry or holdoff on a response: `status` the HTTP status, `error_code`/`reason` from the documented error body (else `None`), `request_id` from `X-Request-ID`. A 2xx body that is not JSON or not a JSON object: `status` 200. Every other check raises it with all four attributes `None`: a key a header cannot carry, a missing capability flag found by the `address_id=None` lookup, a payload whose fields or rows are not what the spec promises (a number that is not finite or too large, a timestamp out of range, a looked-up address id that is not a UUID), windows or sources disagreeing on currency/VAT. |
| `MissingApiKeyError(MinStroemError)` | The first request of a client with no key; the message names `API_KEY_ENV`. |
| `MinStroemClient(api_key: str \| None = None, *, policy: RetryPolicy \| None = None, timeout: float = DEFAULT_TIMEOUT, max_concurrency: int = 2, transport: httpx.AsyncBaseTransport \| None = None)` | `ApiClient` on `BASE_URL`. Key stripped; one that is not printable ASCII without spaces is refused at the first request without being echoed. `repr` is `MinStroemClient(key=set \| missing)`. Property `has_api_key`. |
| `async MinStroemClient.fetch_json(path: str, params: Mapping[str, str] \| None = None) -> dict[str, JsonValue]` | One `GET` with the bearer key. `MissingApiKeyError` (or `MinStroemError` for an unusable key) before any I/O; error responses and an exhausted retry or holdoff on a response become `MinStroemError`, chained (a `feature_unavailable` names the capability); a 2xx body that is not JSON or not a JSON object is a `MinStroemError` with `status` 200; a transport failure stays `RetriesExhaustedError`. Only awaitable on the client's loop. |
| `MinStroemClient.get_json(path, params=None) -> dict[str, JsonValue]` | The sync form, through `run`. |
| `main() -> None` | Showcase (live API). |

## `src/energydata/minstroem/_series.py`

Private. The interval literals, period alignment, Copenhagen-calendar windows under each
endpoint's cap, address resolution (`address_id=None` takes the only address after checking that its id is a
UUID and its capability flag), payload checks and the frame builder. Windows are requested concurrently and
gathered in order; on a failure the rest are cancelled and the lowest-index exception is
raised, never an `ExceptionGroup`.

| Signature | Description |
|---|---|
| `type Aggregation = Literal["hour", "day", "month", "year"]`, `AGGREGATIONS: tuple[Aggregation, ...]` | Usage, cost and revenue intervals, API spelling. |
| `type PriceResolution = Literal["hour", "fifteenMinutes"]`, `PRICE_RESOLUTIONS: tuple[PriceResolution, ...]` | Historical price intervals, API spelling. |
| `main() -> None` | Showcase of alignment and calendar windows, offline; with `MINSTROEM_API_KEY` set it adds one live address lookup. |

## `src/energydata/minstroem/addresses.py`, `usage.py`, `costs.py`, `prices.py`

Each function takes `client: MinStroemClient | None = None` (created and closed when absent, a
passed one left open) and the address-scoped ones a keyword-only `address_id: str | None =
None`: a non-UUID is a `ValueError` before any request; `None` looks up the addresses and uses
the only one — none or several is a `ValueError` (the latter listing each id and label), and an
address lacking the endpoint's capability flag is a `MinStroemError` naming it.

| Signature | Description |
|---|---|
| `get_addresses(*, client=None) -> pd.DataFrame` | `RangeIndex`; `id`, `label`, `formatted_address`, `region`, `time_zone`, `grid_operator_name`, `supplier_name` (object), then the six capability `bool` columns `consumption`, `solar_export`, `consumption_costs`, `solar_export_revenue`, `prices`, `forecast_prices`. |
| `get_consumption(start, end=None, *, aggregation="hour", address_id=None, client=None) -> pd.DataFrame` | kWh: `energy_kwh`, `is_estimate`. `attrs`: `address_id`, `aggregation`. |
| `get_solar_export(...)` | Same, from `/solar-export`. |
| `get_consumption_costs(...)` | `energy_kwh`, `total_price_per_kwh`, `fixed_fees`, `total_cost`, `is_estimate`; `attrs` add `currency`, `vat_included` (True). |
| `get_solar_export_revenue(...)` | `net_revenue` in place of `total_cost`; `vat_included` False. |
| `get_prices(start, end=None, *, resolution="hour", address_id=None, client=None) -> pd.DataFrame` | `total_price_per_kwh`, `variable_charges_per_kwh` (currency/kWh incl. VAT). `attrs`: `address_id`, `resolution`, `currency`, `vat_included`. |
| `get_forecast_prices(*, address_id=None, client=None) -> pd.DataFrame` | Hourly, first to last forecast hour (gaps NaN), same two columns. `attrs`: `address_id`, `currency`, `vat_included`, `fetched_at` (tz-aware Copenhagen, when the response arrived); no `resolution`. Empty forecast: empty frame, same columns, empty tz-aware `"time"` index, same attrs. |
| `get_prices_with_forecast(start, end=None, *, address_id=None, client=None) -> pd.DataFrame` | Hourly, the two price columns and `is_forecast` (`True` forecast, `False` confirmed, `<NA>` neither); confirmed wins, forecast only after the last confirmed hour; confirmed prices never requested beyond local midnight two days ahead. With `end`, exactly `[start, end)`; without, to the last forecast hour (local midnight two days ahead when the forecast is empty); a computed end at or before `start` gives the empty frame. `attrs`: `address_id`, `currency`, `vat_included`, `fetched_at`. Needs the `forecastPrices` capability and, where it requests confirmed prices, `prices` (with `address_id=None` checked after the lookup and before `/prices`; a period wholly beyond the cap never requests it). A confirmed row whose prices are null is still the confirmed hour and wins the overlap; the two sources disagreeing on currency/VAT is a `MinStroemError`. |
| `main() -> None` | Showcase in each module (live API). |

Window sizes per request (Copenhagen calendar): usage hour 365 days, day/month/year 4 years;
costs hour/day 365 days, month 12 months, year 1 year; prices hour 365 days, quarter-hour 89 days.

## Tests

Written at step 5, none touching the network (about 900 tests): `tests/test_minstroem_client.py`,
`tests/test_minstroem_series.py`, `tests/test_minstroem_addresses.py`,
`tests/test_minstroem_usage.py`, `tests/test_minstroem_costs.py`,
`tests/test_minstroem_prices.py` and `tests/test_minstroem_spec.py` (the contract test against
`tests/fixtures/minstroem_openapi.json`, the OpenAPI 3.1 spec as published on 2026-10-26), all
on `httpx.MockTransport`.

What each offline suite covers: `client` the error types, the key (argument, environment, stripping,
unusable keys never echoed, 401 never echoing either key), error and 2xx-body mapping, retries,
holdoff and transport failures; `series` the shared path through the five series functions (period
alignment, the calendar index, window splitting against every cap, payload checks, address
resolution, client lifecycle) and `MinStroemService` itself; `addresses`, `usage`, `costs` and
`prices` each endpoint family's columns, dtypes, `attrs`, nulls and the forecast and combined series
(`prices` holds the primary goal); `spec` the package against the OpenAPI fixture (paths, query
parameters, fields, enums, the spec's own examples and error responses, the mock's bodies), the
live suite's wiring (run in a subprocess), the exports, the showcases and the docs.

`tests/test_minstroem_live.py` holds the live suite: every test is
marked `live`, skipped without `MINSTROEM_API_KEY`, and plain `pytest` deselects it
(`addopts = "-v -m 'not live'"`). It shares one module-scoped client; the address under test is
`MINSTROEM_ADDRESS_ID` when set, else the first address with `forecastPrices`, else the first
address; a test whose capability that address lacks is skipped naming the capability; each test
adds a `live_report` line, never the key, an address text or an address id.

`tests/conftest.py` carries the support, imported by the suites with `from conftest import ...`:
`ADDRESS_ID` (the default address UUID); `minstroem_address(address_id: str = ADDRESS_ID, label:
str = "Home", **capabilities: bool) -> dict[str, Any]` (one `/me/addresses` object, every
capability true unless overridden); `minstroem_price_row(start: datetime, total: float | None,
variable: float | None = 0.25) -> dict[str, Any]` (one hourly price/forecast row from an aware
`start`; the hour is added in UTC, so a row across the autumn change is one hour); `MinStroemService(addresses: list[dict[str, Any]] | None = None, *, api_key: str =
"test-key", currency: str = "DKK")` — a mock API written independently of the package: bearer
check (401 `invalid_token`), parameter validation (400 `invalid_parameter`, `invalid_range`),
outward expansion to the Copenhagen calendar and caps after expansion (400 `range_too_large`:
366 local days touched, 90 for `fifteenMinutes`, five calendar years for usage at `day` and
above), an unknown address 404 `resource_not_found`, a capability the address lacks 422
`feature_unavailable`, `X-Request-ID` on every response; rows served sparse from `rows[suffix]`
or, while `generate` is true, one per interval minus `gaps` (start texts); `forecast` the
forecast rows; `overrides[last path segment]` answering a request in its place; every request
kept in `requests` and read back with `calls(suffix)` (a path *ends with* `suffix`, so ask for `"/prices"`: `"prices"` also matches `forecast-prices`); `client(api_key: str | None =
"test-key", *, policy: RetryPolicy | None = None, max_concurrency: int = 2)` building a real
`MinStroemClient` on it with instant retries — the `minstroem_service` fixture (one address,
every capability, generated rows), the `live_report` fixture (`live_report(line)` adds a line)
and the `pytest_terminal_summary` hook printing the `MIN STRØM LIVE SUMMARY` block when any
line was added. The autouse no-network guard lets a test marked `live` through.
