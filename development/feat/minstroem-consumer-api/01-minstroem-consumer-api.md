# Min Strøm Consumer API, with price forecasts first

<!-- claude-plan step=4 status=active -->

| Field | Value |
|---|---|
| Feature | `feat/minstroem-consumer-api` (the folder) |
| Round | `1` |
| Branch | `feat/minstroem-consumer-api` |
| Started | `2026-10-26` |

## Progress

| # | Step | Skill | Runs | Status |
|---|---|---|---|---|
| 1 | Conceptualize | `/conceptualize` | with the user | done |
| 2 | Plan | `/plan` | with the user | done |
| 3 | Implement | `/implement` | in `/build` | done |
| 4 | Verify | `/verify` | in `/build` | pending |
| 5 | Test | `/test` | in `/build` | pending |
| 6 | Concept check | `/concept-check` | in `/build` | pending |
| 7 | Ship | `/ship` | in `/build` | pending |
| 8 | Recommend | `/recommend` | with the user | pending |
| 9 | Pull request | `/create-pr` | with the user | pending |
| 10 | Review | `/watch-pr` | on the pull request | pending |

Statuses: `pending`, `in progress`, `done`.

## Builds on

Nothing — this is the first round.

---

## 1. Concept

### What this is

A new source subpackage, `energydata.minstroem`, for the **Min Strøm Consumer API**
(`https://api.minstroem.app/consumer/v1`, docs at <https://docs.minstroem.app/introduction>,
OpenAPI 3.1 spec at <https://docs.minstroem.app/api-reference/openapi.json>). It follows
the Energi Data Service pattern: one client on top of `utils.ApiClient`, and one plain
synchronous function per endpoint returning a `pandas.DataFrame` with a tz-aware
`Europe/Copenhagen` index. The user's primary goal is **hourly price forecasts**; every
other endpoint of the Consumer API is covered too, with forecasts held to the sharpest
criteria and the most live tests.

The Consumer API, as documented on 2026-10-26:

| Endpoint | Data | Interval parameter | Cap after normalisation |
|---|---|---|---|
| `GET /me/addresses` | `data[]`: `id` (uuid), `label`, `formattedAddress`, `region` (DK1, DK2, SE1-4, NO1-5 or null), `timeZone`, `gridOperatorName` (nullable), `supplierName` (nullable), `capabilities` (six booleans: `consumption`, `solarExport`, `consumptionCosts`, `solarExportRevenue`, `prices`, `forecastPrices`) | — | — |
| `GET /me/addresses/{id}/consumption` | `start`, `end`, `energyKWh`, `isEstimate` | `aggregation` = `hour`/`day`/`month`/`year` | 366 days hourly, five years otherwise |
| `GET /me/addresses/{id}/solar-export` | same as consumption | same | same |
| `GET /me/addresses/{id}/consumption-costs` | `energyKWh`, `totalPricePerKWh` (nullable), `fixedFeesForInterval`, `totalCost`, `isEstimate`; response `currency`, `vatIncluded: true` | `aggregation` | 366 days |
| `GET /me/addresses/{id}/solar-export-revenue` | `energyKWh`, `totalPricePerKWh` (nullable), `fixedFeesForInterval`, `netRevenue`, `isEstimate`; `vatIncluded: false` | `aggregation` | 366 days |
| `GET /me/addresses/{id}/prices` | `totalPricePerKWh`, `variableChargesPerKWh` (nullable), incl. VAT, 3 decimals | `resolution` = `hour`/`fifteenMinutes` | 366 days / 90 days |
| `GET /me/addresses/{id}/forecast-prices` | same row shape as prices; response `resolution: "hour"`, `isEstimate: true`; regions DK1, DK2, SE3, SE4 | none — no period | — |

Requests take `from`/`to` as RFC 3339 with `Z`, half-open, expanded outward by the server to
whole Copenhagen-calendar intervals. Responses are UTC and sparse: a missing row is unknown,
not zero. Auth is `Authorization: Bearer <key>` (key created in the iOS app, needs Min Strøm
Plus). Limits: 60 requests/minute and 2,000/day; 429 carries `Retry-After`. Errors are
`{"error": true, "reason": ..., "errorCode": ...}` with codes `invalid_parameter`,
`invalid_range`, `range_too_large`, `invalid_token`, `credential_expired`, `plus_required`,
`forbidden`, `resource_not_found`, `feature_unavailable`, `rate_limited`, `internal_error`,
`upstream_unavailable`, `entitlement_unavailable`, `rate_limiter_unavailable`, and every
response carries `X-Request-ID`.

### Why it is worth building

The user wants Min Strøm's hourly price forecasts (the price of the coming hours beyond the
latest confirmed price, incl. VAT and their address's charges) in the same frame-shaped,
Danish-time form as the rest of the package, so they can plan consumption. The other
endpoints come along because the client, auth, error handling and period logic are shared,
and the user asked for everything the API offers.

### Inputs and outputs

- **Key:** `api_key=` on the client, else the `MINSTROEM_API_KEY` environment variable.
  Without a key everything imports and constructs; the first request raises a clear error.
- **Address:** every address-scoped function takes `address_id: str | None = None`. `None`
  looks up the addresses and uses the only one; zero or several is a `ValueError` (several
  listing each id and label).
- **Periods:** `start`/`end` as elsewhere in the package — naive is Danish local time, half-open
  `[start, end)`, a lone date is the whole local day. A period not aligned to the requested
  aggregation/resolution (e.g. `aggregation="month"` starting mid-month) is a `ValueError`,
  never silently expanded.
- **Output:** a DataFrame whose index is the interval start, tz-aware `Europe/Copenhagen`,
  one row per interval of `[start, end)` on the Danish calendar (23/25 hours on DST days),
  a missing interval NaN — never zero. Currency (`DKK`/`SEK`/`NOK`), VAT inclusion and, for
  forecasts, the fetch time live in `frame.attrs`.
- **Forecast:** no period in; the rows the API returns out, hourly.
- **Combined series:** `start`, optional `end`; confirmed hourly prices where they exist and
  forecast after the last confirmed hour, with an `is_forecast` column.

### How it connects to the rest of the repo

Calls `utils`: `ApiClient` (loop, concurrency, retry with `Retry-After` — 429 is already in
`RETRY_STATUSES`), `resolve_period`, `period_index`/`conform` and, where it fits, the chunked
fetch helpers. Nothing calls it and it touches no existing module beyond re-exports and
docs. The `DEVELOPMENT.md` note on `date_windows` being elapsed-time applies: windows must be
split on Copenhagen calendar boundaries, not elapsed UTC spans. The note on the flat `tests/`
colliding is answered for this round by prefixing: `test_minstroem_*.py`.

### Explicitly out of scope

- The **Enterprise API** (`/thirdParty`, key + HMAC secret) — a different product and key.
- Anything that writes: the Consumer API is read-only.
- Caching, or keeping a history of forecast vintages — `fetched_at` in `attrs` lets the
  caller store snapshots themselves.
- Restructuring `tests/` into per-subpackage folders.

### Acceptance criteria

| # | The finished feature... |
|---|---|
| A1 | **Forecast (primary goal).** `get_forecast_prices(address_id=None)` returns an hourly frame indexed by hour start (tz-aware Copenhagen) with columns `total_price_per_kwh` and `variable_charges_per_kwh` (incl. VAT, float, NaN where null), and `attrs` holding `currency`, `vat_included` and `fetched_at` (tz-aware). An empty forecast is an empty frame with the same columns and index type; an address whose `forecastPrices` capability is false fails with a clear error naming the capability. |
| A2 | **Combined series.** `get_prices_with_forecast(start, end=None, address_id=None)` returns hourly prices with the same two price columns plus a boolean `is_forecast`: confirmed prices wherever they exist, forecast only for hours after the last confirmed hour, confirmed winning any overlap. With `end` the frame covers exactly `[start, end)` hourly, an hour with neither being NaN; without `end` it runs to the last forecast hour. |
| A3 | **Addresses.** `get_addresses()` returns one row per address with id, label, formatted address, region, time zone, grid operator, supplier and the six capability flags as boolean columns; no addresses is an empty frame with the same columns. |
| A4 | **Address default.** With `address_id=None`, the address-scoped functions use the account's only address; zero addresses, or more than one, is a `ValueError` — the latter listing each id and label. |
| A5 | **Series.** Consumption and solar export (`energy_kwh`, `is_estimate`), consumption costs and solar-export revenue (energy, effective rate, fixed fees, total cost / net revenue, `is_estimate`), and historical prices (`total_price_per_kwh`, `variable_charges_per_kwh`) each return one row per interval of `[start, end)` on the Danish calendar for every documented aggregation/resolution; a missing interval is NaN, never zero; a period not aligned to the interval is a `ValueError`; currency and VAT inclusion are in `attrs`. |
| A6 | **Long periods.** A period beyond the endpoint's cap is split into windows aligned to Copenhagen calendar boundaries; no interval is requested twice, and the result equals what one unbounded call would give. |
| A7 | **Errors and key.** An API error raises `MinStroemError` carrying the HTTP status, `errorCode`, `reason` and `X-Request-ID`. The key comes from `api_key=` or `MINSTROEM_API_KEY`, and never appears in an exception message, a `repr` or test output. A missing key fails only when a request is made, with a message naming `MINSTROEM_API_KEY`. |
| A8 | **Tests.** Plain `pytest` is green with no key and touches no network. Live tests, marked `live`, cover every endpoint and especially the forecast and the combined series; they are skipped without `MINSTROEM_API_KEY` and run against the real API with it. The user gets one PowerShell line, run from the repo root with the key inline, that runs only the live tests and prints a readable summary to paste back. |
| A9 | **Docs.** README, `STRUCTURE.md` and a new `.claude/rules/structure-minstroem.md` describe the subpackage, its functions, units and the key setup. |

### Open questions

None. Decided with the user on 2026-10-26: Consumer API only; `address_id` optional when the
account has exactly one address; misaligned periods refused rather than expanded; every
endpoint built, forecasts first; a combined confirmed-plus-forecast series included.

Stated assumption: it is not known without a key whether `/prices` accepts a `to` in the
future. The live tests check it; the combined series caps its confirmed-price request itself
if it does not.

---

## 2. Plan

Plan acceptance: the user said in advance, on 2026-10-26, "Du behøver ik vente på mit
godkend af planen. Bare fortsæt når hele step 2 er klar" — the plan is accepted once this
section, its coverage check and its critique are complete, and the build opens in the same
turn.

### Approach

**Structural, on the Energi Data Service pattern.** A thin `MinStroemClient` subclass of
`ApiClient` owns auth, error mapping and payload checks and exposes one JSON-object fetch;
a private `_series.py` owns everything shared by the period endpoints (alignment check,
calendar windows, the windowed fetch, the full-period frame); one public module per
endpoint family (`addresses.py`, `usage.py`, `costs.py`, `prices.py`) holds thin functions
that only name their endpoint, fields and columns. Every function takes an optional
`client` (created and closed when absent, a passed one left open) and an optional,
keyword-only `address_id`.

Rejected: **minimal** — one module with seven functions and the window logic inline. It
is fewer files, but the windowing and frame building would be written once per endpoint
family or hidden in a 700-line module, and the forecast work (the user's goal) would be
buried in it. **Defensive** — typed result dataclasses instead of frames, or a per-address
client object. It contradicts the package's "every endpoint returns a DataFrame" rule and
adds a second way to call everything. Reusing `utils.chunking.gather_chunked` for windows
was rejected too: it steps in elapsed UTC time (`DEVELOPMENT.md` already records that this
lands boundaries at 23:00 local across DST), and the caps here are in Copenhagen calendar
days, months and years, so windows are computed on the local calendar. They are gathered
with `utils.chunking._gather_ordered` (imported privately, as `day_ahead.py` already does):
in order, cancelling the rest on the first failure, raising the lowest-index exception
rather than an `ExceptionGroup`. `utils` itself is not changed this round, so the
`DEVELOPMENT.md` note on making it public stays open.

### Modules

| Path | New or changed | Purpose |
|---|---|---|
| `src/energydata/minstroem/__init__.py` | new | Re-exports every public name below (not `main`). |
| `src/energydata/minstroem/client.py` | new | `MinStroemClient`: bearer key from argument or `MINSTROEM_API_KEY`, the error types, JSON-object fetch with error mapping. |
| `src/energydata/minstroem/_series.py` | new | Private shared path for period endpoints: interval literals, period alignment, calendar windows under each cap, windowed concurrent fetch, the full-period frame, address resolution, client lifecycle. |
| `src/energydata/minstroem/addresses.py` | new | `get_addresses`. |
| `src/energydata/minstroem/usage.py` | new | `get_consumption`, `get_solar_export`. |
| `src/energydata/minstroem/costs.py` | new | `get_consumption_costs`, `get_solar_export_revenue`. |
| `src/energydata/minstroem/prices.py` | new | `get_prices`, `get_forecast_prices`, `get_prices_with_forecast` — the forecast work lives here. |
| `tests/conftest.py` | changed | `_no_network` lets tests marked `live` through; adds `MinStroemService` (mock API), the `minstroem_service` fixture, the `live_report` fixture and a `pytest_terminal_summary` hook printing the live summary. |
| `tests/fixtures/minstroem_openapi.json` | new, **committed with this plan** | The OpenAPI 3.1 spec as published on 2026-10-26 (from `https://docs.minstroem.app/api-reference/openapi.json`), for the contract test. Already on the branch; the build uses it as is and never re-downloads it. |
| `tests/test_minstroem_client.py`, `tests/test_minstroem_series.py`, `tests/test_minstroem_addresses.py`, `tests/test_minstroem_usage.py`, `tests/test_minstroem_costs.py`, `tests/test_minstroem_prices.py`, `tests/test_minstroem_spec.py` | new | Offline suites (step 5). |
| `tests/test_minstroem_live.py` | new | Live suite, every test marked `live`, skipped without `MINSTROEM_API_KEY` (written at step 3 so it exists even if step 5 is cut short; extended at step 5). |
| `pyproject.toml` | changed | `[tool.pytest.ini_options]`: `markers = ["live: calls the real Min Strøm API; needs MINSTROEM_API_KEY"]`, and `addopts = "-v -m 'not live'"` so plain `pytest` (and the stop gate, and CI) deselects the live tests even when the key is set; the live command passes `-m live`, and the last `-m` wins. |
| `README.md`, `STRUCTURE.md`, `.claude/rules/structure-minstroem.md`, `.claude/rules/structure-utils.md` | changed / new | Docs (A9). The utils rules file only gains the conftest line. The README's first line names Min Strøm as a source. |

### Public API

Literals and constants first, then classes and functions. Every function returns a
`pandas.DataFrame`. "Series frame" below means: index named `"time"`, `datetime64[ns,
Europe/Copenhagen]`, holding every interval start of `[start, end)` on the Danish calendar
for the requested interval, in order; a row the API did not return is NaN (floats) / `<NA>`
(the `boolean` column); floats are `float64`, `is_estimate`/`is_forecast` pandas
`"boolean"` dtype.

| Signature | Module | Purpose | Covers |
|---|---|---|---|
| `BASE_URL: str = "https://api.minstroem.app/consumer/v1"` | `client.py` | The Consumer API root. | A7 |
| `API_KEY_ENV: str = "MINSTROEM_API_KEY"` | `client.py` | The environment variable read when no key is passed. | A7, A8 |
| `class MinStroemError(Exception)` with `__init__(self, message: str, *, status: int \| None = None, error_code: str \| None = None, reason: str \| None = None, request_id: str \| None = None) -> None` and attributes `status`, `error_code`, `reason`, `request_id` | `client.py` | An API error response, an exhausted retry or holdoff on a response, or a payload that is not what the spec promises (then `status` is the 2xx status and `error_code` is `None`). `str()` is the message only. | A7 |
| `class MissingApiKeyError(MinStroemError)` | `client.py` | Raised by the first request when the client has no key; message names `API_KEY_ENV`. All four attributes `None`. | A7 |
| `MinStroemClient(api_key: str \| None = None, *, policy: RetryPolicy \| None = None, timeout: float = DEFAULT_TIMEOUT, max_concurrency: int = 2, transport: httpx.AsyncBaseTransport \| None = None)` | `client.py` | `ApiClient` on `BASE_URL`. `api_key=None` reads `API_KEY_ENV` at construction; the key is stripped, and an empty result counts as none. A key that is not entirely `[\x21-\x7e]` (control characters, inner whitespace, non-ASCII) is kept out of the headers and refused at the first request with a `MinStroemError` saying the key has characters an HTTP header cannot carry — never echoing it. Never fails for a missing or bad key at construction. `max_concurrency < 1` is a `ValueError`. `__repr__` returns `MinStroemClient(key=set)` / `MinStroemClient(key=missing)` — never the key. Property `has_api_key: bool`. | A7 |
| `async MinStroemClient.fetch_json(path: str, params: Mapping[str, str] \| None = None) -> dict[str, JsonValue]` | `client.py` | One `GET` through `ApiClient.request` with `Authorization: Bearer <key>`; returns the JSON object. Maps `httpx.HTTPStatusError`, and `RetriesExhaustedError`/`HoldoffTooLongError` that carry a response, to `MinStroemError` (chained) with status, `errorCode`, `reason` (from the body when it is the documented error object, else `None`) and `X-Request-ID`. A `RetriesExhaustedError` without a response (transport failure) propagates unchanged. A 2xx body that does not parse (`ParseError`) or is not a JSON object is a `MinStroemError` (`status` the 2xx status, `error_code` `None`), chained. `MissingApiKeyError` (or the bad-key `MinStroemError`) before any I/O. Only awaitable on the client's loop. | A7 |
| `MinStroemClient.get_json(path: str, params: Mapping[str, str] \| None = None) -> dict[str, JsonValue]` | `client.py` | Sync form through `run`. | A7 |
| `type Aggregation = Literal["hour", "day", "month", "year"]`, `AGGREGATIONS: tuple[Aggregation, ...]` | `_series.py`, re-exported | Usage, cost and revenue intervals, API spelling. | A5 |
| `type PriceResolution = Literal["hour", "fifteenMinutes"]`, `PRICE_RESOLUTIONS: tuple[PriceResolution, ...]` | `_series.py`, re-exported | Historical price intervals, API spelling. | A5 |
| `get_addresses(*, client: MinStroemClient \| None = None) -> pd.DataFrame` | `addresses.py` | `RangeIndex`; columns in order `id`, `label`, `formatted_address`, `region`, `time_zone`, `grid_operator_name`, `supplier_name` (object, `None` where null), then `consumption`, `solar_export`, `consumption_costs`, `solar_export_revenue`, `prices`, `forecast_prices` (`bool`). No addresses: empty frame, same columns. An address object missing a required field, or a non-boolean capability, is a `MinStroemError`. | A3 |
| `get_consumption(start: TimeLike, end: TimeLike \| None = None, *, aggregation: Aggregation = "hour", address_id: str \| None = None, client: MinStroemClient \| None = None) -> pd.DataFrame` | `usage.py` | Series frame: `energy_kwh`, `is_estimate`. `attrs`: `address_id`, `aggregation`. | A5, A6 |
| `get_solar_export(start: TimeLike, end: TimeLike \| None = None, *, aggregation: Aggregation = "hour", address_id: str \| None = None, client: MinStroemClient \| None = None) -> pd.DataFrame` | `usage.py` | Same, from `/solar-export`. | A5, A6 |
| `get_consumption_costs(start: TimeLike, end: TimeLike \| None = None, *, aggregation: Aggregation = "hour", address_id: str \| None = None, client: MinStroemClient \| None = None) -> pd.DataFrame` | `costs.py` | Series frame: `energy_kwh`, `total_price_per_kwh`, `fixed_fees`, `total_cost`, `is_estimate`. `attrs`: `address_id`, `aggregation`, `currency`, `vat_included` (`True`). | A5, A6 |
| `get_solar_export_revenue(start: TimeLike, end: TimeLike \| None = None, *, aggregation: Aggregation = "hour", address_id: str \| None = None, client: MinStroemClient \| None = None) -> pd.DataFrame` | `costs.py` | Series frame: `energy_kwh`, `total_price_per_kwh`, `fixed_fees`, `net_revenue`, `is_estimate`. `attrs` as costs, `vat_included` `False`. | A5, A6 |
| `get_prices(start: TimeLike, end: TimeLike \| None = None, *, resolution: PriceResolution = "hour", address_id: str \| None = None, client: MinStroemClient \| None = None) -> pd.DataFrame` | `prices.py` | Series frame: `total_price_per_kwh`, `variable_charges_per_kwh`. `attrs`: `address_id`, `resolution`, `currency`, `vat_included`. | A5, A6 |
| `get_forecast_prices(*, address_id: str \| None = None, client: MinStroemClient \| None = None) -> pd.DataFrame` | `prices.py` | Hourly frame (index as a series frame) from the first to the last forecast hour, interior gaps NaN: `total_price_per_kwh`, `variable_charges_per_kwh`. `attrs`: `address_id`, `currency`, `vat_included`, `fetched_at` (tz-aware Copenhagen `pd.Timestamp`, taken when the response arrived). Empty forecast: empty frame, same columns, empty tz-aware `"time"` index, `attrs` still set. A 422 `feature_unavailable` surfaces as `MinStroemError` whose message names the `forecastPrices` capability. | A1, A4 |
| `get_prices_with_forecast(start: TimeLike, end: TimeLike \| None = None, *, address_id: str \| None = None, client: MinStroemClient \| None = None) -> pd.DataFrame` | `prices.py` | Hourly frame: `total_price_per_kwh`, `variable_charges_per_kwh`, `is_forecast`. Rules in the guide, step 9. `attrs`: `address_id`, `currency`, `vat_included`, `fetched_at`. | A2, A4 |
| `main() -> None` in each module | every module | Showcase; `_series.main()` is an offline showcase of alignment and calendar windows. With no key it prints one line saying to set `MINSTROEM_API_KEY` and returns (exit 0); with a key it calls the live API and prints shapes, the region and a few values — never the address text or the key. | — |

Shared behaviour of every address-scoped function (A4): `address_id` and `client` are
keyword-only on purpose (A2's `get_prices_with_forecast(start, end=None, address_id=None)`
is written with `address_id` as a keyword, following the package's keyword-only `client`),
so this is not drift. `address_id=None` makes one `/me/addresses` request and uses the
only address, and — because its capability flags are then in hand — checks the endpoint's
flag before requesting it, raising `MinStroemError` naming the capability (e.g.
"the address has no 'forecastPrices' capability") when it is false; with an explicit id
the server's 422 mapping names it instead; zero addresses is a `ValueError`
("no addresses on this account"); several is a `ValueError` listing each `id` and `label`.
An `address_id` that is not a UUID string (`uuid.UUID(value)` fails) is a `ValueError`
before any request, so nothing can be smuggled into the path. Validation of `start`/`end`,
`aggregation` and `resolution` happens before any request, including the address lookup.

### Implementation guide

1. **Spec fixture.** Copy the OpenAPI spec to `tests/fixtures/minstroem_openapi.json`
   (download it; if the download fails, copy the scratchpad file named in Modules).
2. **`client.py`.** Constants, `MinStroemError`, `MissingApiKeyError`, `MinStroemClient`.
   Build the `Authorization` header into `ApiClient`'s `headers` at construction when the
   key is present; keep the key only there and in one private attribute; override
   `__repr__`. `fetch_json` checks the key first, then calls `self.request("GET", path,
   params=params, fmt="json")` and catches `httpx.HTTPStatusError`,
   `RetriesExhaustedError` and `HoldoffTooLongError`; for those with a response, read the
   body with `read_json` inside a `try` (a non-JSON or non-error body leaves `error_code`
   and `reason` `None`) and raise `MinStroemError(...) from exc`. Message:
   `"Min Strøm API: HTTP {status} {error_code} on {path}: {reason} (request id {id})"`,
   omitting absent parts; for `feature_unavailable` append the capability for the path's
   last segment (`consumption` → `consumption`, `solar-export` → `solarExport`,
   `consumption-costs` → `consumptionCosts`, `solar-export-revenue` →
   `solarExportRevenue`, `prices` → `prices`, `forecast-prices` → `forecastPrices`):
   `"; the address lacks the 'forecastPrices' capability"`. The message never contains a
   header. `main()` lists addresses' regions and capabilities.
3. **`_series.py` — intervals and alignment.** The literals and tuples; a private
   `_check_interval(value, allowed, name)` (`ValueError` listing the allowed values).
   Period resolution: `resolve_period(start, end, resolution=timedelta(minutes=15))` for
   `fifteenMinutes`, `timedelta(hours=1)` for everything else; then for `day`, `month`,
   `year` require both bounds at local midnight, plus day 1 for `month`, plus 1 January
   for `year`, else `ValueError` naming the bound, its value and the interval. (A lone
   date with `aggregation="day"` is therefore one row; with `"month"` it is refused.)
4. **`_series.py` — calendar boundaries and the index.** A private function returning
   every interval start in `[start, end)` for an interval name: `period_index` for `hour`
   (1 h) and `fifteenMinutes` (15 min); for `day`/`month`/`year`, naive local dates
   stepped by `pd.DateOffset(days=1 | months=1 | years=1)` and then `tz_localize` to
   Copenhagen — never a fixed 24-hour `Tick`, which drifts across DST. Name the index
   `"time"`.
5. **`_series.py` — windows.** Each window is `[lo, hi)` with `hi` the earlier of `end`
   and the `N`-th boundary of unit `B` strictly after `lo`, where boundaries are local
   midnights (`B = day`), local month starts (`month`) or local 1 January (`year`):

   | Endpoint family | Interval | `B` | `N` |
   |---|---|---|---|
   | consumption, solar export | `hour` | day | 365 |
   | consumption, solar export | `day`, `month`, `year` | year | 4 |
   | costs, revenue | `hour`, `day` | day | 365 |
   | costs, revenue | `month` | month | 12 |
   | costs, revenue | `year` | year | 1 |
   | prices | `hour` | day | 365 |
   | prices | `fifteenMinutes` | day | 89 |

   Every window is then aligned to the interval, touches at most 366 local days (90 for
   quarter-hours, five calendar years for usage at day and above, one year for yearly
   costs), and consecutive windows share a boundary, so no interval is requested twice.
6. **`_series.py` — address resolution and client lifecycle.** A private async resolver
   implementing the A4 rules above (lookup, single-address rule, capability pre-check,
   UUID check), and a private context helper that yields the passed client or a new one
   it closes.
7. **`_series.py` — fetch and frame.** A private async function: resolve the address
   (step 6), then request every window concurrently with `_gather_ordered` (the client's
   semaphore bounds it), each with `from`/`to` as UTC `YYYY-MM-DDTHH:MM:SSZ` and the
   interval parameter (`aggregation` or `resolution`). Check each payload: `data` is a
   list of objects carrying the endpoint's required fields (the spec's required
   properties), numbers are numbers (not `bool`), nullable fields may be `null`
   (→ NaN) — and for `/prices` and `/forecast-prices` **both** price fields accept `null`
   (→ NaN) although the spec types `totalPricePerKWh` as non-null, because A1 promises
   NaN where null, `isEstimate` is a bool. Parse `start` as UTC, convert to Copenhagen. A row
   outside its window is dropped (the server may expand); a row whose start is not on the
   interval index, or a start seen twice, is a `MinStroemError`. For monetary endpoints
   every window's `currency` and `vatIncluded` must agree, else `MinStroemError`. Build
   the frame by reindexing onto the full index — NaN/`<NA>` where absent, never filled —
   with the fixed column order and dtypes. A private sync wrapper runs it through the
   client, creating and closing a client when none was passed.
8. **`addresses.py`, `usage.py`, `costs.py`.** Thin functions over the shared path:
   endpoint path, interval parameter name, family (for the window table), field→column map.
9. **`prices.py`.** `get_prices` as above. `get_forecast_prices`: resolve the address, one
   request to `/forecast-prices`, check the payload (`resolution` must be `"hour"`; each row
   on a whole hour, `end - start == 1 h`; no duplicate starts), stamp `fetched_at`, build
   the hourly index from the first to the last row start (interior gaps NaN), empty frame
   when `data` is empty. `get_prices_with_forecast`:
   - resolve `start` (and `end` when given) with `resolve_period(..., resolution=1 h)`; when
     `end` is `None`, use only the resolved start;
   - resolve the address once; fetch the forecast first;
   - a forecast-capability error (pre-check or 422) stops the combined series too — without
     a forecast it is `get_prices`;
   - the frame's end is `end` when given; otherwise one hour after the last forecast row,
     or, with an empty forecast, local midnight two days after today's local date;
   - a frame end at or before `start` returns the documented empty frame (columns, empty
     tz-aware `"time"` index, `attrs` set) without requesting `/prices`;
   - confirmed prices are requested over `[start, min(frame end, confirmed cap))`, where the
     confirmed cap is local midnight two days after today's local date (day-ahead prices
     are never confirmed beyond tomorrow, so no `to` further ahead is ever sent), through
     the same windowed path as `get_prices(resolution="hour")`; skipped when that range is
     empty;
   - A2 applied literally: `last_confirmed` is the latest hour with a confirmed row;
     confirmed rows give `is_forecast = False`; forecast rows give `True` only for hours
     after `last_confirmed` (all forecast hours when there is no confirmed row), so
     confirmed wins any overlap; forecast rows outside `[start, frame end)` are dropped;
     every other hour is NaN with `is_forecast` `<NA>`;
   - currency and VAT flag must agree between the two sources, else `MinStroemError`.
10. **`__init__.py`** re-exports: `BASE_URL`, `API_KEY_ENV`, `MinStroemClient`,
    `MinStroemError`, `MissingApiKeyError`, `Aggregation`, `AGGREGATIONS`,
    `PriceResolution`, `PRICE_RESOLUTIONS` and the eight `get_*` functions.
11. **Test support.** `pyproject.toml` marker. `tests/conftest.py`: `_no_network` takes
    `request` and returns early when `request.node.get_closest_marker("live")` is set;
    `MinStroemService` (described under Test intents T13) with a `client(api_key="test-key",
    **kwargs)` builder and the `minstroem_service` fixture; a session-scoped list behind a
    `live_report` fixture (`live_report(line: str)` appends), and
    `pytest_terminal_summary` printing, when the list is non-empty, a block headed
    `MIN STRØM LIVE SUMMARY` with one line per entry.
12. **`tests/test_minstroem_live.py`** (written now, extended at step 5). Module-level
    `pytestmark = [pytest.mark.live, pytest.mark.skipif(not os.environ.get(API_KEY_ENV),
    reason="set MINSTROEM_API_KEY to run the live Min Strøm tests")]`. One module-scoped
    client. The address under test: `MINSTROEM_ADDRESS_ID` when set, else the first address
    whose `forecastPrices` is true, else the first address. A test whose capability is false
    for that address is skipped with the capability named. Each test adds a `live_report`
    line with counts, first/last timestamps, region and a few rounded values — never the
    key, the address text or the address id. Tests: addresses shape; forecast (columns,
    index, attrs, hourly, starts no earlier than the current hour minus one hour, finite
    values); combined series today→`None` (continuous hourly index, `is_forecast` never
    `False` after a `True`); prices hour (two days back) and `fifteenMinutes` (one day);
    a probe of `/prices` with a `to` three days ahead that reports whether the API accepted
    it (passes either way; the report line is what matters); consumption hour/day/month/
    year; solar export; costs; revenue; an hourly consumption request over 400 days
    (two windows) succeeding; a wrong key giving `MinStroemError` with status 401 whose
    `str`, `repr` and `args` do not contain either key; a random UUID address giving a 403
    or 404 `MinStroemError`; and `address_id=None` either working (one address) or raising
    the listing `ValueError` (several).
13. **Docs.** README section "Min Strøm" after the Energi Data Service sections: the key
    (Min Strøm Plus, iOS app, `MINSTROEM_API_KEY` or `api_key=`), an example with the
    forecast, a function table like the others (unit, interval, columns, endpoint), the
    missing-is-NaN and `attrs` notes, and how to run the live tests with exactly this
    line from the repo root (the step 7 trace and the pull request repeat it):

    ```powershell
    $env:MINSTROEM_API_KEY='<din-nøgle>'; .venv\Scripts\python.exe -m pytest tests/test_minstroem_live.py -m live -rA --tb=short -p no:cacheprovider; Remove-Item Env:MINSTROEM_API_KEY
    ```

    (`Remove-Item` because `$env:` lasts for the rest of the PowerShell session; `-rA`
    gives a one-line outcome per test, skip reasons included, under the summary block.) `STRUCTURE.md` tree and
    subpackage section; `.claude/rules/structure-minstroem.md` with full signature tables;
    conftest line in `structure-utils.md` and `structure-energidataservice.md` where they
    describe `conftest.py`.

### Test intents

| # | Must prove | Covers |
|---|---|---|
| T1 | `get_forecast_prices` builds the hourly frame (columns, dtypes, Copenhagen index, interior gaps NaN, nulls NaN), sets `currency`/`vat_included`/`fetched_at`, returns the documented empty frame for `data: []`, refuses an off-hour or duplicate row, turns a 422 `feature_unavailable` into a `MinStroemError` naming `forecastPrices`, and with `address_id=None` refuses an address whose `forecastPrices` flag is false before requesting it; a `null` `totalPricePerKWh` or `variableChargesPerKWh` is NaN, not an error. | A1 |
| T2 | `get_prices_with_forecast` stitches confirmed then forecast with `is_forecast`; a mock serving confirmed rows past the first forecast hour gives confirmed values there and forecast only after the last confirmed hour; with `end` covers exactly `[start, end)` with NaN/`<NA>` for neither; without `end` runs to the last forecast hour (or local midnight two days ahead when the forecast is empty); never sends `/prices` a `to` beyond local midnight two days after today; a computed end at or before `start` gives the empty frame; a forecast-capability error stops it; drops forecast rows outside the period; refuses a currency disagreement. Includes a DST day. | A2 |
| T3 | `get_addresses` maps every field and capability to the documented columns and dtypes, keeps nulls as `None`, returns the empty frame for `{"data": []}`, and refuses a malformed address. | A3 |
| T4 | `address_id=None` uses the single address (one lookup request, then the series request with that id); zero and several addresses raise `ValueError`, the latter listing ids and labels; a non-UUID id is refused before any request. | A4 |
| T5 | Each series function, for each of its intervals, returns one row per interval of `[start, end)` on the Danish calendar — including 23- and 25-hour days, months and years — with missing rows NaN/`<NA>` (never 0), the right columns, dtypes and `attrs`; a misaligned bound is a `ValueError` naming it before any request; `from`/`to`/interval parameters are what the API expects. | A5 |
| T6 | For every row of the window table, a period just over the cap is split into aligned windows that each pass the mock's cap check, abut without overlap, and give the same frame as an uncapped mock serving the whole period at once. | A6 |
| T7 | HTTP errors (each documented status with its error body, a non-JSON error body, 429 with a long `Retry-After`, exhausted 503s) become `MinStroemError` with status, code, reason and request id, chained; a transport failure stays `RetriesExhaustedError`; a non-object or malformed 200 body is a `MinStroemError`. | A7 |
| T8 | The key: taken from the argument, else from `MINSTROEM_API_KEY`; sent as `Bearer`; a missing/blank key raises `MissingApiKeyError` naming the variable only at the first request (construction and imports succeed); the key appears in no exception `str`/`args`/chained message, no `repr` of the client, and no captured test output — including a key with a trailing newline (stripped, works) and one with an inner control or non-ASCII character (refused at the first request, never echoed in any `str`, `args` or chained message). | A7, A8 |
| T9 | Client lifecycle: a created client is closed on success and on error; a passed one is left open and reusable. | A5 |
| T10 | Live suite: deselected by plain `pytest` even with the key set (the `addopts` marker expression); skipped (not failed, not erroring) with `-m live` and no key; collected with `-m live`; plain `pytest` touches no network; the `_no_network` guard still refuses for unmarked tests; the summary block appears only when live lines were reported. | A8 |
| T11 | Contract: every field name, enum value, path and query parameter the modules use matches `tests/fixtures/minstroem_openapi.json` (required properties of each schema, `Aggregation`, `PriceResolution`, `Currency` enums, the seven paths, `servers[0].url == BASE_URL`). | A3, A5, A1 |
| T12 | Docs: the README names every public function of the subpackage and `MINSTROEM_API_KEY`, and contains the exact live-test PowerShell line from guide step 13; `STRUCTURE.md` names every new module (the stop gate also checks this). | A9 |
| T13 | The mock (`MinStroemService`) behaves like the documented API, so T5/T6 mean something: bearer check (401 `invalid_token`), parameter validation (400 `invalid_parameter`, `invalid_range`), outward normalisation to the Copenhagen calendar, caps after normalisation (400 `range_too_large`) pinned to the strictest reading and written independently of `_series.py` — at most 366 local calendar days touched (any partial day counting) for hourly/daily usage, costs and revenue and hourly prices, 90 for `fifteenMinutes`, at most 5 calendar years touched for usage at `day` and above, at most 366 days for costs/revenue at `month`/`year` (so a yearly request crossing New Year is refused), each counted after outward expansion, sparse rows with configurable gaps and nulls, currency, forecast rows, per-path status overrides, and a record of every request. A few direct tests of the mock pin this. | A5, A6, A7 |

### Risks

- **Real API behaviour differs from the docs** (cap counting, future `to`, rounding,
  row ordering). The offline suite proves the code against the documented contract; the
  live suite is the only check of reality and runs only on the user's machine. The build
  does **not** halt for this — it ships the documented behaviour and the live summary tells
  us. A finding from the user's live run becomes a fix before the pull request merges.
- **pandas `boolean` dtype** through reindex/concat can silently turn into `object`. Assert
  dtypes in tests; build the column with `pd.array(..., dtype="boolean")` after reindexing.
- **Day/month/year boundaries across DST.** Use naive dates plus `tz_localize`; Copenhagen
  midnight always exists and is never ambiguous, so `tz_localize` needs no
  `ambiguous`/`nonexistent` arguments. If a test shows otherwise, fix it in `_series.py`; no
  halt.
- **The conftest change touches every test.** The live exemption keys on the marker only;
  T10 proves the guard still bites. If the guard change breaks an existing suite, fix the
  guard, not the suite.
- **Key leakage through `httpx` exceptions.** `HTTPStatusError`'s message holds the URL,
  never headers, and the key is never in the URL; T8 asserts it across the chain.
- **Rate limits in the live suite** (60/min). About 25 requests; the retry wrapper honours
  `Retry-After` up to `max_holdoff` (300 s). No action needed.
- **`/prices` may refuse a future `to`.** The combined series sends at most local midnight
  two days ahead; if the live probe shows even that is refused, the fix is a cap at the
  current hour — a follow-up before merge, not a halt.
- **Halt only** for a finding that changes section 1 — for example, if a criterion turns out
  to be untestable as written or contradicts the documented API.

### Critique

`plan-critic` verdict: accept with changes. Every finding applied:

1. Combined series never fetched confirmed hours at/after the first forecast row, so "confirmed wins" relied on a docs claim — **applied**: confirmed prices are requested over the frame (capped at local midnight two days ahead), A2 applied literally via `last_confirmed`; T2 rewritten.
2. A key with control/non-ASCII characters could leak through h11's "Illegal header value" error — **applied**: key stripped, validated against `[\x21-\x7e]+`, refused at first request without echo; T8 extended.
3. A user who sets `MINSTROEM_API_KEY` would make plain `pytest` (and the stop gate) run live — **applied**: `addopts` deselects `live`; T10 updated.
4. The A8 PowerShell line was never written down — **applied**: fixed in guide step 13, checked by T12.
5. Windowed fetch reimplemented `_gather_ordered` with weaker cancellation — **applied**: imported privately like `day_ahead.py`; utils unchanged, DEVELOPMENT note stays open.
6. Spec types `totalPricePerKWh` non-null though A1 promises NaN; capability error relied only on a 422 — **applied**: null accepted for both price fields; capability pre-check when `address_id=None`; T1 extended.
7. Non-JSON 2xx would surface as `ParseError` — **applied**: mapped to `MinStroemError`.
8. Combined series' end-before-start and 422 cases undecided — **applied**: empty frame; capability error stops it; T2 extended.
9. Spec fixture source fragile — **applied**: `tests/fixtures/minstroem_openapi.json` committed with this plan.
10. Mock cap counting unspecified, risking circular tests — **applied**: pinned to the strictest reading in T13, written independently of `_series.py`.
11. `_series.main()` missing, step order, keyword-only `address_id` — **applied**: all three.

Coverage re-run after the critique: every criterion A1–A9 still has at least one Public API
entry and at least one test intent; no public entry lacks a criterion.

---

## 3. Implementation notes

> Written in step 3. Only deviations from the plan above, each with its reason. "Built as
> planned" is a complete and good entry. On a fix round, also the reproduction test's red
> run, pasted here before the fix was written — step 6 cites it.

Built as planned, with these small departures:

- A 2xx body that is not valid JSON is a `MinStroemError` with `status=200` rather than
  "the 2xx status": `ApiClient.request` does not expose the response, and a GET success is
  200. Public signatures are unchanged.
- The Public API table's `main()` for `client.py` lists addresses' regions and capabilities,
  as planned; the shared "no key, print one line" check is a private helper
  `_showcase_has_key` in `client.py`, imported by the other modules.
- `_series.main()` is offline except for one extra address lookup when a key is set.
- Usage caps in the mock (`MinStroemService`) follow the concept table (366 days at `hour`,
  five calendar years at `day`/`month`/`year`); T13's "hourly/daily usage" wording was read
  that way because the plan's window table spans four years at usage `day`.
- `STRUCTURE.md` and `structure-minstroem.md` already name the seven offline test files that
  step 5 writes, so the structure check reports them as missing until then.
- No deviation touches section 1.

---

## 4. Verification log

> Written in step 4: the static half. Command output, not a summary of it.

| Check | Result |
|---|---|
| `ruff check .` | |
| `ruff format --check .` | |
| `mypy` | |
| Plan completeness | every signature in the Public API table exists as written |
| `STRUCTURE.md` | in sync |
| `python -m <package>.<module>` | |

---

## 5. Test log

> Written in step 5: the dynamic half.

| Intent | Test names | Result |
|---|---|---|

Edge cases considered and deliberately skipped, with reasons:

---

## 6. Concept check

> Written in step 6, against section 1 — not against section 2. The question is whether
> the thing built is the thing agreed, not whether it matches the plan.

| # | Criterion | Met | Evidence |
|---|---|---|---|
| A1 | | | |

Drift found, and what was done about it:

### Earlier rounds still hold

> Later rounds only. Re-check every acceptance criterion from every earlier round in this
> folder: this round changed code they depend on, and their tests passing is necessary but
> not sufficient — a criterion can be satisfied by tests that no longer describe what the
> feature does.

| Round | # | Criterion | Still met | Evidence |
|---|---|---|---|---|

---

## 7. Ship log

| Field | Value |
|---|---|
| Commits | |
| Pushed to | |

---

## 8. Recommendations

> Written in step 8. Only follow-ups that are critical and belong to this work, which most
> rounds do not have: replace the table with `None.` when there are none. Lesser ideas are
> one-line notes in `DEVELOPMENT.md`, not rows here. Not bugs in what this round built —
> those go back through `/build` before the pull request. A critical defect outside what
> section 1 promised, such as a class member it put out of scope, is a recommendation here,
> and its round opens through `/fix`.

| # | Recommendation | Why it is critical | Effort | Decision |
|---|---|---|---|---|
| R1 | | | | |

Decisions: `deferred`, `rejected`, or `next round` — a new numbered file in this folder,
taken back through steps 1 to 7 on the same branch.

---

## 9. Pull request

> Written in step 9, in the commit that opens the pull request — so the URL is not known
> yet and the pull request is found from the branch. The review itself is recorded on the
> pull request thread, not here: this file is `done` from step 9 on.

| Field | Value |
|---|---|
| URL | opened by step 9 — see the branch's pull request |
| Opened as | ready for review |

---

## Halted

> Only if the build stopped. Written by `/build`: the step, the reason verbatim from the
> step that halted, the question for the user — and, once answered, the answer and what
> changed because of it. Never deleted; it is the record of where the plan was thinner
> than the code needed.
