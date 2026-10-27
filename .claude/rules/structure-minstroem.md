---
paths:
  - "src/energydata/minstroem/**"
  - "tests/test_minstroem_*.py"
  - "tests/minstroem_live_mock.py"
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
parameters, fields, enums, the spec's own examples and error responses, the mock's bodies and its UUID `X-Request-ID`s), the
live suite's wiring (run in a subprocess), the exports, the showcases and the docs.

`tests/test_minstroem_live.py` holds the live suite: every test is
marked `live`, skipped without `MINSTROEM_API_KEY` (a blank key counts as none, as the client
strips it), and plain `pytest` deselects it (`addopts = "-v -m 'not live'"`). It shares one
module-scoped client; the address under test is `MINSTROEM_ADDRESS_ID` when set (matched without
regard to case or surrounding space; an id the account does not have skips every test that needs
an address, as does an account with none), else the first address with `forecastPrices`, else the
first address; a test whose capability that address lacks is skipped naming the capability (the
combined-series tests need both `forecastPrices` and `prices`); each test adds a `live_report`
line, never the key, an address text or an address id. The `is_forecast` column is read with
Series methods (`eq`, `isna`), never Python truthiness, because an hour with neither source is
`<NA>`; "now" is floored in UTC, because a Copenhagen time cannot be floored in the repeated hour
of the autumn change. The address-default test expects a `ValueError` for no address as well as
for several. The combined-series cross-check makes four requests (forecast, combined, prices,
forecast) and so does not assume one snapshot: confirmed prices are always compared, the forecast
value for value only when it is the same before and after, else it must be made of hours one of
the two snapshots had, and the report line says so.

The output is pasted into a chat, so the suite keeps the key, an address id and the address text
out of it under every `--tb` style; only `--showlocals` (`-l`) defeats that, and the README says
not to add it. Every test is wrapped by a private decorator: a failed request fails the test
with an `AssertionError` instead, a `MinStroemError` showing its message, status, error code,
reason and request id (a UUID, kept as it is) and a `RetryError` its message, with every other
UUID (an address id) replaced by `<address>`, the chained error suppressed and the traceback cut
at the test's own frame, because the frames below it have the request path as an argument and
`--tb=long` prints it. The address the tests pass around is a `dict` subclass whose `repr`
is `<address>`, and its id and the keys are `str` subclasses whose `repr` is `<private>`, which is
what pytest prints for the arguments of a frame. The package's own messages are unchanged.

`tests/minstroem_live_mock.py` is a pytest plugin that runs that live suite offline:
`pytest -p minstroem_live_mock tests/test_minstroem_live.py -m live`, with a dummy
`MINSTROEM_API_KEY` (stripped, as the client strips it), `tests/` and `src/` on `PYTHONPATH`, the
scenario in `MINSTROEM_LIVE_MOCK` (`SCENARIO_ENV`; `plain` when unset or empty) and, optionally,
the clock in `MINSTROEM_LIVE_NOW` (`NOW_ENV`). Its `pytest_configure() -> None` hook builds that
scenario's service around the current time, makes every real httpx transport refuse (before any
fixture of any scope is set up, so the live suite's module-scoped `address` fixture is covered)
and patches `MinStroemClient.__init__` so every client, whatever `transport` it was given, is
answered by the mock, with instant retries unless a `policy` is passed. A missing or blank key, an
unknown scenario (the message names `MINSTROEM_LIVE_MOCK` and the value, case and space matter) and
a bad instant are each a `pytest.UsageError`. `pytest_unconfigure() -> None` restores the
constructor, the clock and the transports. `MINSTROEM_LIVE_NOW` is an ISO 8601 instant with an
offset (`2026-06-15T12:00:00+02:00`; anything else, a naive time or a date included, is a
`pytest.UsageError`, and so is one too close to the end of the calendar to lay data out around):
when set, the data is laid out around it and `pandas.Timestamp.now` is fixed to it, so the
package (the confirmed-price cap, `fetched_at`) and the live tests ("today") read the instant the
data was built for. Unset or empty, the run uses the real clock, which is fine to look at by hand
but races an hour or local midnight passing during the run. `SCENARIOS` is `plain`, `hole`,
`gappy_forecast`, `empty_forecast`, `no_prices`, `no_forecast`, `no_addresses`, `two_addresses`
and `prices_not_found`; `build_service(scenario: str, now: datetime, api_key: str) ->
MinStroemService` lays the data out around `now` (confirmed prices from local midnight three days
back to tomorrow noon, a 48-hour forecast from the current hour; offsets such as "02:00 today"
are elapsed hours after local midnight, an hour off the wall clock on a daylight-saving change
day) and each scenario bends it: `hole` drops two confirmed hours (today 01:00, yesterday 05:00)
and nulls the total at today 03:00; `gappy_forecast` ends the confirmed prices at 02:00 today, or
at the current hour when that is earlier, and serves the forecast over `[current hour + 2 h,
current hour + 40 h)`, missing +5 h and +6 h, with a null total in its first row and a null
variable charge at +7 h, so the current hour and the next have neither source at any time of day;
`empty_forecast` has no forecast rows; `no_prices` / `no_forecast` give the one address no
`prices` / `forecastPrices`; `no_addresses` gives the account none; `two_addresses` adds
`SECOND_ADDRESS_ID` as a second address, the only one that can forecast; `prices_not_found`
answers `/prices` with a 404 whose reason holds the address id. `ValueError` for an unknown
scenario. `tests/test_minstroem_spec.py` runs the suite once per scenario in a subprocess with the
clock pinned (`PINNED_NOW`) and asserts which tests pass or skip, where the combined series runs
its count of hours with neither source, a report line proving the scenario's branch ran, one
summary line per test that reported (and, for `plain`, the exact lines at the pinned instant), that no
key, address id or address text is in the output, and, for `prices_not_found`, that the failures
keep their facts, request id included, and lose the id under every `--tb` style. It also runs the
suite with the clock on and the day after each daylight-saving change, in each pass of the
repeated autumn hour and on its hour, and at local midnight; with a padded key; and with
`MINSTROEM_ADDRESS_ID` set to each address, in upper case, padded and unknown. And it builds
`plain`, `hole`, `gappy_forecast` and `two_addresses` in process on the spring and autumn change
days and an ordinary day, and `gappy_forecast` alone at six times of day from local midnight to 23:30; runs probes of the plugin (the key, scenario and instant it refuses, a
padded key, variables left in the shell, a module-scoped fixture, a client given a real
transport, setup and teardown in process); and checks the live suite's helpers in process (what a
failure report keeps and removes, the traceback cut, what passes through untouched, the cross-check
across a day-ahead publication) and that the package and the live suite read the clock only
through `pandas.Timestamp.now`.

`tests/conftest.py` carries the support, imported by the suites with `from conftest import ...`:
`ADDRESS_ID` (the default address UUID); `minstroem_address(address_id: str = ADDRESS_ID, label:
str = "Home", **capabilities: bool) -> dict[str, Any]` (one `/me/addresses` object, every
capability true unless overridden); `minstroem_price_row(start: datetime, total: float | None,
variable: float | None = 0.25) -> dict[str, Any]` (one hourly price/forecast row from an aware
`start`; the hour is added in UTC, so a row across the autumn change is one hour);
`minstroem_request_id(number: int) -> str` (the UUID the mock gives its `number`th request as
`X-Request-ID`, as the spec types the header); `MinStroemService(addresses: list[dict[str, Any]] | None = None, *, api_key: str =
"test-key", currency: str = "DKK")` — a mock API written independently of the package: bearer
check (401 `invalid_token`), parameter validation (400 `invalid_parameter`, `invalid_range`),
outward expansion to the Copenhagen calendar and caps after expansion (400 `range_too_large`:
366 local days touched, 90 for `fifteenMinutes`, five calendar years for usage at `day` and
above), an unknown address 404 `resource_not_found`, a capability the address lacks 422
`feature_unavailable`, a UUID `X-Request-ID` on every response; rows served sparse from `rows[suffix]`
or, while `generate` is true, one per interval minus `gaps` (start texts); `forecast` the
forecast rows; `overrides[last path segment]` answering a request in its place; every request
kept in `requests` and read back with `calls(suffix)` (a path *ends with* `suffix`, so ask for `"/prices"`: `"prices"` also matches `forecast-prices`); `client(api_key: str | None =
"test-key", *, policy: RetryPolicy | None = None, max_concurrency: int = 2)` building a real
`MinStroemClient` on it with instant retries — the `minstroem_service` fixture (one address,
every capability, generated rows), the `live_report` fixture (`live_report(line)` adds a line)
and the `pytest_terminal_summary` hook printing the `MIN STRØM LIVE SUMMARY` block when any
line was added. `block_real_transports(monkeypatch: pytest.MonkeyPatch) -> None` makes every
real httpx transport refuse (`httpx.MockTransport` is untouched); the autouse no-network guard
calls it for every test except one marked `live` (it is function-scoped, so a module-scoped
fixture is set up before it), and the `minstroem_live_mock` plugin calls it from
`pytest_configure` for those.
