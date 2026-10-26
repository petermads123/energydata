# Min Strøm Consumer API, with price forecasts first

<!-- claude-plan step=8 status=active -->

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
| 3 | Implement | `/implement` | in `/build` | done (re-run after step 6 sent it back, see section 3) |
| 4 | Verify | `/verify` | in `/build` | done (re-run after step 6 sent the work back to step 3, see section 4) |
| 5 | Test | `/test` | in `/build` | done (re-run after step 6 sent the work back, see section 5) |
| 6 | Concept check | `/concept-check` | in `/build` | done (second audit in section 6, after the first sent the work back to step 3) |
| 7 | Ship | `/ship` | in `/build` | done |
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
| `class MinStroemError(Exception)` with `__init__(self, message: str, *, status: int \| None = None, error_code: str \| None = None, reason: str \| None = None, request_id: str \| None = None) -> None` and attributes `status`, `error_code`, `reason`, `request_id` | `client.py` | An API error response or an exhausted retry or holdoff on a response (all four attributes from the response), a 2xx body that is not JSON or not a JSON object (`status` 200, `error_code` `None`), or any other check that fails (a payload whose fields or rows are not what the spec promises (including a number that is not finite or too large, a timestamp out of range and a looked-up address id that is not a UUID), an unusable key, a missing capability flag, sources disagreeing on currency or VAT): all four attributes `None`. `str()` is the message only. *(Corrected at step 4, see section 3.)* | A7 |
| `class MissingApiKeyError(MinStroemError)` | `client.py` | Raised by the first request when the client has no key; message names `API_KEY_ENV`. All four attributes `None`. | A7 |
| `MinStroemClient(api_key: str \| None = None, *, policy: RetryPolicy \| None = None, timeout: float = DEFAULT_TIMEOUT, max_concurrency: int = 2, transport: httpx.AsyncBaseTransport \| None = None)` | `client.py` | `ApiClient` on `BASE_URL`. `api_key=None` reads `API_KEY_ENV` at construction; the key is stripped, and an empty result counts as none. A key that is not entirely `[\x21-\x7e]` (control characters, inner whitespace, non-ASCII) is kept out of the headers and refused at the first request with a `MinStroemError` saying the key has characters an HTTP header cannot carry — never echoing it. Never fails for a missing or bad key at construction. `max_concurrency < 1` is a `ValueError`. `__repr__` returns `MinStroemClient(key=set)` / `MinStroemClient(key=missing)` — never the key. Property `has_api_key: bool`. | A7 |
| `async MinStroemClient.fetch_json(path: str, params: Mapping[str, str] \| None = None) -> dict[str, JsonValue]` | `client.py` | One `GET` through `ApiClient.request` with `Authorization: Bearer <key>`; returns the JSON object. Maps `httpx.HTTPStatusError`, and `RetriesExhaustedError`/`HoldoffTooLongError` that carry a response, to `MinStroemError` (chained) with status, `errorCode`, `reason` (from the body when it is the documented error object, else `None`) and `X-Request-ID`. A `RetriesExhaustedError` without a response (transport failure) propagates unchanged. A 2xx body that does not parse (`ParseError`) or is not a JSON object is a `MinStroemError` (`status` 200, `error_code` `None`), chained. `MissingApiKeyError` (or the bad-key `MinStroemError`) before any I/O. Only awaitable on the client's loop. | A7 |
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
- *(Recorded at step 4; a step 3 omission.)* The `MinStroemError` row of the Public API table
  said a payload "not what the spec promises" carries the 2xx status. Only `fetch_json`'s two
  checks (body not JSON, body not a JSON object) do, with `status=200`. Every other payload
  check (`_parse`, `_build`, `_address_frame`, the forecast checks, the currency/VAT
  agreement checks) and the key and capability checks raise `MinStroemError` with `status`,
  `error_code`, `reason` and `request_id` all `None`: those checks run on a parsed `data`
  list, after `fetch_json` has dropped the response, and a status would say nothing a caller
  could act on. The `fetch_json` row of the plan was precise and is met; the class-row
  wording was loose and is corrected in section 2. A7 promises the four attributes for "an
  API error", which these are not, so section 1 is untouched. Step 5's T7 should assert
  `status == 200` only for the not-JSON and not-an-object bodies, and `status is None` for
  a well-formed JSON object whose rows are wrong.
- The Public API table's `main()` for `client.py` lists addresses' regions and capabilities,
  as planned; the shared "no key, print one line" check is a private helper
  `_showcase_has_key` in `client.py`, imported by the other modules.
- `_series.main()` is offline except for one extra address lookup when a key is set.
- Usage caps in the mock (`MinStroemService`) follow the concept table (366 days at `hour`,
  five calendar years at `day`/`month`/`year`); T13's "hourly/daily usage" wording was read
  that way because the plan's window table spans four years at usage `day`.
- `STRUCTURE.md` and `structure-minstroem.md` already name the seven offline test files that
  step 5 writes, so the structure check reports them as missing until then.
- *(Recorded at step 5; code changes the tests made necessary. Public signatures are
  unchanged; the changes are private helpers and behaviour inside the planned contract.)*
  - `_series._resolve_address` now returns a private `_Address(id, lacks)` instead of the id
    string, with a private `_require(address, capability)`; `_Fetched` gained `starts`, the
    starts of the rows the API returned.
  - `get_prices_with_forecast` applies the shared rule to its second endpoint: with
    `address_id=None` it checks the `prices` flag after the forecast fetch and before `/prices`
    (guide step 9 and the shared rule; only when `/prices` is actually requested).
  - The combined series counts an hour as confirmed when the API returned a confirmed row for
    it, even a row of nulls (guide step 9: "the latest hour with a confirmed row"). Step 3 used
    "total price not NaN", which let the forecast replace a confirmed row and discard its
    variable charge, against A2's "confirmed winning any overlap".
  - Payload checks refuse a number that is not finite (`NaN`, `Infinity`, which `read_json`
    accepts) or too large for a float, a timestamp outside the nanosecond range, and a
    looked-up address id that is not the hyphenated UUID form (it goes into a request path);
    each is a `MinStroemError` with all four attributes `None`.
  - Docstrings: `RetriesExhaustedError` added to the `Raises:` of the eight `get_*` functions
    and `get_json`; the `end` argument of the four usage and cost functions says a lone
    timestamp is refused for `day`/`month`/`year`; the costs `total_price_per_kwh` wording
    follows the spec ("null at zero energy").
  - Test support: `conftest.minstroem_price_row` added its hour in Copenhagen wall-clock time
    (two hours across the autumn change); it now adds it in UTC. `tests/test_minstroem_live.py`
    stepped days with `Timedelta(days=n)`, which lands at 01:00 across a DST change and is
    refused for a day interval; it now steps on the calendar (`_day`), and two live tests were
    added (the combined series cross-checked against its two endpoints; the forecast's hours).
- No deviation touches section 1.
- *(Recorded at the step 5 re-run.)* No package code changed; the work is in the live suite, the offline
  plugin and the test support, listed in section 5. Public signatures are unchanged.

### Re-run of step 3, sent back by step 6 (A8 partially unmet)

Step 6 found `test_live_prices_with_forecast_open_end` crashing on an hour with neither source.
Public signatures and the package are unchanged; the work is in the live suite, the test
support and one new test-support file. Section 1 is untouched.

**Red first.** Before changing the live suite, the plugin below was written, and the suite as
step 6 left it (`b6b3b04`) was run through it, one scenario at a time (21 live tests each):

```
hole              1 failed, 20 passed
gappy_forecast    1 failed, 20 passed
empty_forecast    1 failed, 20 passed
no_prices         2 failed, 14 passed, 5 skipped   (open_end and closed_end: 422 feature_unavailable)
prices_not_found  6 failed, 15 passed              (the address id three times in the output)
plain, no_forecast, two_addresses: green
```

The failing test, under `hole` (verbatim; the same shape under `gappy_forecast` and
`empty_forecast`):

```
___________________ test_live_prices_with_forecast_open_end ____________________
tests/test_minstroem_live.py:171: in test_live_prices_with_forecast_open_end
    f"confirmed {flags.count(False)}, forecast {flags.count(True)}, "
                 ^^^^^^^^^^^^^^^^^^
pandas/_libs/missing.pyx:462: in pandas._libs.missing.NAType.__bool__
    ???
E   TypeError: boolean value of NA is ambiguous
1 failed, 20 deselected in 0.09s
```

The new guard tests (below) against that same live file: 6 failed (`hole`, `gappy_forecast`,
`empty_forecast`, `no_prices`, the id-leak test and the decorator test), 3 passed (`plain`,
`two_addresses`, `no_forecast`, which show no defect).

**What changed.**

- `tests/test_minstroem_live.py`:
  - `test_live_prices_with_forecast_open_end` counts with the Series
    (`flags.eq(False).sum()`, `flags.eq(True).sum()`, `flags.isna().sum()`), asserts the three
    counts add up to the frame, and checks "never confirmed after forecast" through a new private
    `_hours(flags, value)` (`flags.eq(value).fillna(False)`), not a loop over `tolist()`. The
    cross-check test used `combined.index[flags == False]`, which only worked because
    `Index.__getitem__` turns `<NA>` into `False`; it uses `_hours` too, and its two
    `# noqa: E712` are gone. Swept the rest of the file for Python truthiness over a
    `boolean`-dtype value: none left (`addresses[...]` columns are plain `bool`, and the float
    columns hold NaN, not `<NA>`).
  - (a) the open-end and closed-end combined tests also `_need(address, "prices")`, so an address
    with a forecast and no `prices` skips them naming the capability, as the cross-check test did.
  - (b) `forecast[...].dropna().map(pd.notna).all()` (always true) is now `.map(math.isfinite).all()`:
    a priced hour is a finite number.
  - (c) every live test is wrapped by the private decorator `_anonymous_failures`: a
    `MinStroemError` or `RetryError` fails the test with `a request failed: <type>: <message>
    [status=... error_code=... reason=... request_id=...]`, every UUID replaced by `<address>`,
    raised `from None` (the chained `httpx` error carries the URL) with the original frames kept.
    Skips and ordinary assertion failures pass through. The package's messages are unchanged.
    Two assertions that would have printed an id or a key through pytest's own introspection
    were rewritten to assert a computed boolean: the lower-case id check, and the wrong-key test
    (which would otherwise have printed the real key if the package ever leaked it).
- `tests/minstroem_live_mock.py` (new, a pytest plugin, not a test file): loaded with
  `-p minstroem_live_mock`, a dummy `MINSTROEM_API_KEY` and a scenario in `MINSTROEM_LIVE_MOCK`,
  it replaces `MinStroemClient.__init__` so every client, whatever transport it was given, talks
  to a `MinStroemService`, and adds an autouse fixture that keeps every real httpx transport
  refusing. `build_service(scenario, now, api_key)` lays confirmed prices (three days back to
  tomorrow noon) and a 48-hour forecast out from `now` and bends one thing per scenario:
  `plain`, `hole` (two confirmed hours missing, one confirmed null total), `gappy_forecast`
  (confirmed stop at 02:00, forecast starts two hours after the current hour, two missing hours,
  null total first, null variable later), `empty_forecast`, `no_prices`, `no_forecast`,
  `two_addresses` and `prices_not_found` (`/prices` 404 whose reason holds the address id).
- `tests/conftest.py`: the guard's body is now `block_real_transports(monkeypatch)`, called by
  `_no_network` for every test but one marked `live`, and by the plugin for those. Behaviour of
  `_no_network` is unchanged; the guard test in `test_minstroem_spec.py` still passes.
- `tests/test_minstroem_spec.py` (24 new tests, about 20 s because each scenario is a pytest
  subprocess of 21 tests): the whole live suite run once per scenario, asserting the exit code,
  all 21 outcomes from a JUnit file (every test passes, or is skipped exactly where the address
  lacks the capability), the `neither` count of the open-ended combined series per scenario (0 for
  tidy data, at least 1 and 2 where the data has a hole), and that no key, address id or address
  text is in the output; the `prices_not_found` run failing exactly the six price tests with
  status, code, reason and request id in the output and the id nowhere (and no chained-exception
  text); every scenario having a guard; an unknown scenario a `ValueError`; the combined series
  built from each scenario at a spring-change, autumn-change and ordinary `now`; the plugin
  still refusing a real transport and routing a transport-less client to the mock; every live test
  carrying the decorator. The docs test also requires `tests/minstroem_live_mock.py` in
  `STRUCTURE.md` and the rules file.
- `STRUCTURE.md`, `.claude/rules/structure-minstroem.md`, `.claude/rules/structure-utils.md`: the
  plugin, `block_real_transports`, the decorator and the scenarios.

**Green.** All eight scenarios through the plugin: `plain`, `hole`, `gappy_forecast`,
`empty_forecast`, `two_addresses` 21 passed; `no_prices` 14 passed, 7 skipped; `no_forecast` 16
passed, 5 skipped; `prices_not_found` 6 failed as designed, with the id replaced by `<address>`.
Whole tree: `ruff check .` and `ruff format --check .` clean, `mypy` no issues in 57 files,
`pytest` `2642 passed, 21 deselected, 1 warning` (the warning is the pandas deprecation in
`utils/frames.py` noted at step 4); `stop_gate.structure_problems` reports nothing. Nothing in
the offline suite reaches the network: the plugin run is refused by the guard it re-enables.

**Recorded at the step 4 re-run (a step 3 omission, found by the brief's question).** The plugin
laid the mock out around `datetime.now(UTC)` at `pytest_configure`, while the package (the
confirmed-price cap, `fetched_at`) and the live tests (`_day`, the forecast-start check) read the
clock themselves, later. Section 4 shows that a run straddling local midnight breaks the `hole`
scenario's hour-count bound. The plugin gained `NOW_ENV = "MINSTROEM_LIVE_NOW"`: an ISO 8601
instant with an offset around which the data is laid out and to which `pandas.Timestamp.now` is
fixed for the run (restored by `pytest_unconfigure`); unset or empty keeps the real clock, a naive
or unparseable value is a `pytest.UsageError`. `test_minstroem_spec.py` pins every subprocess run
(`PINNED_NOW`) and gained 12 tests. No public signature of the package, no test of the live suite
and nothing in section 1 changed.

---

## 4. Verification log

> Written in step 4: the static half. Command output, not a summary of it.

| Check | Result |
|---|---|
| `ruff check .` | `All checks passed!` (first run and after the doc edits) |
| `ruff format --check .` | `84 files already formatted` |
| `mypy` | `Success: no issues found in 49 source files` |
| `pytest` | `1735 passed, 18 deselected, 1 warning` (the 18 are the live tests, deselected by `addopts`; the warning is a pandas deprecation in `utils/frames.py`, not in this round). No code changed at step 4, so the first run was already green. |
| Plan completeness | every signature in the Public API table exists as written; one wording mismatch in the table itself, fixed (below) |
| `STRUCTURE.md` | in sync, after the auditor's nine edits (below); `stop_gate.structure_problems` reports only the seven offline test files that step 5 writes |
| `python -m <package>.<module>` | all six modules exit 0 without a key (below) |

### Plan completeness

Checked by `inspect.signature` against the Public API table, then by reading each module.

| Plan entry | Result |
|---|---|
| `BASE_URL`, `API_KEY_ENV` | met, values as planned |
| `MinStroemError` (signature, four attributes) | met; **deviation** in the table's description, below |
| `MissingApiKeyError(MinStroemError)` | met; all four attributes `None` |
| `MinStroemClient.__init__` (signature, `max_concurrency < 1` a `ValueError` from `ApiClient`, key stripped, bad key refused at first request, `__repr__`, `has_api_key`) | met; `max_concurrency=0` gives `ValueError`, `repr` is `MinStroemClient(key=set)` / `(key=missing)` |
| `async fetch_json`, `get_json` | met; `fetch_json` is a coroutine function |
| `Aggregation`, `AGGREGATIONS`, `PriceResolution`, `PRICE_RESOLUTIONS` | met, re-exported |
| `get_addresses`, `get_consumption`, `get_solar_export`, `get_consumption_costs`, `get_solar_export_revenue`, `get_prices`, `get_forecast_prices`, `get_prices_with_forecast` | met: parameter names, keyword-only markers, defaults, annotations and return types equal the table |
| `main()` in every module | met: `client`, `_series`, `addresses`, `usage`, `costs`, `prices` |
| `__init__.py` re-exports | met: the fifteen names of guide step 10 and nothing else (`main` is not exported) |
| Spec fixture, `pyproject.toml` marker and `addopts`, `tests/conftest.py`, `tests/test_minstroem_live.py`, README section and PowerShell line, `STRUCTURE.md`, the three rules files | present |

- **Missing:** none.
- **Unplanned:** none. The public names of each module are exactly the planned ones; every
  helper (`_showcase_has_key`, `_api_error`, `_Endpoint`, ...) starts with `_`.
- **Deviation, not recorded at step 3:** the `MinStroemError` row said a payload that is not
  what the spec promises carries the 2xx status. The code does that only in `fetch_json`
  (`status=200` for a body that is not JSON or not an object) and leaves all four attributes
  `None` for every other check. The code stands; recorded in section 3 and the plan row
  corrected in section 2. It does not touch section 1 (A7 covers API errors).

### `structure-auditor`

The auditor ran before this step and returned nine edits (eight in
`.claude/rules/structure-minstroem.md`, one in `STRUCTURE.md`). Each was checked against the
code and `tests/conftest.py` as they stand; no code changed at this step, so none was
superseded. All nine applied:

1. `MinStroemError` and `fetch_json` rows: the real `status` behaviour (this is the
   deviation above). Applied.
2. `get_forecast_prices` row: `attrs` keys named, no `resolution`, empty-frame index. Applied.
3. Tests section: the conftest support with full signatures (`ADDRESS_ID`, `minstroem_address`,
   `minstroem_price_row`, `MinStroemService`, its caps and `calls`/`client`), `_no_network`
   named as "the no-network guard". Applied.
4. `get_prices_with_forecast` row: `is_forecast` values, the end rules, `attrs`, capability.
   Applied.
5. Shared paragraph: the `address_id=None` rules. Applied.
6. `_series.py`: dropped the private `_gather_ordered` name, typed the tuples; Tests: full file
   names. Applied.
7. Intro and `_series.main` row: which showcases are offline. Applied.
8. Live-suite sentence: address choice, skips, report lines. Applied.
9. `STRUCTURE.md`: `client.py` exports and the conftest helpers and fixtures. Applied.

### Showcases (`python -m energydata.minstroem.<module>`, `MINSTROEM_API_KEY` unset)

Every module exits 0 and prints the one-line notice; each also prints the expected
`RuntimeWarning` for a module re-exported from `__init__.py`. `_series` is offline and prints
its two calendar windows and the refused month start:

```
costs per hour: 2 windows over 2025-03-01 to 2026-04-01
  2025-03-01T00:00:00+01:00 -> 2026-03-01T00:00:00+01:00
  2026-03-01T00:00:00+01:00 -> 2026-04-01T00:00:00+02:00
refused: start must be the first of a month at local midnight for interval 'month', got '2026-03-15T00:00:00+01:00'
Set MINSTROEM_API_KEY to run this showcase against the live Min Strøm API.
```

There is no key in this environment, so the with-key branches were exercised instead
against the in-repo `MinStroemService` mock (a scratch script that sets a fake key and gives
the client the mock transport; nothing committed). All six `main()` ran to the end and
printed: 1 address in `DK1` with its capabilities; consumption per day `(7, 2)`; solar export
per hour `(24, 2)`; costs per month `(1, 5)` in `DKK`; revenue per day `(7, 5)`; a 29-hour
forecast with `fetched_at`; the combined series; and quarter-hourly history `(100, 2)` for
the 25-hour day 2026-10-25. (The combined series showed 0 forecast hours there only because
the mock generates confirmed rows for every requested hour, so confirmed wins everywhere, as
specified; the real API is the live suite's to check.) The showcases are in the required form:
every argument a named variable, the call on its own line, the result named then printed, and
the fixed-set arguments (`aggregation`, `resolution`, `interval`, `family`) carry their
accepted values in a same-line comment.


### Re-run, after step 6 sent the work back to step 3

Step 3 re-ran (live-suite NA crash, the offline guard for it, the pytest plugin). This entry is
the second verification; the first, above, stays as it was.

| Check | Result |
|---|---|
| `ruff check .` | `All checks passed!` |
| `ruff format --check .` | `92 files already formatted` |
| `mypy` | `Success: no issues found in 57 source files` |
| `pytest` | `2654 passed, 21 deselected, 1 warning` in 84 s (2642 at step 3's close plus the 12 new spec tests below; the 21 are the live tests; the warning is the pandas deprecation in `utils/frames.py` noted before, not in this round) |
| Plan completeness | every signature of the Public API table equals `inspect.signature`; nothing missing, nothing unplanned (below) |
| `STRUCTURE.md` and rules | the auditor's four edits applied, plus the new env var; `stop_gate.structure_problems`, `stray_test_files` and `missing_init_files` all `[]` |
| `python -m energydata.minstroem.<module>` | all six exit 0 without a key; with a key (fake, against the mock) all six `main()` ran to the end |

**Is the clock a real flakiness risk? Yes, but only at local midnight, and only in the spec suite.**
Step 3 named "an hour or local midnight". The plugin lays the mock out around the instant
`pytest_configure` runs; the package and the live tests read `pd.Timestamp.now` later, so the
question is what a gap between the two instants does. I simulated it faithfully (a scratch
plugin in the scratchpad, not committed: it pins `pd.Timestamp.now` to a chosen instant and lays
the mock out one second earlier), over `plain`, `hole`, `gappy_forecast`, `empty_forecast` and
`two_addresses`:

| Instant pair | Live suite | Hours with neither source, against `EXPECTED_NEITHER` |
|---|---|---|
| layout 23:59:59.5, clock 00:00:00.5 next day | 21 passed in every scenario | `plain` 0, `hole` **0** (bound is at least 1: **fails**), `gappy_forecast` 3, `empty_forecast` 36, `two_addresses` 0 |
| layout 14:59:59.5, clock 15:00:00.5 | 21 passed in every scenario | all within bounds |
| layout and clock 15:00:30 (control) | 21 passed | all within bounds |

An hour boundary is harmless. A midnight boundary moves "today" under the data: `hole` lays its
two holes on the old day, `_day(0)` starts the combined series on the new one, the holes fall out
of the frame (`neither` 0 in the table) and `test_every_live_test_passes_against_the_mock_in_every_shape_of_data[hole]`
would fail on its lower bound of 1. The live suite itself never fails (it does not assert the count), so
the user's own run is unaffected. The window is about a second a day, so the odds per run are
tiny, but it fails the same way every time it opens, in CI or at the stop gate, with nothing in
the output pointing at the clock. Judged real, fixed inside the build.

**Fix.** The pin the brief suggested, extended to the clock the code reads: with
`MINSTROEM_LIVE_NOW` set the plugin lays the mock out around that instant *and* fixes
`pandas.Timestamp.now` to it, because pinning only the layout would put it a day away from the
clock the package and the tests read. A pinned run is the same on any day, so the spec suite now
also runs `plain` on the spring change day, the day after it, the autumn change day and the day
after it (the dates the live tests' date arithmetic broke on, see step 5), and `hole` at local
midnight. Mutation check: with the `Timestamp.now` patch removed, 5 of the new tests fail
(the four DST runs and the clock probe, run separately); with it back, all pass. The seven scenarios that pass were also run
by hand at six pinned instants (an ordinary day, local midnight, both change days and the days
after them): 21 passed, or the skips of `no_prices` and `no_forecast`, in all 42 runs.

| New test (`tests/test_minstroem_spec.py`) | Proves |
|---|---|
| `test_every_live_test_passes_whichever_instant_the_clock_is_pinned_to` (5) | the whole live suite passes at the DST days and at local midnight, within `EXPECTED_NEITHER` |
| `test_the_plugin_pins_the_clock_for_the_package_and_the_tests` | `pd.Timestamp.now` is the pinned instant in Copenhagen, in UTC and naive; `fetched_at` and the first forecast hour come from it |
| `test_without_a_pinned_instant_the_plugin_leaves_the_real_clock` (2: unset, empty) | the clock stays real |
| `test_a_pinned_instant_without_an_offset_or_not_a_date_is_a_usage_error` (4) | a naive time, `noon` and a blank are refused, the message naming `MINSTROEM_LIVE_NOW` and the value |

The existing scenario runs now pass `NOW_ENV: PINNED_NOW` (`2026-06-15T12:00:00+02:00`); none of
their assertions changed.

### Plan completeness (re-run)

Checked by `inspect.signature` against the Public API table, and by listing the public names of
every module. `get_addresses`, the seven series and forecast functions, `MinStroemClient`,
`MinStroemError`, `fetch_json` (a coroutine function), `get_json`, `has_api_key`, `BASE_URL`,
`API_KEY_ENV`, the two literal pairs and `MissingApiKeyError(MinStroemError)` all match. The
package exports seventeen names (the first log's "fifteen" miscounted the same list). Every
module has a `main()`. Since the first step 4 the package gained only private helpers
(`_Address`, `_require`, `_server_address_id`), all recorded in section 3.

- **Missing:** none. **Unplanned:** none. **Deviation not recorded in section 3:** none (the
  plugin's clock pin is recorded in section 3 now, as a step 3 omission).

### `structure-auditor` (re-run)

Four edits, on the code as the step 3 re-run left it. Each was checked against the code as it
stands; the clock pin superseded none, but it extended the first. All four applied.

1. `structure-minstroem.md`, the plugin paragraph: scenario descriptions corrected against
   `build_service` (`hole` drops two confirmed hours and nulls the total at 03:00; `gappy_forecast`
   ends the confirmed prices at 02:00 and serves `[+2 h, +40 h)`), the two hooks, the default
   scenario, the `UsageError` and the instant retries named. Applied, and extended with
   `MINSTROEM_LIVE_NOW`, the DST and midnight runs and the clock tests.
2. The live-suite paragraph: the failure wrapper is no longer named, and `RetryError` shows only its
   message (only a `MinStroemError` has the four attributes). Applied.
3. The rules file's `paths` gained `tests/minstroem_live_mock.py`, so it loads when Claude edits
   the plugin. Applied.
4. `structure-utils.md`: the guard calls `block_real_transports`, it is not that function. Applied.

`STRUCTURE.md` needed no edit from the auditor; one clause was added for the pinnable clock.

### Showcases (re-run)

Without a key all six modules exit 0 and print the one-line notice; `_series` prints its two
windows and the refused month start, as before. No `main()` code changed since the first step 4,
so the required form still holds. The with-key branches were run again against the mock (a
scratch script, not committed): 1 address in `DK1` with its capabilities; consumption per day
`(7, 2)`; solar export per hour `(24, 2)`; costs per month `(1, 5)` in `DKK`; revenue per day
`(7, 5)`; a 48-hour forecast with `fetched_at`; the combined series of 66 hours, 30 of them
forecast; quarter-hourly history `(100, 2)`. A first attempt of that script re-ran `client.py` as
`__main__`, which made a fresh `MinStroemClient` that the mock patch did not reach, so it sent one
request with the fake key `fake-showcase-key` to the real API and got a 401; nothing else left
the machine and no real key was involved.

---

## 5. Test log

> Written in step 5: the dynamic half.

Seven offline suites, 883 tests, none touching the network (the whole tree: 2618 passed,
21 live tests deselected; `ruff check .`, `ruff format --check .` and `mypy` clean). The live
suite (21 tests, two added) was run only against the in-repo mock through a scratch plugin kept
out of the repo, to test its own logic: all 21 passed there, after the day-arithmetic fix below.
It collects and skips cleanly with `-m live` and no key (21 skipped, none failed or erroring).
`stop_gate.structure_problems` reports nothing.

| Intent | Test names | Result |
|---|---|---|
| T1 forecast | `test_minstroem_prices.py`: `test_the_forecast_is_an_hourly_frame_with_gaps_and_nulls_as_nan`, `..._attrs_are_exactly_the_documented_four`, `test_an_empty_forecast_is_the_documented_empty_frame`, `test_a_single_forecast_row_...`, `..._autumn_change_are_25_...`, `..._spring_change_are_23_...`, `test_a_null_price_field_is_nan_with_the_row_kept`, `test_a_row_that_is_not_one_whole_hour_or_repeats_is_a_minstroem_error` (7 shapes), `test_a_forecast_response_with_the_wrong_resolution_currency_or_shape_is_an_error`, `test_the_forecast_capability_is_named_whichever_route_finds_it_missing`; `test_minstroem_series.py`: capability, id and lifecycle cases for `forecast` | green |
| T2 combined | `test_minstroem_prices.py`: `test_confirmed_then_forecast_with_the_flag_and_confirmed_winning_the_overlap`, `..._a_confirmed_gap_before_the_last_confirmed_hour_is_not_filled_...`, `..._never_requested_beyond_local_midnight_two_days_ahead`, `..._wholly_beyond_the_cap_...`, `..._straddling_the_cap_...`, `test_without_an_end_it_runs_to_the_last_forecast_hour` (+ later confirmed rows), `test_with_an_end_the_frame_is_exactly_the_period_...`, `test_a_forecast_that_ends_before_the_start_gives_the_empty_frame_...`, `test_an_empty_forecast_runs_to_the_confirmed_cap`, `test_a_dst_day_is_25_or_23_confirmed_hours`, `test_forecast_rows_outside_the_period_are_dropped`, the null-price tests, `test_sources_that_disagree_on_currency_or_vat_are_an_error`, the capability tests, `test_a_confirmed_hour_never_follows_a_forecast_hour` | green |
| T3 addresses | `test_minstroem_addresses.py` (49): every field and flag to its column, nulls `None`, text exact, order, empty frame with dtypes, 25 malformed shapes, extra keys ignored | green |
| T4 address default | `test_minstroem_series.py`: `test_the_only_address_is_looked_up_once_and_used`, `..._skips_the_lookup`, zero and several addresses, non-UUID ids (11 forms) refused before key and request, canonical forms, server ids that are not UUIDs, all seven address-scoped functions | green |
| T5 series | `test_minstroem_series.py`, `_usage.py`, `_costs.py`, `_prices.py`: index per interval incl. 23/25-hour and 92/100-quarter days, missing is NaN/`<NA>`, zero stays zero, dtypes, `attrs`, misaligned bounds (12 cases) before any request, `from`/`to`/interval parameters | green |
| T6 windows | `test_a_period_over_the_cap_is_split_aligned_and_equals_one_unbounded_call` (13 table rows), `test_windows_end_on_calendar_boundaries_and_abut`, `test_every_window_touches_no_more_local_days_than_the_cap`, `test_usage_windows_never_touch_more_than_five_calendar_years`, per-family long-period tests | green |
| T7 errors | `test_minstroem_client.py`: each documented status, all nine spec error examples (`test_minstroem_spec.py`), non-JSON bodies, 429 long and short `Retry-After`, exhausted 5xx, transport failure stays `RetriesExhaustedError`, non-object and non-JSON 2xx | green |
| T8 key | `test_minstroem_client.py`: argument, environment (read once, stripped), `Bearer`, missing and blank key, unusable keys (7) never echoed in `str`/`args`/chain/traceback, 401 with either key absent from everything and captured output | green |
| T9 lifecycle | `test_minstroem_series.py`: created client closed after success, error and missing key; passed client open and reusable; closed passed client is `RuntimeError` (8 functions each) | green |
| T10 live wiring | `test_minstroem_spec.py`: deselected with a key set, collected with `-m live`, skipped with no key, the no-network guard exempts only `live`, summary block only when a line was reported (all in subprocesses, no network); the live suite covers every function, the forecast and combined most | green |
| T11 contract | `test_minstroem_spec.py`: fields, nullability, enums, paths, query parameters, response shape, capability names, address columns against the spec; every request the eight functions make is a documented operation; the mock's bodies validate against the spec; the spec's own examples and error responses go through the package | green |
| T12 docs | `test_minstroem_spec.py`: README names every function, class, column and the exact PowerShell line; `STRUCTURE.md` and the rules file name every module and test file; `stop_gate.structure_problems` clean; exports; `python -m` of all six modules | green |
| T13 mock | `test_minstroem_series.py` (bottom): bearer, parameter validation, outward expansion, every cap at and over its limit (partial day counts, yearly cost across New Year refused), 404/422, sparse and generated rows, gaps, currency, forecast, overrides, request log | green |

### Bugs the tests found and how each was fixed

Each was red against the step 4 code (`62541b7`) and is green now; 15 new tests fail on the old
code (checked by restoring it temporarily), and a 16-mutation spot check of the production code
(window sizes, NaN fill, UTC/local, caps, the stitching rule, key pattern, strip, request id,
capability text) left no survivor.

| Finding | Class | Fix |
|---|---|---|
| `NaN`/`Infinity`/`1e999` in a number field passed `_number` (a present row became NaN, indistinguishable from a missing one); a 400-digit integer raised `OverflowError` | code bug | `_number` refuses non-finite and oversized numbers as `MinStroemError` |
| A row `start` outside the ns range (`2300-...`) raised `OutOfBoundsDatetime` out of the call | code bug | the whole conversion is inside the `try` of `_timestamp` |
| An address id from `/me/addresses` that is not a UUID went into the request path unchecked (`a/b` changed the path) | code bug vs the plan ("nothing can be smuggled into the path") | `_server_address_id` requires the hyphenated UUID form, else `MinStroemError`; used as sent |
| `get_prices_with_forecast` with `address_id=None` on an address without `prices` sent a `/prices` request and relied on the 422 | code bug vs the shared rule | `_require(address, "prices")` before `/prices`, see below |
| A confirmed row of nulls at the tail was replaced by the forecast, discarding its non-null variable charge | code bug vs A2 and guide step 9 | confirmed is "a row exists", see below |
| `minstroem_price_row` produced a two-hour row across the autumn change | test-support bug | the hour is added in UTC (found by the 25-hour forecast test; the package's own "one whole hour" check was right to refuse it) |
| Live tests stepped days with `Timedelta(days=n)`: 7 days before today crosses a DST change in late March and late October and lands on 01:00, so the `day` live tests (consumption, solar export, costs, revenue) would have raised `ValueError` on the user's run | test bug (never run until now) | `_day(offset)` steps on the calendar; the length checks are exact (`_hours_between`) instead of `in (47, 48, 49)`; pinned by a spec test |

### The two decisions the brief singled out (judged on the record, no halt)

**Address with `forecastPrices` but without `prices` (input-space 4, contract 3).** Section 2's
shared rule is general: with `address_id=None` the function "checks the endpoint's flag before
requesting it", and with an explicit id "the server's 422 mapping names it instead". The combined
series requests two endpoints, so the rule applies to each; the planned outcome for a missing
capability is an error naming it (A1 does the same for the forecast), and the guide's "without a
forecast it is `get_prices`" covers only the forecast. A2 describes the output for an address
that can supply both; it does not promise a forecast-only series for an address that cannot
supply confirmed prices, and inventing one would be new behaviour rather than a reading of A2.
So section 1 is not amended and nothing was guessed: the result is `MinStroemError` naming
`'prices'` (status 422 with an explicit id, none from the pre-check), which the code already gave
through the 422; the only deviation was the wasted `/prices` request, now removed. The check
sits where `/prices` would be requested, so a period wholly beyond the confirmed cap (forecast
only, no `/prices` request) works for such an address, exactly as it does with an explicit id
(pinned by `test_without_the_prices_capability_a_period_that_needs_no_confirmed_price_still_works`).
If the user would rather have a forecast-only series for such an address, that is a new
criterion, not a fix.

**Null totals in the combined series (input-space 3, contract 4).** A2: "confirmed prices
wherever they exist, forecast only for hours after the last confirmed hour, confirmed winning any
overlap"; guide step 9: "`last_confirmed` is the latest hour with a confirmed row". The concept
also treats the data as sparse rows, where a missing *row* is unknown. Step 3 implemented
"confirmed" as "total price not NaN", so a confirmed row with a null total lost to the forecast
and its variable charge was discarded: that contradicts "confirmed winning any overlap" and the
guide's "row". Settled by A2's wording, not a new decision: an hour is confirmed when the API
returned a row for it, nulls and all. Symmetrically a forecast row of nulls is still a forecast
hour (`is_forecast` `True`, prices NaN), and `<NA>` means neither source has a row. Pinned by
`test_a_confirmed_row_of_nulls_is_still_the_confirmed_hour_and_wins_the_overlap`,
`..._in_the_middle_is_confirmed_nan_not_forecast` and
`test_a_forecast_row_with_a_null_total_is_forecast_with_a_nan_total`. The spec types the total as
non-null, so this only matters for out-of-contract data.

### Remaining contradictions, each judged

| Report item | Judgement |
|---|---|
| Error chain overwritten on windowed paths (contract 1) | **Limit of reusing `utils`, not fixed here.** `_gather_ordered` re-raises `failures[...] from group_error`, replacing the failing window's `__cause__` (the `httpx` error) with an `ExceptionGroup` that contains the error itself. Status, code, reason and request id survive (A7 holds), no group escapes, and the direct, address and forecast paths keep their chain. `utils` is unchanged this round (plan); pinned by `test_a_windowed_failure_still_carries_the_http_error_of_a_single_request` so a change shows. Candidate `DEVELOPMENT.md` note for step 8. |
| `RetriesExhaustedError` missing from `Raises:` (contract 2) | docstring gap, fixed in nine docstrings |
| Lone timestamp refused for `day`/`month`/`year`, message names `end` (input-space 5, contract 8) | docstring gap, fixed (`end:` of the four usage and cost functions); the message names the derived bound and its value, left as is |
| `total_price_per_kwh` "NaN at zero energy" (input-space 6) | the spec says the API sends null at zero energy; docstring reworded, code passes the API's value through (pinned) |
| Open-ended frame stops at the last forecast hour even with later confirmed rows (contract 5) | intended, A2 as written ("without `end` it runs to the last forecast hour"); pinned by `test_without_an_end_the_frame_stops_at_the_last_forecast_hour_even_with_later_confirmed_rows` |
| Blank explicit key does not fall back to the environment (contract 6) | intended, the docstring says only `None` reads it; pinned |
| `repr` shows `key=missing` for an unusable key (contract 7, input-space 7) | intended, the plan defines two states; pinned |
| Explicit ids canonicalised to lowercase, looked-up ids used as sent (contract 9) | intended and pinned; whether the real API is case-sensitive on ids is only the live suite's to say, and `test_live_listed_address_id_works_as_listed` is there for it |
| `status` 200 for any unreadable 2xx (contract 10) | section 3, pinned |
| `MinStroemClient(api_key=b"key")` raises `TypeError` (input-space 7) | a type-annotation violation, not a "bad key"; skipped |

### Wrong expectations in my first draft of the tests (test fixed, not the code)

Month offsets across the March change are `[1, 2, 2]`, not `[1, 1, 2]` (the change is on the
29th); a request of 365 days plus one hour touches 366 days, which the mock accepts, so the
over-cap control uses a longer period and the code's 365-day windows are the stricter reading; a
partial first day counts for the cap, which fixed the arithmetic of the partial-day test; a
duplicate-start error names the value but not the endpoint; `--collect-only -q` prints a tree
because `addopts` has `-v`.

Edge cases considered and deliberately skipped, with reasons:

- **Which concurrent window's error wins** when two fail: not deterministic with a mock that
  answers at once; the rule is `utils`' and `test_chunking.py` covers it.
- **Purity of `start`/`end`:** they are immutable `str`/`date`/`datetime`; the only mutable
  argument, `params`, has a test.
- **Very long strings, unicode ids:** nothing in the code limits length; `uuid.UUID` and the
  canonical-form comparison already reject non-canonical text (a non-ASCII digit is normalised
  away or refused).
- **The real API's behaviour** (cap counting, a future `to`, rounding, row order, id case): the
  live suite only, on the user's machine; the offline suite proves the documented contract.
- **Rate-limit pacing** (60/min, 2,000/day): by design the client only retries a 429.
- **A client shared across threads:** `ApiClient`'s contract, covered by `test_api_client.py`.

### Re-run of step 5, after step 6 sent the work back (the live suite)

Scope: the package's public functions are unchanged and were already well tested, so this pass
is about what the user will run with a real key and paste back for approval (A8): the live suite
(`tests/test_minstroem_live.py`), its offline plugin (`tests/minstroem_live_mock.py`) and
`block_real_transports` in `conftest.py`. **No package code changed**; no public signature
changed. Nothing was sent to the real API: every run went through the plugin (which refuses real
transports) or the in-process mock. Two `test-designer` readers (`input-space`, `contract`) ran
first; between them they found six certain defects in the live suite and plugin and a handful of
suspicions. Every item was judged on the record below. Nothing amends section 1.

**The PowerShell line is unchanged.** `--tb=short` stays; the README gained one sentence after
the line (the output is clean under every `--tb` style, but `-l` / `--showlocals` prints every
local, so do not add it) and the `MINSTROEM_ADDRESS_ID` note says any case works. The spec test
that pins the line (`LIVE_COMMAND`) is untouched and passes.

**Red first.** The guard tests were written before the fixes and run against the code as step 4
left it (`0821b4f`), together with the existing request-id assertions rewritten for UUIDs:
72 failed (23 in `test_minstroem_client.py`, 3 in `test_minstroem_series.py`, 46 in
`test_minstroem_spec.py`), 661 passed. The 35 request-id failures are the old mock's `req-N`
against assertions that now expect a UUID; the other 37 are the defects:

| Defect (red run) | Evidence |
|---|---|
| `now.floor("h")` on a Copenhagen time raised in the repeated autumn hour | the three `repeated-hour-*` rows of `test_every_live_test_passes_whichever_instant_the_clock_is_pinned_to`: `test_live_forecast_prices` failed |
| `test_live_address_default` failed on an account with no address | new scenario `no_addresses`: `ValueError: no addresses on this account` against `match="address_id"` |
| `_anonymous` erased the request id (a UUID in the real API) as if it were an address | `test_a_failure_report_keeps_a_uuid_request_id_...`, `..._removes_an_address_id_the_reason_repeats`, `test_one_uuid_can_be_kept_whatever_its_case`, and the end-to-end `test_a_failed_live_request_reports_its_facts_...` (`request id <address>`) |
| the plugin gave the mock the unstripped key, the client strips it: every request 401 | `test_the_plugin_accepts_a_padded_key_the_way_the_client_strips_it` (3) |
| a blank key passed the plugin's check; the live module's `skipif` did not strip either | `test_the_plugin_refuses_a_missing_or_blank_key[blank, newline]`; `test_dash_m_live_with_a_blank_key_skips_every_test_like_no_key[space, tab-newline]` and `test_every_live_test_passes_with_a_key_that_has_whitespace_around_it` |
| an unknown or empty `MINSTROEM_LIVE_MOCK` was an `INTERNALERROR` | `test_a_scenario_that_does_not_exist_is_a_usage_error_naming_the_variable` (4), `test_an_empty_scenario_variable_means_the_default_scenario` |
| `MINSTROEM_LIVE_NOW` at the end of the calendar was an `INTERNALERROR` | `test_a_pinned_instant_the_data_cannot_be_laid_out_around_is_a_usage_error` |
| `gappy_forecast` lost its gap for `now` in local `[00:00, 01:00)` | `test_the_gappy_forecast_starts_after_a_gap_at_any_time_of_day[00:00, 00:30, 00:59]`. The whole-suite rows at those times passed on the old code (the two forecast holes keep `neither` at 2), so the data-level test is the guard. |
| module-scoped fixture setup ran before the transport block | `test_a_module_scoped_fixture_is_set_up_with_the_real_transports_already_refusing` |
| `MINSTROEM_ADDRESS_ID` compared case-sensitively and unstripped, and never exercised offline | `test_the_address_named_by_minstroem_address_id_is_the_one_under_test[upper-case-id, padded-id]` |
| the anonymity promise held only under `--tb=short` | `test_no_traceback_style_shows_the_key_an_address_id_or_the_address_text[auto, long]`: the `address` fixture and the request path were printed. A hand run at the same point showed `address = {'id': '5b0ac4d2-...', 'label': 'Home', 'formatted_address': 'Testvej 1, 1000 Testby', ...}` and the id as a frame argument. |
| the cross-check assumed one snapshot across day-ahead publication | `test_the_cross_check_survives_day_ahead_publication_between_its_requests`: `KeyError` / failed assert on hours the later forecast no longer holds |
| the probes inherited `MINSTROEM_LIVE_MOCK` / `MINSTROEM_LIVE_NOW` from the shell | shown by hand (the strip list changed in the same edit as the test): the real-clock probe with `MINSTROEM_LIVE_NOW=2001-01-01T00:00:00+00:00` exported fails; the fix is the strip list of `_pytest`, pinned by `test_probes_do_not_inherit_plugin_variables_left_in_the_shell` |
| the mock sent `req-N` request ids although the spec types the header `format: uuid` | `test_the_mocks_request_ids_are_uuids_like_the_specs_header` |
| `_describe`/`_anonymous_failures` helpers, the `<address>` repr types | new in-process tests (red: attribute errors on the old code) |

Not red on the old code, written as regression guards (the designers expected them green):
`test_unconfiguring_the_plugin_restores_...` (1 and 2 configure cycles),
`test_a_pinned_instant_is_read_in_any_offset_form_and_becomes_utc` (4) and the two refusals,
`test_the_forecast_is_laid_out_on_utc_hours_whatever_the_offset_of_now` (2),
`test_the_plugin_routes_a_client_given_a_real_transport_and_refuses_sync_httpx`,
`test_the_block_names_the_url_spares_mock_transports_and_is_undone_after_each_test`,
`test_hours_treats_an_unknown_flag_as_neither_and_keeps_the_zone`, `test_every_form_of_an_address_id_is_replaced` (7),
`test_other_exceptions_pass_through_a_live_test_untouched` (3),
`test_the_package_and_the_live_suite_read_the_clock_only_through_timestamp_now`, and the
`gappy_forecast` rows at 01:00, 12:00 and 23:30.

**What changed (all in test support and the live suite).**

- `tests/test_minstroem_live.py`: `now` is floored in UTC; `_anonymous(text, *, keep=None)` keeps
  one UUID (the request id) and `_describe` passes `error.request_id`; `_anonymous_failures` raises
  with the traceback cut at the test's own frame (`_test_frame`), because the package's frames
  carry the request path as an argument and `--tb=long` prints it; `_Address(dict)` and
  `_Private(str)` have `repr`s `<address>` and `<private>`, so a report of a frame's arguments (the
  `address` fixture, the id from `_need`, the keys) shows nothing; the `address` fixture matches
  `MINSTROEM_ADDRESS_ID` without regard to case or space; `skipif` and the wrong-key test strip the
  key; `test_live_address_default` expects a `ValueError` for no address as well as for several and
  asserts on a computed boolean (the message for several lists every id and label); the
  cross-check makes four requests (forecast, combined, prices, forecast), always compares the
  confirmed hours, compares the forecast value for value only when it is the same before and
  after, and otherwise requires the hours to be in one of the two snapshots and says so in its
  report line. The module docstring says what it promises and what defeats it (`-l`).
- `tests/minstroem_live_mock.py`: the key is stripped and a blank one is a `pytest.UsageError`; an
  unknown scenario is a `UsageError` naming `MINSTROEM_LIVE_MOCK` and the value, an empty one is
  `plain`; an instant the data cannot be laid out around is a `UsageError`; real transports are
  blocked in `pytest_configure` (the per-test autouse fixture is gone, it is redundant);
  `pytest_unconfigure` restores them; new scenario `no_addresses`; `gappy_forecast` confirmed
  prices end at `min(02:00 today, current hour)`; the docstring says its offsets are elapsed hours.
- `tests/conftest.py`: `minstroem_request_id(number)` and the mock's `X-Request-ID` is that UUID;
  `_no_network`'s docstring says a module- or session-scoped fixture is set up before it.
- `tests/test_minstroem_client.py`, `test_minstroem_series.py`: the mock-derived `req-N`
  assertions use `minstroem_request_id` (the two `MinStroemError(request_id="req-1")` that build an
  error by hand are unchanged).
- `README.md`, `STRUCTURE.md`, `.claude/rules/structure-minstroem.md`: the sentence on `-l`, the
  address-id case, the helper, the ninth scenario and the plugin's behaviour.

**New tests (72 more than at the step 4 close, 2654 to 2726; all in `tests/test_minstroem_spec.py`, plus the
rewritten request-id assertions in the client and series suites).**

| Group | Proves |
|---|---|
| Plugin refusals (key, scenario, instant) | a missing/blank key, an unknown scenario or an out-of-range instant is a clean `UsageError` (exit 4, naming the variable and value, no `INTERNALERROR`); an empty scenario is `plain`; a padded key works; every offset form of the instant becomes UTC |
| Environment | the probes ignore `MINSTROEM_LIVE_MOCK` / `MINSTROEM_LIVE_NOW` left in the shell |
| Plugin mechanics | set up and torn down in process (client, clock, both transports restored, twice in a row); the module-scoped-fixture gap closed; a client given a real transport is still answered by the mock and sync `httpx` refused; `block_real_transports` names the URL, spares `MockTransport` and does not outlive its test |
| Data | `gappy_forecast` has its gap at six times of day; the forecast lies on UTC hours for any offset of `now`; the whole live suite passes in both passes of the repeated autumn hour and on the hour |
| Whole-suite variants | a padded key; the `no_addresses` scenario (17 skip naming the account, the other 4 pass); `MINSTROEM_ADDRESS_ID` as the cannot-forecast address, upper case, padded and unknown; no key, id or address text under `--tb=short/auto/long/native`; a blank key skips like none (3) |
| Summary (inside the existing scenario test, one row per scenario) | one line per test that reported, and for `plain` the four lines that matter, exactly, at the pinned instant |
| Live-suite helpers, in process | a failure report keeps a UUID request id and removes only the address id, whatever its case or place; a `RetryError` becomes an anonymous assertion at the test frame only; skips, assertions and interrupts pass through; `_hours` treats `<NA>` as neither; `_Address` / `_Private` show nothing; the cross-check survives a publication between its requests and still compares values when the forecast holds still; the README and the module docstring name `--showlocals`; the mock's request ids are UUIDs as the spec's header is; the package and the live suite read the clock only through `pd.Timestamp.now` |

**Mutation checks.** The red run is the mutation check of the fixes themselves: the old code is
the mutant, and every defect in the table failed at least one new test. Two mutations of the new
cross-check were run by hand on top of the fix: forcing `held_still` true makes the publication
test fail (`forecast hours not in the forecast`), and comparing the forecast against `+ 1` makes
the `plain` whole-suite row and `test_a_forecast_that_holds_still_is_compared_value_for_value`
fail; removing the "in neither snapshot" assertion makes
`test_a_combined_forecast_of_hours_neither_snapshot_had_fails_the_cross_check` fail (a moving forecast
excuses a value mismatch, not an invented hour).

**Whole tree.** `ruff check .` and `ruff format --check .` clean (92 files), `mypy` no issues in
57 files, `pytest` `2726 passed, 21 deselected, 1 warning` in about 2 minutes (2654 before this
pass; the warning is the pandas deprecation in `utils/frames.py` noted at step 4). A hand sweep (not
committed) of the eight passing scenarios at eleven awkward instants (midnight, 23:59:59, both
sides of each DST change, both passes of the repeated hour, New Year) gave 88 runs and no
failure. `stop_gate.structure_problems`, `stray_test_files` and `missing_init_files` all `[]`.

### Both readers' items, judged

| Item | Judgement |
|---|---|
| `floor("h")` in the repeated hour (contract 1) | **Bug in the live suite, fixed** (UTC floor), pinned by three whole-suite rows |
| zero addresses fails `test_live_address_default` (contract 2) | **Bug in the live suite, fixed**; new scenario `no_addresses` |
| request id erased (input-space 1, contract 3) | **Bug in the live suite, fixed**; the mock now emits UUIDs so the next such bug cannot hide |
| key not stripped by the plugin / `skipif`, blank key passes (input-space 2 and 6, contract 8) | **Bugs, fixed** |
| bad / empty scenario is `INTERNALERROR` (input-space 5, contract 14) | **Fixed**; empty is `plain`, as the rules file said |
| probes inherit the shell's plugin variables (input-space 3) | **Fixed** in `_pytest` |
| `gappy_forecast` gap vanishes at local `[00:00, 01:00)` (input-space 4) | **Fixed** |
| module-scoped fixture before the block (contract 6) | **Fixed for the plugin**; see below for `_no_network` |
| `MINSTROEM_ADDRESS_ID` untested and case-sensitive (contract 5) | **Fixed and tested**; the package accepts an explicit id in any case, the suite now does too |
| cross-check assumes one snapshot (contract 7) | **Flake risk on real data, fixed** |
| anonymity only under `--tb=short` (contract 4) | **Fixed for every `--tb` style**; `--showlocals` cannot be defended and is documented in the docstring and the README |
| unconfigure, `RetryError` branch, pass-through, pinned forms, `_hours`, UTC layout, routing, block semantics, summary (input-space 7 to 15, contract 9, 10, 12, 13) | **Tests written**, green on the old code except where listed above |
| clock read only through `Timestamp.now` (input-space 10, contract 11) | **Static test written** (green); the pin patches only that call |
| `at()` adds elapsed hours, so "02:00 today" / "tomorrow noon" are an hour off wall-clock on a change day (input-space contradiction 6, contract 15) | **Wording, not a bug**: the docstring and the rules file now say elapsed hours. Changing `at()` to wall-clock would move the existing DST-day expectations for no benefit, and the scenarios keep their shape. Not pinned by a test. |
| extreme `MINSTROEM_LIVE_NOW` (input-space suspicion 7a) | **Fixed** (`UsageError`) |

Edge cases considered and deliberately skipped, with reasons:

- **Hardening `_no_network` against module- and session-scoped fixtures** (contract 6 says the
  same gap exists there). The guard is function-scoped on purpose: it must be able to let a
  `live` test through, which a session-wide block cannot, and a hook-based rewrite would touch
  every one of 2,700 tests for a hazard no offline fixture here has (none builds a transport).
  The docstring now says so. The plugin, where the live suite's own module-scoped fixture does
  make a request, is closed.
- **The 32-hex (hyphenless) form of an address id** (input-space 11, suspicion row): the spec
  types ids `format: uuid`; the package refuses any other form.
- **Address text inside the API's own `reason`**: the suite cannot know it and the documented error
  reasons are generic; only the address id is replaced.
- **A package `ValueError` or `KeyError` carrying an id**: the live suite never passes an id the
  package would refuse (ids come from the listing), and only `MinStroemError` / `RetryError` carry
  a path.
- **`--showlocals`**: not defendable (every local is printed); documented in the docstring and the
  README instead.
- **Real-API behaviour** (a forecast refresh between two requests, rounding, row order): the live
  run only. The cross-check no longer fails on a moving forecast; it says so in its report line.

---

## 6. Concept check

> Written in step 6, against section 1 — not against section 2. The question is whether
> the thing built is the thing agreed, not whether it matches the plan.

First audit, by a different model from the one that planned the work, on `9b1e56d`. Audited
against section 1 first, then the code. Run here: `ruff check .` (`All checks passed!`),
`ruff format --check .` (91 files formatted), `mypy` (`no issues found in 56 source files`),
plain `pytest` with no key (`2618 passed, 21 deselected`), and, to judge the live suite as
written, the 21 live tests against the in-repo `MinStroemService` through a scratch plugin kept
out of the repo (`liveplug/mockplug.py` in the scratchpad), under four data scenarios.

| # | Criterion | Met | Evidence |
|---|---|---|---|
| A1 | Forecast (primary goal) | yes | `prices.py:105-162` (`get_forecast_prices`), `_fetch_forecast` `prices.py:280-310`. Run against the mock: columns `total_price_per_kwh`, `variable_charges_per_kwh`, both `float64`; index `datetime64[ns, Europe/Copenhagen]` named `time`; a `null` `variableChargesPerKWh` is `NaN` with the row kept; `attrs` = `address_id`, `currency`, `vat_included`, `fetched_at` (tz-aware Copenhagen `Timestamp`). `data: []` gives an empty frame with the same columns, an empty tz-aware index and the same `attrs`. Capability false: with `address_id=None` `MinStroemError("the address has no 'forecastPrices' capability")` and no `/forecast-prices` request; with an explicit id the 422 becomes `MinStroemError` (status 422, `feature_unavailable`, request id) ending `the address lacks the 'forecastPrices' capability`. Tests: `test_minstroem_prices.py` `test_the_forecast_is_an_hourly_frame_with_gaps_and_nulls_as_nan`, `..._attrs_are_exactly_the_documented_four`, `test_an_empty_forecast_is_the_documented_empty_frame`, `test_the_forecast_capability_is_named_whichever_route_finds_it_missing`, `test_a_row_that_is_not_one_whole_hour_or_repeats_is_a_minstroem_error`. |
| A2 | Combined series | yes | `prices.py:165-260`, `_stitch` `prices.py:313-343`. Mock with confirmed 00:00-10:00 (hole at 05:00) and forecast 08:00-14:00: 08-10 confirmed and `is_forecast=False` with the confirmed values (the forecast's 9.0 not used), 11-14 forecast `True`, the 05:00 hole `NaN` with `<NA>` (never filled from the forecast); `/prices` was asked only for `[00:00, 14:00)`. With `end` the frame is exactly `[start, end)` (18 rows, hours 15 to 17 `<NA>`); without `end` it stops at the last forecast hour; `start` beyond the forecast gives the documented empty frame. Tests: `test_confirmed_then_forecast_with_the_flag_and_confirmed_winning_the_overlap`, `test_a_confirmed_gap_before_the_last_confirmed_hour_is_not_filled_from_the_forecast`, `test_without_an_end_it_runs_to_the_last_forecast_hour`, `test_with_an_end_the_frame_is_exactly_the_period_and_the_end_hour_is_excluded`, `test_a_dst_day_is_25_or_23_confirmed_hours`, `test_a_confirmed_hour_never_follows_a_forecast_hour`. |
| A3 | Addresses | yes | `_series._address_frame` (`_series.py:259-292`): `id`, `label`, `formatted_address`, `region`, `time_zone`, `grid_operator_name`, `supplier_name`, then six `bool` capability columns; empty frame with the same columns for `{"data": []}`. `test_minstroem_addresses.py` (49 tests, among them `test_null_fields_stay_none_not_nan`, `test_a_malformed_address_is_a_minstroem_error_without_status`). |
| A4 | Address default | yes | `_resolve_address` (`_series.py:321-353`): `ValueError("no addresses on this account")`; several: `ValueError` listing `id (label)` for each; one lookup then the series request. `test_the_only_address_is_looked_up_once_and_used`, `test_several_addresses_are_a_value_error_listing_each_id_and_label`, `test_the_address_is_looked_up_once_for_both_requests` (combined). |
| A5 | Series | yes | `_fetch_series` (`_series.py:514-543`) over `usage.py`, `costs.py`, `prices.py`: one row per interval of `[start, end)` from `_interval_index` (calendar stepping for day/month/year, never a fixed 24 h), absent rows `NaN`/`<NA>`, misaligned bound a `ValueError` naming it before any request (`_resolve_series_period`, `_series.py:136-160`), `currency` and `vat_included` in `attrs` for costs, revenue and prices. `test_a_zero_reading_stays_zero_and_a_missing_row_is_nan`, `test_a_dst_day_has_23_or_25_hourly_rows_and_the_right_utc_window`, `test_quarter_hours_follow_the_danish_calendar`, `test_prices_with_no_rows_are_all_nan_never_zero`. The spec contract test `test_minstroem_spec.py` ties every field, enum, path and query parameter to `tests/fixtures/minstroem_openapi.json`. |
| A6 | Long periods | yes | `_windows` (`_series.py:192-207`) on local calendar boundaries with the `_WINDOWS` table; `test_a_period_over_the_cap_is_split_aligned_and_equals_one_unbounded_call` (13 table rows), `test_windows_end_on_calendar_boundaries_and_abut`, `test_every_window_touches_no_more_local_days_than_the_cap`, `test_usage_windows_never_touch_more_than_five_calendar_years`, `test_window_boundaries_are_local_midnights_not_elapsed_time`. The mock's caps are written independently of `_series.py` (`conftest.py:272-278`, `403-409`). |
| A7 | Errors and key | yes | `client.py`: `_api_error` (`client.py:211-242`) fills `status`, `error_code`, `reason`, `request_id`; the key lives only in the `Authorization` header and `_key_state`; `__repr__` is `MinStroemClient(key=set\|missing)`; `MissingApiKeyError` names `MINSTROEM_API_KEY` and is raised by the first request (`_check_key`, `client.py:198-208`), construction never fails. `test_wrong_key_is_a_401_that_never_echoes_either_key`, `test_unusable_key_is_refused_at_the_first_request_and_never_echoed`, `test_missing_key_constructs_and_fails_only_at_the_first_request`, `test_the_key_is_not_in_the_repr_of_a_working_client`, `test_key_with_a_trailing_newline_is_stripped_and_works`. The section 3 deviation (`status` is `None` for payload checks that are not an API error) does not affect A7, which promises the four attributes for an API error. |
| A8 | Tests | **partially** | Met here: plain `pytest` is green with no key (`2618 passed, 21 deselected`) and touches no network (the `_no_network` guard bites for unmarked tests, exempts only `live`); with `MINSTROEM_API_KEY` set to a dummy, plain `pytest tests/test_minstroem_live.py` still gives `21 deselected` (the `addopts` expression); `-m live` collects all 21 and, with no key, gives `21 skipped` with the reason `set MINSTROEM_API_KEY to run the live Min Strøm tests`. The README carries the exact PowerShell line (`README.md:160`, asserted by `test_minstroem_spec.py`). Against the mock the suite covers all seven endpoints: addresses, forecast (3 tests), combined series (open end, closed end, a cross-check against its two endpoints), prices hour and quarter-hour, a probe of a future `to`, consumption at all four aggregations, solar export, costs, revenue, a 400-day two-window request, the listed address id, a wrong key, an unknown address, the address default; the `MIN STRØM LIVE SUMMARY` block prints (21 lines) above one `PASSED` line per test under `-rA`. The real run is on the user's machine after step 7 and is not held against this criterion. **Unmet, code wrong:** `test_live_prices_with_forecast_open_end` crashes with `TypeError: boolean value of NA is ambiguous` (`tests/test_minstroem_live.py:171`, `flags.count(False)`) whenever the combined series holds an hour with neither source, which A2 promises as `<NA>` and the API's sparse rows make ordinary: reproduced twice against the mock, once with one confirmed hour missing at 05:00 and once with a forecast that starts after a gap (`1 failed, 20 passed`; the other 20 pass). On the user's run the combined series, the second thing A8 names, would give a traceback in place of its summary line. The fix is small and belongs to the live suite: count with the Series (`flags.eq(False).sum()`, `flags.eq(True).sum()`, `flags.isna().sum()`), not `list.count`. See the drift list. |
| A9 | Docs | yes | README section "Min Strøm" (`README.md:111-165`): key setup (Plus, iOS app, `MINSTROEM_API_KEY`/`api_key=`), forecast example, a table of all eight functions with unit, interval, columns and endpoint, missing-is-NaN and `attrs`, the live command. `STRUCTURE.md` names every module and test file (`stop_gate.structure_problems` reports `[]`); `.claude/rules/structure-minstroem.md` has the full signature tables. `test_minstroem_spec.py` (148 passed with the gate tests after my doc edits). The structure-auditor's four gaps are applied below. |

Drift found, and what was done about it:

| Finding | Kind | Resolution |
|---|---|---|
| `test_live_prices_with_forecast_open_end` counts flags with `list.count`, which raises on `<NA>` (A8). | code (test) wrong against A8 | **Sent back to step 3.** The step 3/5 author should count with the Series, and add an offline guard so the live suite's own logic is exercised on a hole and on a gappy forecast (the scratch plugin used here is a model for it: confirmed prices from the mock minus one hour; a forecast that starts after the confirmed end). Nothing in section 1 changes. |
| Out of scope (Enterprise API, writes, caching or vintage history, restructuring `tests/`) | none | Nothing of it built: no `thirdParty`/HMAC code, only `GET`, `fetched_at` stamped but nothing stored, `tests/` still flat with the `test_minstroem_*` prefix. |
| Connections and surface | none | The branch diff against `main` touches no `utils` or `energidataservice` source; only `conftest.py`, `pyproject.toml`, the three rules files, `STRUCTURE.md` and `README.md` beside the new files. Public surface is the fifteen names of the concept; nothing the concept did not ask for. |
| Showcase | none | All six `python -m energydata.minstroem.<module>` exit 0 without a key and print the one-line notice; `_series` prints its two windows and the refused month start. The with-key branches were exercised at step 4. |
| Live-suite observations, not blocking | for the re-run, optional | (1) `test_live_prices_with_forecast_open_end` and `..._closed_end` call `get_prices_with_forecast` with an explicit id but skip only on `forecast_prices`, not on `prices`, so an address with a forecast and no `prices` would fail instead of skip; the cross-check test does skip on both. (2) `forecast["total_price_per_kwh"].dropna().map(pd.notna).all()` is always true. (3) A failing live request puts the address id in the pasted traceback through `MinStroemError`'s message (`on /me/addresses/<id>/prices`); the report lines are clean. |
| Structure-auditor item 1: `MarketRecords` undocumented in `structure-energidataservice.md` | doc gap, predates the round | **Applied.** `type MarketRecords` exists at `tests/conftest.py:49` and is imported by `test_pricelist.py:12` and `test_dsos.py:6`. |
| Structure-auditor item 2: the three `rules/structure-*.md` files missing from the `.claude/` table in `STRUCTURE.md` | doc gap, this round made it a third | **Applied** as one row. |
| Structure-auditor item 3: `tests/` tree line said "one test_<module>.py per module" | stale purpose | **Applied**; `stop_gate.structure_problems` still `[]` with the `<`/`*` line. |
| Structure-auditor item 4: `tests/fixtures/**` in the energidataservice rules `paths` also matched the Min Strøm spec | stale entry | **Applied**: `tests/fixtures/energidataservice_*.json`. |

### Earlier rounds still hold

First round: there are no earlier rounds to re-check.

### Second audit (re-run, after steps 3, 4 and 5 re-ran for the live suite)

Section 1 read first, then the code and the tests, on `5c2b078`. `git diff 9b1e56d..HEAD -- src
pyproject.toml` is empty: the package, and so the evidence for A1 to A7 in the first audit, is
byte-identical to what was audited then; everything since is the live suite, test support, the
offline plugin and docs. `git diff main...HEAD -- src/energydata/utils src/energydata/energidataservice`
is empty too. Run here, no key in the environment:

| Gate | Result |
|---|---|
| `ruff check .` / `ruff format --check .` | `All checks passed!` / `92 files already formatted` (also after my doc edits) |
| `mypy` | `Success: no issues found in 57 source files` |
| `pytest` (plain, no key) | `2726 passed, 21 deselected, 1 warning in 114.90s` (the warning is the `utils/frames.py` pandas deprecation noted at step 4); the six package suites alone `781 passed`, `test_minstroem_spec.py` `210 passed` |
| `stop_gate.structure_problems` / `stray_test_files` / `missing_init_files` | `[]` / `[]` / `[]` |

| # | Criterion | Met | Evidence |
|---|---|---|---|
| A1 | Forecast (primary goal) | yes | Unchanged package (empty diff since the first audit, whose runs against the mock stand): `get_forecast_prices` `prices.py:105-162`, `_fetch_forecast` `prices.py:280-310`. The tests of the first audit are green again in the 2726 (`test_the_forecast_is_an_hourly_frame_with_gaps_and_nulls_as_nan`, `..._attrs_are_exactly_the_documented_four`, `test_an_empty_forecast_is_the_documented_empty_frame`, `test_the_forecast_capability_is_named_whichever_route_finds_it_missing`). Live: `test_live_forecast_prices` and `test_live_forecast_hours_are_whole_local_hours_and_unique` pass through the plugin, and report `forecast: 48 hours ..., region DK1, DKK, first 0.680, last 0.670`, or `forecast: EMPTY` under `empty_forecast`. |
| A2 | Combined series | yes | Unchanged package, same tests green (`test_confirmed_then_forecast_with_the_flag_and_confirmed_winning_the_overlap`, `..._a_confirmed_gap_before_the_last_confirmed_hour_is_not_filled_...`, `test_a_confirmed_hour_never_follows_a_forecast_hour`). The live tests now meet the `<NA>` hours A2 promises: `neither 1` under `hole`, `neither 21` under `gappy_forecast`, `neither 12` under `empty_forecast`, all `21 passed`; the open end, the closed end and the cross-check against `get_prices` and `get_forecast_prices` each report (`combined cross-check: 61 confirmed hours match get_prices, 12 forecast hours match get_forecast_prices`). |
| A3 | Addresses | yes | Unchanged `_address_frame`; `test_minstroem_addresses.py` (49) green; live `test_live_addresses_shape` passes, also with no address (`no_addresses`). |
| A4 | Address default | yes | Unchanged `_resolve_address`; the series tests green; live `test_live_address_default` covers one address, one without forecast, several (`two_addresses`, `ValueError as designed`) and none (`no_addresses`). |
| A5 | Series | yes | Unchanged `_fetch_series` and period resolution; `test_minstroem_series.py`, `_usage.py`, `_costs.py`, `_prices.py` green; the live tests cover every aggregation and both resolutions. |
| A6 | Long periods | yes | Unchanged `_windows`; the window tests green; live `test_live_hourly_consumption_over_two_windows` (`9601 rows, 0 missing` against the mock). |
| A7 | Errors and key | yes | Unchanged `client.py`; the client tests green. The live wrong-key test now asserts a computed boolean (a failed assertion would not print the key through pytest's own introspection); in the run with `prices_not_found`, `grep -c` finds no line of the whole output (summary, failures, short summary) holding the dummy key or the address id. |
| A8 | Tests | **yes** (was partially) | See "A8 in detail" below. |
| A9 | Docs | yes | README "Min Strøm" section (`README.md:111-167`) with the key setup, the forecast example, the eight-function table, the missing-is-NaN and `attrs` notes, the live line and, new since the first audit, the `--showlocals` warning and the address-id note; `STRUCTURE.md` and `.claude/rules/structure-minstroem.md` describe the plugin, its nine scenarios and its clock pin; the five `structure-auditor` edits are applied (below); the 18 docs, README and structure tests of `test_minstroem_spec.py` (`-k "docs or readme or structure or rules"`) green. |

#### A8 in detail

The first audit's unmet part was `test_live_prices_with_forecast_open_end` crashing with
`TypeError: boolean value of NA is ambiguous` on an hour with neither source. I checked that the
fix removes the class, not the instance, and then asked the three questions the brief set.

**The defect class is gone.** The live file has no `list.count`, `== True/False`, `bool()`,
`tolist()` loop or `if flags` over a `boolean`-dtype value (a sweep of `.count(`, `== True|False`,
`is True|False`, `tolist(`, `bool(`, `.all()`, `.any()`: every remaining hit is a Python-bool
`attrs` value or a plain-bool NumPy result). The counts use `flags.eq(False).sum()`,
`flags.eq(True).sum()` and `flags.isna().sum()` (`tests/test_minstroem_live.py:293-297`) and the
ordering check goes through `_hours` (`:213-216`), which treats `<NA>` as neither. The first
audit's three live-suite observations are resolved: both combined tests also call
`_need(address, "prices")` (`:277`, `:312`); `.map(pd.notna).all()` is `.map(math.isfinite).all()`
(`:261`); a failed request no longer carries the address id (see below).

**Will it pass or skip cleanly on real data?** I could only run it through the in-repo plugin
(`-p minstroem_live_mock`, a dummy key, the README line's flags, the mock refusing every real
transport) and, for shapes the plugin has no scenario for, through three scratch plugins in the
scratchpad (not committed). Results, real clock:

| Shape of data | Result |
|---|---|
| `plain` | `21 passed in 0.94s`, `neither 0` |
| `hole` (two confirmed hours missing, one null total) | `21 passed`, `neither 1` |
| `gappy_forecast` (forecast starts after a gap, with holes and nulls) | `21 passed`, `neither 21`, `forecast ... first nan` formatted without error |
| `empty_forecast` | `21 passed`, `forecast: EMPTY`, `0 forecast hours match` |
| `two_addresses` | `21 passed`, default address test `ValueError as designed` |
| `no_prices` / `no_forecast` | `14 passed, 7 skipped` / `16 passed, 5 skipped`, the reason names the capability |
| `no_addresses` | `4 passed, 17 skipped`, the reason `the account has no addresses` |
| `prices_not_found` | the six price tests fail as designed with status, code, reason and request id and the address as `<address>` |
| scratch: no confirmed price rows and nothing generated at all | `21 passed`; `49 NaN, mean nan` and `49 missing, sum 0.0 kWh` print without error |
| scratch: `/me/addresses` answers 403 `plus_required` | see observation 1 |

The suite makes 31 requests (counted on the mock: 5 address lookups, 9 forecast, 8 prices, 6
consumption, 1 each of costs, revenue and solar export), under the 60 a minute, and the client
retries a 429 after its `Retry-After` regardless. The wiring checks all hold:
plain `pytest tests/test_minstroem_live.py` with a key set gives `21 deselected`; `-m live` with
no key `21 skipped` with the reason; with a blank key `21 skipped`; `-m live --collect-only` with
a key `21 tests collected`. None of these can send a request (they collect, deselect or skip).
The spec suite's subprocess runs were read for the same property: every run that sets a
non-blank key either loads the plugin, only collects, or runs the deselected expression.

**Is the output readable and anonymous?** A passing run prints the `MIN STRØM LIVE SUMMARY` block,
21 lines, one per test, then `short test summary info` with one `PASSED` or `SKIPPED` line per
test (and the full skip reason once per reason):

```
addresses: 1, regions ['DK1'], forecast-capable 1
forecast: 48 hours 2026-10-26T19:00:00+01:00 .. 2026-10-28T18:00:00+01:00, region DK1, DKK, first 0.680, last 0.670
combined (today..): 67 hours 2026-10-26T00:00:00+01:00 .. 2026-10-28T18:00:00+01:00, confirmed 36, forecast 31, neither 0
combined cross-check: 61 confirmed hours match get_prices, 12 forecast hours match get_forecast_prices
PROBE /prices with to=+3 days: accepted, 36 priced rows
wrong key: HTTP 401 invalid_token
unknown address: HTTP 404 resource_not_found
```

It holds the region, counts, time spans, a few rounded values, never the key, an address id or
the address text. The only non-ASCII character is `ø` (and `Ø`), which every Windows code page
the user can have encodes. A failing request reads
`AssertionError: a request failed: MinStroemError: Min Strøm API: HTTP 404 resource_not_found on
/me/addresses/<address>/prices: ... (request id ...) [status=404 error_code=... reason=... request_id=...]`
and nothing else of the package's frames, which is what support needs.

**Is the README's PowerShell line exactly what the user should run?** Yes.

```powershell
$env:MINSTROEM_API_KEY='<din-nøgle>'; .venv\Scripts\python.exe -m pytest tests/test_minstroem_live.py -m live -rA --tb=short -p no:cacheprovider; Remove-Item Env:MINSTROEM_API_KEY
```

It is the line of guide step 13, one line, key inline, run from the repo root, and `README.md:160`
equals it (`test_minstroem_spec.py` pins it as `LIVE_COMMAND`). `.venv\Scripts\python.exe` is the
interpreter the README's own setup creates (`python -m venv .venv`, `pip install -e ".[dev]"`);
`-m live` overrides `addopts`' `-m 'not live'` (the last `-m` wins, shown above); `--tb=short` is
the style the anonymity guarantee was verified under together with `auto`, `long` and `native`;
`-rA` gives the per-test lines; `Remove-Item` runs even when pytest fails because the separator is
`;`. The one thing that would defeat the anonymity, `-l`, is warned about in the README and the
module docstring.

**Observations, not blocking and not an unmet criterion** (A8 does not promise a tidy report for
every failure, and nothing here is the crash the first audit found):

1. *A failure of `/me/addresses` is reported once per dependent test.* The `live_client` and
   `address` fixtures are module-scoped and not wrapped by `_anonymous_failures`, so with a key that
   is wrong, expired, without Plus or rate-limited, the 17 tests that need the address each end as
   an `ERROR at setup` (`3 failed, 1 passed, 17 errors`; the 3 failures are the tests that call the endpoint themselves: two report the failed request anonymously through the decorator, the wrong-key test fails because the scratch mock answers 403 where it wants 401) with the same chained traceback of about 37 lines (scratch run, 403
   `plus_required`: facts present at the foot, `status=403 plus_required`, request id; no key, no
   id, because `/me/addresses` has no id in its path). The first lines tell the user what is wrong;
   the paste is just long. A candidate for a `DEVELOPMENT.md` note at step 8 (wrap the fixture's
   `get_addresses` in the same anonymiser, or skip-with-reason once).
2. *One assertion can fail on real data without a package defect.* `test_live_forecast_prices`
   requires the first forecast hour to be no earlier than the current hour minus one (guide step
   12). If the real forecast starts in the past, the test fails and says so; that is a finding the
   live run exists to produce.
3. Not-unmet but worth knowing: the spec suite grew to 210 tests (about 83 s) because each scenario
   is a pytest subprocess of 21 tests; the whole plain `pytest` is now about 2 minutes.

#### Drift found, and what was done about it

| Finding | Kind | Resolution |
|---|---|---|
| A8's `<NA>` crash from the first audit | code (test) wrong | Fixed by the step 3 re-run, red first (section 3), guarded by `test_every_live_test_passes_against_the_mock_in_every_shape_of_data` for `hole`, `gappy_forecast` and `empty_forecast`; I re-ran it and found the class gone. No second send-back. |
| Out of scope (Enterprise API, writes, caching or vintage history, restructuring `tests/`) | none | Nothing built: `src/` is unchanged; `tests/` is still flat with the `test_minstroem_*` prefix; the plugin is a support module, not a test folder. |
| Connections and surface | none | Package surface unchanged: seventeen names, no `main` exported; no `utils` or `energidataservice` source touched on the branch. `tests/minstroem_live_mock.py` is test support (`SCENARIOS`, `build_service`, two hooks) outside the package and outside what the concept promises users. |
| Showcase | none | All six `python -m energydata.minstroem.<module>` exit 0 without a key; `_series` prints its two windows and the refused month start; with-key branches exercised at step 4 (re-run). |
| Structure-auditor edit 1 (rules file: "just before one" not a committed test; add the `gappy_forecast` times of day) | stale purpose | **Applied.** `PINNED_INSTANTS` (`test_minstroem_spec.py:1170`) holds both change days, the day after each, the repeated hour (first, second, on the hour) and `hole` at `2026-10-27T00:00:00+01:00`; no 23:59:59 run is committed. |
| Structure-auditor edit 2 (rules file: an unknown or absent `MINSTROEM_ADDRESS_ID`) | stale purpose | **Applied.** The fixture skips with `MINSTROEM_ADDRESS_ID is not an address of this account` / `the account has no addresses`; the spec test asserts the first (`:1761`). |
| Structure-auditor edit 3 (rules file named private `_Address`, `_Private`) | private name leaked | **Applied**, reworded to "a `dict` subclass" and "`str` subclasses"; `_Address` also collides with the unrelated dataclass in `_series.py`. |
| Structure-auditor edit 4 (rules file: the mock's UUID `X-Request-ID`s) | stale purpose | **Applied**; `test_the_mocks_request_ids_are_uuids_like_the_specs_header` (`:2084`). |
| Structure-auditor edit 5 (`STRUCTURE.md` README line "human setup guide", older than the branch) | stale purpose | **Applied**: the README now documents all three sources and the live command. |

`stop_gate.structure_problems` is `[]` after the edits; `ruff check .`, `ruff format --check .` and
the spec suite's 18 docs tests re-run clean.

---

## 7. Ship log

| Field | Value |
|---|---|
| Commits | 29 before this one (listed below, oldest first), plus `Ship: Min Strøm Consumer API, with price forecasts first`, the last commit of the round |
| Pushed to | `origin/feat/minstroem-consumer-api` (`https://github.com/petermads123/energydata`, a **public** repository). Every earlier step had already pushed; at the start of this step `HEAD` and `origin/feat/minstroem-consumer-api` were both `258b545`. |

### Gates on the whole tree

Run at `258b545`, clean working tree, no `MINSTROEM_*` variable set, no Min Strøm key in the
environment.

| Gate | Result |
|---|---|
| `ruff check .` | `All checks passed!` |
| `ruff format --check .` | `92 files already formatted` |
| `mypy` | `Success: no issues found in 57 source files` |
| `pytest` | `2726 passed, 21 deselected, 1 warning in 120.74s` (the 21 are the live tests, deselected by `addopts`; the warning is the pandas deprecation in `utils/frames.py` noted at step 4, not in this round) |

Plan file agrees: steps 1 to 6 `done`; section 6's second audit has A1 to A9 all met, none unmet
(the first audit's A8 was sent back, fixed and re-audited); section 5 has a test against every
intent T1 to T13.

### Review of what the round committed

`git diff main...HEAD --stat`: 25 files, 13,960 insertions, 14 deletions. The repository is
public, so the diff was read for anything a stranger should not see.

| Question | Finding |
|---|---|
| Stray files (scratch scripts, sample data, temp material) | None. All 25 files are planned: the seven `src/energydata/minstroem/` modules, `tests/conftest.py`, the spec fixture, the eight `tests/test_minstroem_*.py` and `tests/minstroem_live_mock.py`, `pyproject.toml`, the README, `STRUCTURE.md`, three rules files and this plan. `git status --ignored` shows nothing outside the venv and caches. No file was ever added and later removed on the branch (every file any commit touched is in the final diff; no deletion or rename in history). |
| `tests/minstroem_live_mock.py` | Committed on purpose, not scratch: section 3 records it, `STRUCTURE.md` and `structure-minstroem.md` name it, `test_minstroem_spec.py` imports and drives it. It is a pytest plugin that loads **only** through an explicit `-p minstroem_live_mock`; `addopts`, `conftest.py` (no `pytest_plugins`) and the README live line do not load it, so the user's real live run cannot be redirected to the mock. It reads the environment and never writes a file or opens a socket. The concept check's scratch plugins stayed in the scratchpad and are not in the repo or its history. |
| Keys | None. The only key values in the diff are `test-key`, `wrong-from-env`, `dummy-key-for-the-offline-live-run` and similar test strings, and the README placeholder `<din-nøgle>`. No `Bearer` token other than `Bearer test-key`; no token shape (`sk-`, `ghp_`, `github_pat_`, `AKIA`, JWT) anywhere. |
| Address ids and address text | No real id. The only ids are the spec's own published example id `2b4bf7a8-b5d0-45ab-8d42-3a5688bba167` (14 times in the spec fixture, as the `OTHER` constant in three test files and once in the spec test), three patterned fixtures (`5b0ac4d2-3c5e-…-0a6f5a1b2c3d`, `0e8d1c3a-7b49-…`, `9f1c2d3e-4b5a-…-0a1b2c3d4e5f`) and four `00000000-0000-4000-8000-00000000000N` request ids. Address text is `Testvej 1, 1000 Testby` and the label `Home`/`Flat`. |
| Personal data and local paths | None: no e-mail address, user name, `/home/user` or `C:\Users` path in any added line. All 29 commits are authored `Claude <noreply@anthropic.com>`. The hostnames are the documented ones (`api.minstroem.app`, `docs.minstroem.app`, `example.invalid`, the sources the README already named). |
| The fake-key request of step 4 | The scratch script is **not** committed, in the tree or in any commit (`git log --all -S` finds the string in one commit only, `0821b4f`, and only in this plan file). What is committed is one prose sentence in section 4 saying that a first attempt of the showcase script sent one request with the fake key `fake-showcase-key` to the real API and got a 401. The string is invented, not a credential; it is left as the honest record and is already on the remote. |
| A step that committed nothing | None. Concept `c355308`, Plan `0d1f48b`, Implement `d52a11a`, Verify `62541b7`, Test `9b1e56d`, Concept check `b6b3b04`, and for the re-run of 3 to 6 `e147468`, `0821b4f`, `5c2b078`, `258b545`: each touches this file. (The step 3 commits carry the subjects `Add Min Strøm Consumer API subpackage with price forecasts` and `Run the live Min Strøm suite offline, and fix its NA crash` rather than `Implement:`; the record is there.) |
| Base | `main` is `d197471` locally and on `origin`; the branch is 29 commits ahead and 0 behind. |

Nothing was removed or changed by this review.

### What the user runs

The live suite has never run against the real API; it ran only against the in-repo mock. With a
Min Strøm Plus key, from the repo root, in PowerShell (the line `README.md` carries and
`test_minstroem_spec.py` pins):

```powershell
$env:MINSTROEM_API_KEY='<din-nøgle>'; .venv\Scripts\python.exe -m pytest tests/test_minstroem_live.py -m live -rA --tb=short -p no:cacheprovider; Remove-Item Env:MINSTROEM_API_KEY
```

It sends about 31 requests (limit 60 a minute), prints a `MIN STRØM LIVE SUMMARY` block and one
outcome line per test, and does not print the key, the address id or the address text. Do not
add `-l` / `--showlocals`. Paste the output back.

### Commits of the round (oldest first)

- `c355308` Concept: Min Strøm Consumer API, with price forecasts first
- `69abfc7` Plan: Min Strøm Consumer API, with price forecasts first (draft, critique pending)
- `0d1f48b` Plan: Min Strøm Consumer API, with price forecasts first
- `55db4e8` WIP step 3: minstroem client, series path and endpoint modules
- `01e0e58` WIP step 3: live marker, Min Strøm mock service and live suite
- `d52a11a` Add Min Strøm Consumer API subpackage with price forecasts
- `59d1d81` WIP step 4: sync structure docs with the code
- `62541b7` Verify: Min Strøm Consumer API, with price forecasts first
- `6282e50` WIP step 5: harden payload checks, prices pre-check, row-based confirmed hours
- `33aee35` WIP step 5: client suite (error mapping, key handling, 2xx bodies, showcase)
- `68751c2` WIP step 5: series suite (shared path, windows, address resolution, lifecycle, mock service)
- `faa31e2` WIP step 5: addresses suite
- `8f0bc6e` WIP step 5: usage suite
- `2b09be9` WIP step 5: costs and revenue suite
- `75070af` WIP step 5: prices and spec suites; fix hourly row helper across DST
- `5bc5e8d` WIP step 5: fix a typo in the spec suite's collect check
- `5c38af8` WIP step 5: live test that the listed address id is accepted as listed
- `9b1e56d` Test: Min Strøm Consumer API, with price forecasts first
- `61931f9` WIP step 6: apply the structure-auditor's four doc fixes
- `b6b3b04` Concept check: Min Strøm Consumer API, with price forecasts first
- `6bcfa43` WIP step 3: live suite counts <NA> with the Series, offline guard for it
- `e147468` Run the live Min Strøm suite offline, and fix its NA crash
- `54962cd` WIP step 4: pin the clock in the offline live run
- `0821b4f` Verify: Min Strøm Consumer API, with price forecasts first
- `0f18417` WIP step 5: harden the live suite and its offline plugin
- `d93e369` WIP step 5: docs for the live-suite changes, trim the guard's slowest rows
- `5c2b078` Test: Min Strøm Consumer API, with price forecasts first (re-run for the live suite)
- `271ab55` WIP step 6: apply the structure-auditor's five doc fixes (re-run)
- `258b545` Concept check: Min Strøm Consumer API, with price forecasts first
- `Ship: Min Strøm Consumer API, with price forecasts first` — this commit: section 7, step 7 `done`, marker moved to step 8

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
