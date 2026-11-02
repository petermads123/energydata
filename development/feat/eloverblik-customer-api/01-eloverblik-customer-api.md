# Eloverblik Customer API: meter data and charges

<!-- claude-plan step=5 status=active -->

| Field | Value |
|---|---|
| Feature | `feat/eloverblik-customer-api` |
| Round | `1` |
| Branch | `feat/eloverblik-customer-api` |
| Version | `major` — a new source subpackage; step 9 adds `<old> → <new>` (would be 1.0.0 → 2.0.0 from main on 2026-11-02) |
| Started | `2026-11-02` |

## Progress

| # | Step | Skill | Runs | Status |
|---|---|---|---|---|
| 1 | Conceptualize | `/conceptualize` | with the user | done |
| 2 | Plan | `/plan` | with the user, unless the critique is clean | done |
| 3 | Implement | `/implement` | in `/build` | done |
| 4 | Verify | `/verify` | in `/build` | done |
| 5 | Test | `/test` | in `/build` | halted |
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

A new source subpackage for Energinet's **Eloverblik Customer API**
(`https://api.eloverblik.dk/customerapi/api`, OpenAPI 3.0.4 spec "Eloverblik.Api.CustomerApi"
v1.0 at `https://api.eloverblik.dk/customerapi/swagger/customerapi-v1.0/swagger.json`). It
follows the Energi Data Service and Min Strøm pattern: one client on top of
`utils.ApiClient`, and one plain synchronous function per endpoint returning a
`pandas.DataFrame`. The primary goal is the user's **meter data** — measured consumption (and
production where there is solar) per metering point, quarter-hourly up to yearly, for the
previous five years plus the current year. The metering points, their master data and the
charges linked to them come along.

The Customer API, as published on 2026-11-02:

| Endpoint | Data | This round |
|---|---|---|
| `GET /token` | Refresh token (header) → 24-hour access token, `{"result": "<jwt>"}`; 2 calls/minute/IP | used by the client |
| `GET /isalive` | Service health; 503 under load | used by the client at most, no public function |
| `GET /meteringpoints/meteringpoints?includeAll=` | `result[]` of `MeteringPointApiDto`: `meteringPointId`, `typeOfMP`, `balanceSupplierName`, address fields, `hasRelation`, `consumerCVR`, `dataAccessCVR`, `childMeteringPoints[]`, `settlementMethod`, `meterReadingOccurrence`, consumer names, `meterNumber`, `consumerStartDate`, `isMovedOut`. `includeAll=true` adds unlinked points registered to the user's CPR/CVR | yes |
| `POST /meteringpoints/meteringpoint/getdetails` | per point `{success, errorCode, errorText, id, result: MeteringPointDetailsCustomerDto}` — about 60 scalar master-data fields plus nested `contactAddresses[]` and `childMeteringPoints[]` | yes |
| `POST /meterdata/gettimeseries/{dateFrom}/{dateTo}/{aggregation}` | per point `{success, errorCode, errorText, id, MyEnergyData_MarketDocument}` → `TimeSeries[]` (`mRID` = point id, `measurement_Unit.name`, `businessType`) → `Period[]` (`resolution`, `timeInterval.start/end`) → `Point[]` (`position`, `out_Quantity.quantity` as string, `out_Quantity.quality`). Dates `YYYY-MM-DD`; aggregation `Actual`, `Quarter`, `Hour`, `Day`, `Month`, `Year`; at most 730 days per request | yes — the core |
| `POST /meteringpoints/meteringpoint/getcharges` | per point `{success, errorCode, …, result: {meteringPointId, subscriptions[], tariffs[], fees[]}}`; a charge has `priceId`, `name`, `description`, `owner`, `validFromDate`, `validToDate`, `periodType`, `price`, `quantity`; a tariff has `prices[]` of `{position, price}` instead | yes |
| `POST /meteringpoints/meteringpoint/getchargelinkswithcharges` | charge links with price series per date interval | no |
| `POST …/timeseries/export`, `…/masterdata/export`, `…/charges/export` | CSV copies of the JSON endpoints | no |
| `POST …/relation/add`, `PUT …/relation/add/{id}/{code}`, `DELETE …/relation/{id}` | link points (writes) / retired | no |

Request bodies are `{"meteringPoints": {"meteringPoint": ["<18-digit id>", …]}}`. Limits: 120
calls/minute/IP, 1,200/minute across all users, 2/minute/IP on `/token`; 429 when exceeded
and 503 when DataHub cannot keep up, with the advice to wait a minute and to bundle 10
points per request. Errors are numbered codes (`10001`, `200xx` metering point, `300xx` meter
data, `500xx` token) — request-level ones as HTTP 4xx, per-point ones inside a 200 response.
Responses carry `X-Correlation-ID`.

### Why it is worth building

The user wants their own measured consumption and production in the package's frame-shaped,
Danish-time form, next to the market prices and tariffs it already fetches, so cost and usage
can be analysed together. The metering points and details are needed to find what to ask
for; the charges say which tariffs and subscriptions actually apply to the user's point,
which `energidataservice.pricelist` cannot know.

### Inputs and outputs

- **Token:** `refresh_token=` on the client, else the `ELOVERBLIK_REFRESH_TOKEN` environment
  variable. Without one everything imports and constructs; the first request raises a clear
  error naming the variable. The client trades it for an access token on its first request
  and reuses that for its lifetime; a 401 triggers exactly one refresh and retry.
- **Metering points:** every point-scoped function takes `metering_point_ids` — `None` means
  every linked point; a string or a sequence of strings names them. Requests are batched at
  most 10 points each.
- **Periods:** `start`/`end` as elsewhere in the package — naive is Danish local time,
  half-open `[start, end)`, a lone date is the whole local day. The API takes whole Danish
  dates, so the client requests the covering dates and trims. A period not aligned to the
  aggregation (e.g. `aggregation="month"` starting mid-month, `"hour"` starting at 10:30) is a
  `ValueError`, never silently expanded.
- **Meter data output:** index is interval start, tz-aware `Europe/Copenhagen`; columns are
  two-level `(metering_point_id, field)` with fields `energy_kwh` (float) and `quality` (the
  raw code string, e.g. `A04`). Aggregations `quarter`, `hour`, `day`, `month`, `year`.
  With `end`, one row per interval of `[start, end)` on the Danish calendar; without `end`,
  through the last interval returned. A missing interval is NaN, never zero.
- **Metering points / details / charges output:** plain tables, one row per point (per
  charge and price position for charges), snake_case columns.

Decided without the user, stated here: `Actual` aggregation is left out — returned points may
each have a different native resolution, which does not fit one index; quarter and hour data
are reached through `quarter` and `hour`. A point whose data comes back at a different
resolution from the one asked, or in a unit other than kWh, raises an error naming the
point — never placed on the wrong grid. A point whose result is `30015` (no data), `30018`
(period outside the point's data period or before its registration), `30010` (period not
covered by the authorization) or `30016` (relation expired) gives NaN for that point in that
request window; any other per-point error raises. (Amended at step 2 with the user,
2026-11-02, from "only `30015`".)

### How it connects to the rest of the repo

Calls `utils`: `ApiClient` (loop, concurrency, retry — 429 and 503 retried with backoff),
`resolve_period`, the period index and frame helpers in `utils/frames.py` (including the
two-level column combining), and the chunked fetch helpers where they fit. Nothing calls
it. It changes `utils` in three backward-compatible ways — `ApiClient.request` gains optional
`headers` and `json`, `_gather_ordered` becomes public `gather_ordered` (its two importers
renamed), and `ApiClient` gains `request_response`, which returns the raw response so a
per-point error inside a 200 can carry its `X-Correlation-ID` — and otherwise touches no
existing module beyond re-exports and docs. (Amended at step 2 with the user, 2026-11-02;
the third change added at the step-5 halt, 2026-11-02.) The `DEVELOPMENT.md` note
on `date_windows` being elapsed-time applies directly: windows must be split on Copenhagen
calendar dates, not elapsed UTC spans. The flat `tests/` collision is sidestepped as before,
with a `test_eloverblik_*.py` prefix.

### Explicitly out of scope

- The **Third-Party API** — a different product and token.
- Anything that writes: linking points (`relation/add`), and the retired endpoints.
- `getchargelinkswithcharges` (charge history) and the three CSV export endpoints.
- `Actual` aggregation (see Inputs and outputs).
- Caching the access token between processes or on disk.
- Restructuring `tests/` into per-subpackage folders.

### Acceptance criteria

| # | The finished feature... |
|---|---|
| A1 | **Metering points.** `get_metering_points(include_unlinked=False)` returns one row per point with id, type, settlement method, supplier, address fields, relation flag and child point ids; no points is an empty frame with the same columns. |
| A2 | **Details.** `get_metering_point_details(metering_point_ids=None)` returns one row per point with the scalar master-data fields as snake_case columns (nested contact addresses left out); `None` means every linked point. |
| A3 | **Meter data (core).** `get_meter_data(start, end=None, aggregation="hour", metering_point_ids=None)` returns a frame indexed by interval start (tz-aware Copenhagen) with two-level columns `(metering_point_id, field)`, fields `energy_kwh` (float) and `quality` (raw code), for aggregations `quarter`, `hour`, `day`, `month`, `year`. With `end` there is one row per interval of `[start, end)` on the Danish calendar (23/25 hours on DST days) and a missing interval is NaN, never zero; without `end` it runs to the last returned interval. A `start`/`end` not aligned to the aggregation is a `ValueError`; a returned resolution or unit differing from the request raises an error naming the point. |
| A4 | **Long periods and many points.** Periods over 730 days are split into windows on Danish calendar dates and points are batched at most 10 per request; no interval is requested twice, and the result equals what one unbounded call would give. |
| A5 | **Charges.** `get_charges(metering_point_ids=None)` returns one row per charge and price position — point, kind (subscription/tariff/fee), price id, name, description, owner, validity, period type, price, quantity and the tariff's `position`. |
| A6 | **Token.** The refresh token comes from `refresh_token=` or `ELOVERBLIK_REFRESH_TOKEN`; one access token is fetched per client and reused, and a 401 triggers exactly one refresh and retry. Neither token appears in any exception message, `repr` or test output. A missing token fails only when a request is made, with a message naming the variable. |
| A7 | **Errors.** An HTTP or per-point API error raises `EloverblikError` carrying the HTTP status, Eloverblik's error code and text, the metering point where relevant, and the correlation id; 429 and 503 are retried with backoff before failing. |
| A8 | **Tests and live run.** Plain `pytest` is green offline with no token. Live tests, marked `live`, cover every public function and are skipped without a token. A temporary runner script (not committed) runs them locally with the user's token and prints a readable report — names, addresses, CPR/CVR and full metering-point ids redacted — for the user to paste back so the implementation can be finalised. |
| A9 | **Docs.** README, `STRUCTURE.md` and a new `.claude/rules/structure-eloverblik.md` describe the subpackage, its functions, units and the token setup. |

### Open questions

None. Decided with the user on 2026-11-02: meter data plus charges (not charge history or
CSV exports); several points come back as one wide frame, every linked point by default;
quality codes kept as a column; the real-token work happens at the end, as a live suite and a
temporary runner script, not as a probe before the plan.

Stated assumptions, unverifiable without a token and checked by the live run (recorded in
`DEVELOPMENT.md`): `dateTo` is exclusive and both dates are Danish calendar days; `Quarter`
on an hourly-metered point either fails or returns hourly data (either way the resolution
check in A3 catches it); quality codes are the CIM `A01`–`A05` strings.

---

## 2. Plan

### Approach

**Chosen: mirror `minstroem`, plus two small, backward-compatible changes to `utils`.** A
new `energydata.eloverblik` subpackage: `client.py` owns the refresh-token → access-token
flow and error mapping on top of `ApiClient`; a private `_common.py` holds what every
point-scoped function shares (id normalisation, the linked-point default, batching by 10,
per-point result unpacking, the client scope); one public module per endpoint family —
`meteringpoints.py`, `meterdata.py`, `charges.py`. `meterdata.py` keeps its period
alignment, Copenhagen-calendar index, date windows and CIM parsing private.

Eloverblik needs two things `utils` cannot give a subclass today: a **JSON request body**
(every data endpoint is a `POST`) and a **per-request header** (the access token is fetched
at run time and replaced on a 401, so it cannot be a constructor header). Both are already
supported by `async_request_with_retry`; `ApiClient.request` just does not pass them. The plan
adds keyword-only `headers` and `json` to `ApiClient.request`, defaulting to today's
behaviour. And concurrent batches need the lowest-index-failure gather that
`energidataservice` and `minstroem` already import privately as `_gather_ordered`;
`DEVELOPMENT.md` already asks for it to be made public once a second caller exists — this
is the third, so it becomes `gather_ordered` and the two existing imports are renamed.
**This amends one sentence of section 1** ("touches no existing module beyond re-exports
and docs"): the user decides it with the plan.

Rejected: (a) **Eloverblik reaching into `ApiClient` privates** (`_on_own_loop`, the
`httpx.AsyncClient`'s headers) to send bodies and swap the token — no change to `utils`, but
couples the subclass to internals the base can change at will. (b) **Promoting the
calendar helpers too** (alignment, calendar index, calendar windows, now duplicated between
`minstroem/_series.py` and `meterdata.py`) into `utils` and migrating `minstroem` — the
cleanest end state, but it refactors a shipped subpackage inside a feature round; noted in
`DEVELOPMENT.md` instead. (c) **One flat module** for the whole subpackage — would be ~800
lines and mix token handling with CIM parsing.

### Modules

| Path | New or changed | Purpose |
|---|---|---|
| `src/energydata/utils/api_client.py` | changed | `ApiClient.request` gains keyword-only `headers` and `json`, passed to `async_request_with_retry`; new `ApiClient.request_response` returning the raw response (added at the step-5 halt). |
| `src/energydata/utils/chunking.py` | changed | `_gather_ordered` renamed `gather_ordered` (public, same behaviour). |
| `src/energydata/utils/__init__.py` | changed | Re-export `gather_ordered`. |
| `src/energydata/energidataservice/day_ahead.py`, `src/energydata/minstroem/_series.py` | changed | Import `gather_ordered` instead of `_gather_ordered`; nothing else. |
| `src/energydata/eloverblik/__init__.py` | new | Re-exports the public names. |
| `src/energydata/eloverblik/client.py` | new | `EloverblikClient`, `EloverblikError`, `MissingTokenError`, `BASE_URL`, `REFRESH_TOKEN_ENV`, `DEFAULT_POLICY`. |
| `src/energydata/eloverblik/_common.py` | new | Private shared path of the point-scoped functions. |
| `src/energydata/eloverblik/meteringpoints.py` | new | `get_metering_points`, `get_metering_point_details`. |
| `src/energydata/eloverblik/meterdata.py` | new | `Aggregation`, `AGGREGATIONS`, `get_meter_data`. |
| `src/energydata/eloverblik/charges.py` | new | `get_charges`. |
| `tests/conftest.py` | changed | `EloverblikService` mock, its helpers and the `eloverblik_service` fixture. |
| `tests/fixtures/eloverblik_openapi.json` | new | The published spec as fetched on 2026-11-02, for the contract test. |
| `tests/test_eloverblik_client.py`, `test_eloverblik_meteringpoints.py`, `test_eloverblik_meterdata.py`, `test_eloverblik_charges.py`, `test_eloverblik_spec.py` | new | Offline suites. |
| `tests/test_eloverblik_live.py` | new | Live suite, marked `live`. |
| `tests/test_api_client.py`, `tests/test_chunking.py` | changed | Cover the two `utils` changes. |
| `pyproject.toml` | changed | The `live` marker's text names both live APIs. |
| `README.md`, `STRUCTURE.md`, `.claude/rules/structure-eloverblik.md` (new), `.claude/rules/structure-utils.md` | changed | Docs. |
| `C:\Users\peter\AppData\Local\Temp\claude\C--Users-peter-OneDrive-REPOS-energydata\a22383bd-cc17-4c58-8c90-940f3d876b29\scratchpad\eloverblik_live_run.py` | new, **never committed** | The temporary runner (A8). |

### Public API

`client: EloverblikClient | None = None` on every `get_*`: created and closed when absent, a
passed one left open. `metering_point_ids: str | Sequence[str] | None = None`: a string is one
id; ids are stripped and must be exactly 18 digits; an empty sequence, a malformed id or a
duplicate is a `ValueError` naming it, before any request; `None` lists the linked points
(`includeAll=false`) and uses all of them, in listing order — none linked is a `ValueError`
saying to link one in the Eloverblik portal.

| Signature | Module | Purpose | Covers |
|---|---|---|---|
| `async ApiClient.request(method: str, path: str, *, params: Mapping[str, str \| int \| float] \| None = None, fmt: Format \| None = None, headers: Mapping[str, str] \| None = None, json: object = None) -> Parsed` | `utils/api_client.py` | As today; `headers` merged over the client's headers for this request, `json` sent as the body. | A6, A3 |
| `async ApiClient.request_response(method: str, path: str, *, params: Mapping[str, str \| int \| float] \| None = None, headers: Mapping[str, str] \| None = None, json: object = None) -> httpx.Response` | `utils/api_client.py` | Added at the step-5 halt. Sends exactly as `request` does (semaphore, retry, the same exceptions except `ParseError`) and returns the response unparsed; `request` becomes `read_response(await self.request_response(...), fmt)`. `EloverblikClient` uses it so `fetch_json` can hand the response's `X-Correlation-ID` to `_post_points`/`_unpack`, and every per-point `EloverblikError` carries it. | A7 |
| `async gather_ordered[T](calls: Sequence[Callable[[], Awaitable[T]]]) -> list[T]` | `utils/chunking.py` | The renamed `_gather_ordered`, unchanged. | A4 |
| `BASE_URL: str` | `client.py` | `"https://api.eloverblik.dk/customerapi/api"`. | A6 |
| `REFRESH_TOKEN_ENV: str` | `client.py` | `"ELOVERBLIK_REFRESH_TOKEN"`. | A6 |
| `DEFAULT_POLICY: RetryPolicy` | `client.py` | `RetryPolicy(max_attempts=4, base_delay=15.0, max_delay=60.0)` — 429/503 waits grow towards the minute Eloverblik asks for. | A7 |
| `EloverblikError(message: str, *, status: int \| None = None, error_code: int \| None = None, error_text: str \| None = None, metering_point_id: str \| None = None, correlation_id: str \| None = None)` | `client.py` | `Exception`; the keywords as attributes; `str()` is the message only. | A7 |
| `MissingTokenError(EloverblikError)` | `client.py` | No refresh token at the first request; message names `REFRESH_TOKEN_ENV`. | A6 |
| `EloverblikClient(refresh_token: str \| None = None, *, policy: RetryPolicy \| None = None, timeout: float = DEFAULT_TIMEOUT, max_concurrency: int = 2, transport: httpx.AsyncBaseTransport \| None = None)` | `client.py` | `ApiClient` on `BASE_URL` with `Accept: application/json`; `policy=None` means `DEFAULT_POLICY`. Token stripped; blank is missing. `repr` is `EloverblikClient(token=set \| missing)`. | A6 |
| `EloverblikClient.has_refresh_token: bool` (property) | `client.py` | Whether a usable refresh token is held. | A6 |
| `async EloverblikClient.fetch_json(method: Literal["GET", "POST"], path: str, *, params: Mapping[str, str] \| None = None, body: object = None) -> dict[str, JsonValue]` | `client.py` | One authorised request returning the JSON object. Fetches the access token on first use (under a lock, so concurrent calls share one `/token` call), reuses it, and on a 401 refreshes once and retries once. Only awaitable on the client's loop. | A6, A7 |
| `EloverblikClient.get_json(method: Literal["GET", "POST"], path: str, *, params: Mapping[str, str] \| None = None, body: object = None) -> dict[str, JsonValue]` | `client.py` | The sync form, through `run`. | A6 |
| `get_metering_points(*, include_unlinked: bool = False, client: EloverblikClient \| None = None) -> pd.DataFrame` | `meteringpoints.py` | `RangeIndex`; columns below. | A1 |
| `get_metering_point_details(metering_point_ids: str \| Sequence[str] \| None = None, *, client: EloverblikClient \| None = None) -> pd.DataFrame` | `meteringpoints.py` | `RangeIndex`, one row per point in request order; every scalar field of `MeteringPointDetailsCustomerDto` in spec order as a snake_case column. | A2 |
| `type Aggregation = Literal["quarter", "hour", "day", "month", "year"]`, `AGGREGATIONS: tuple[Aggregation, ...]` | `meterdata.py` | The supported aggregations, in that order. | A3 |
| `get_meter_data(start: TimeLike, end: TimeLike \| None = None, *, aggregation: Aggregation = "hour", metering_point_ids: str \| Sequence[str] \| None = None, client: EloverblikClient \| None = None) -> pd.DataFrame` | `meterdata.py` | The meter-data frame (A3); `attrs["aggregation"]`. | A3, A4 |
| `get_charges(metering_point_ids: str \| Sequence[str] \| None = None, *, client: EloverblikClient \| None = None) -> pd.DataFrame` | `charges.py` | `RangeIndex`; columns below. | A5 |
| `main() -> None` | every module but `__init__.py` | Showcase; with no token it prints one line saying so and returns, otherwise it calls the live API (`_common.main` and `meterdata`'s alignment/windows part run offline). | — |

**`get_metering_points` columns**, in order: `metering_point_id`, `type_of_mp`,
`settlement_method`, `meter_reading_occurrence`, `balance_supplier_name`, `street_code`,
`street_name`, `building_number`, `floor_id`, `room_id`, `city_sub_division_name`,
`postcode`, `city_name`, `municipality_code`, `location_description`, `meter_number`,
`consumer_start_date` (object, `None` where null); `has_relation`, `is_moved_out` (`bool`);
`child_metering_point_ids` (a `tuple[str, ...]` per row, `()` when none). Consumer names and
CPR/CVR fields are left out.

**`get_metering_point_details` columns**: the API field name converted by two regex passes —
`([A-Z]+)([A-Z][a-z])` → `\1_\2`, then `([a-z\d])([A-Z])` → `\1_\2` — lower-cased with `__`
collapsed (`typeOfMP` → `type_of_mp`, `powerLimitKWDecimal` → `power_limit_kw_decimal`,
`gridOperatorID_SchemeAgencyIdentifier` → `grid_operator_id_scheme_agency_identifier`).
`power_limit_kw_decimal` is `float64` (NaN where null); every other column object (`None`
where null). `contactAddresses` and `childMeteringPoints` are left out.

**`get_meter_data` frame**: index `"time"`, `datetime64[ns, Europe/Copenhagen]`, the interval
starts of `[start, end)` on the Danish calendar; columns a `MultiIndex` named
`("metering_point_id", "field")`, points in request order, fields `energy_kwh` (`float64`)
then `quality` (object, the raw code); a missing interval is NaN in both. Without `end`, the
index runs to the end of the last interval any point returned; nothing returned gives the
empty frame (zero rows, same columns, empty tz-aware `"time"` index).

**`get_charges` columns**: `metering_point_id`, `kind` (`"subscription"`, `"tariff"`,
`"fee"`), `price_id`, `name`, `description`, `owner`, `period_type` (object); `valid_from`,
`valid_to` (`datetime64[ns, Europe/Copenhagen]`, NaT where null); `price` (`float64`);
`quantity`, `position` (`Int64`, `<NA>` where the charge kind has none). Rows per point in
request order: subscriptions, tariffs (each price position its own row, by position), fees.
No charges gives an empty frame with the same columns and dtypes.

### Implementation guide

1. **`utils` changes.** Add `headers`/`json` to `ApiClient.request`, passed through (the
   retry wrapper merges headers over the client's). Rename `_gather_ordered` →
   `gather_ordered`, re-export it, update the two importers. Update `structure-utils.md`
   and delete the `DEVELOPMENT.md` entry asking for it.
1a. **Test support first, before any Eloverblik code is written** (so the mock is built
   from the spec, not from the parser): the spec fixture and `EloverblikService` described
   in step 10.
2. **`client.py`.** Constants, error types, constructor (token from argument or
   environment, stripped; not printable ASCII without spaces → refused at the first
   request without echo, like Min Strøm). Private `_access_token()` under an
   `asyncio.Lock` created lazily on the client's loop: `GET /token` with
   `Authorization: Bearer <refresh>`; `{"result": <non-empty str>}` else `EloverblikError`.
   `fetch_json`: check token → ensure access token → `request(..., headers={"Authorization":
   f"Bearer {access}"}, json=body, fmt="json")`; on a 401 `HTTPStatusError` it takes the
   token lock and fetches a new access token only if the cached one is still the one that
   was rejected (otherwise it uses the one another request already refreshed), then retries
   once; a second 401 raises. Concurrent 401s therefore cost one `/token` call. Error mapping
   (`_api_error`): status; `X-Correlation-ID`; from a JSON object body, `errorCode` (int)
   and the text from `errorText`, else `detail`, else `title` (ProblemDetails). A 401 or
   403 from `/token` says the refresh token was rejected. `RetriesExhaustedError` /
   `HoldoffTooLongError` with a response map the same way; a transport failure stays
   `RetriesExhaustedError`; a 2xx body that is not a JSON object is `EloverblikError` with
   `status` 200. No message ever contains either token.
3. **`_common.py`.** `_normalise_ids`, `_client_scope`, `_linked_ids(client)` (from
   `/meteringpoints/meteringpoints`), `_batches(ids)` (≤ 10, order kept),
   `_post_points(client, path, ids)` → per-point results **mapped by their `id`**, not by
   position (batches concurrent through `gather_ordered`): an `id` that was not requested
   raises `EloverblikError`; a requested id with no result raises naming it; a repeated id
   keeps all its results (meter data merges their series under step 6's duplicate rule;
   details and charges take one row per result), and `_unpack(result, path)` reading
   `success`/`errorCode`/`errorText`/`id` and raising `EloverblikError(status=200,
   error_code, error_text, metering_point_id, correlation_id)` for a failure the caller does
   not tolerate. Payload-shape helpers (`_text`, `_nullable_text`, `_number`) raising
   `EloverblikError` naming the field. `main()` offline: id normalisation and batching.
4. **`meteringpoints.py`.** Both functions per the column tables.
5. **`meterdata.py` — periods.** Alignment: `quarter`/`hour` through `resolve_period` with
   that resolution; `day`/`month`/`year` additionally require local midnight / the 1st /
   1 January (as Min Strøm does). With `end=None`, `start` (a date or a timestamp) begins
   an open period: `resolve_period` is used only for alignment and its one-slot `end` is
   discarded, so a lone date without `end` runs past that day. Request dates: `dateFrom` =
   local date of `start`; `dateTo` = local date of `end`, plus one day when `end` is not
   local midnight; `end=None` requests through tomorrow. "Today" is read only through
   `pd.Timestamp.now(DANISH_TZ)`. `dateTo` is clamped to tomorrow's local date and
   `dateFrom` to the **history horizon**, 1 January of (current local year − 5); a part
   wholly after today or wholly before the horizon is not requested, and its intervals are
   NaN (with `end`) — mirroring each other. Windows on Danish calendar dates: `quarter`/`hour`/`day` at
   most 730 days, `month` 12 months, `year` 1 year — abutting, so nothing is requested
   twice; each window × each batch of 10 ids is one `POST
   /meterdata/gettimeseries/{from}/{to}/{Quarter|Hour|Day|Month|Year}`, all through
   `gather_ordered`.
6. **`meterdata.py` — parsing.** Per point result: `30015` → no rows for that point;
   another failure → raise. `MyEnergyData_MarketDocument.TimeSeries[]`: `mRID` is the point
   (a series for a point not asked for is an error); `measurement_Unit.name` must be `KWH`
   (case-insensitive); each `Period` needs `resolution` equal to the request's
   (`PT15M`, `PT1H`, `P1D`, `P1M`, `P1Y`) else raise naming point, asked and returned;
   `timeInterval.start` parsed as UTC. Position `p` (1-based integer string) starts at
   start + (p−1)·resolution — elapsed time for `PT15M`/`PT1H`, Copenhagen calendar steps
   for `P1D`/`P1M`/`P1Y`. `out_Quantity.quantity` null or absent gives `energy_kwh` NaN with
   the quality kept (the spec's `A02` "no quantity returned"); text present but not a
   finite number raises. `quality` a string or absent. Points outside `[start, end)` are dropped; the same start twice for a point is
   kept once when identical and an error when not.
7. **`meterdata.py` — frame.** Calendar interval index (elapsed for quarter/hour via
   `period_index`, `date_range` on local wall time for day/month/year); place each point's
   rows; NaN elsewhere; `MultiIndex` columns built directly, because `combine_levels` /
   `conform` are float64-only and `quality` is text; `attrs["aggregation"]`. The docstring
   says the interval holding today may be incomplete.
8. **`charges.py`.** Per the column table; `validFromDate`/`validToDate` parsed with
   `pd.to_datetime(..., utc=True)` → Copenhagen (naive strings read as Copenhagen),
   unparseable → `EloverblikError`. Tariff `position` arrives as a string and is parsed to
   `Int64` (not an integer → `EloverblikError`); a tariff with an empty `prices[]` gives one
   row with `price` NaN and `position` `<NA>`, so the charge is still listed.
9. **`__init__.py`** re-exports: `BASE_URL`, `REFRESH_TOKEN_ENV`, `DEFAULT_POLICY`,
   `EloverblikClient`, `EloverblikError`, `MissingTokenError`, `Aggregation`, `AGGREGATIONS`,
   the four `get_*`.
10. **Test support.** Save the spec as `tests/fixtures/eloverblik_openapi.json` — copy
    `C:\Users\peter\AppData\Local\Temp\claude\C--Users-peter-OneDrive-REPOS-energydata\a22383bd-cc17-4c58-8c90-940f3d876b29\scratchpad\eloverblik_swagger.json`
    (fetched 2026-11-02), or re-fetch
    `https://api.eloverblik.dk/customerapi/swagger/customerapi-v1.0/swagger.json` if it is gone. In
    `conftest.py`, `EloverblikService` — written from the spec, not the package: `/token`
    (refresh token check, issues access tokens, can be told to expire one), bearer check
    (401 ProblemDetails), `/isalive`, the four data endpoints, request-level validation as
    documented (30000, 30001, 30002, 30003, 30004, 30008, 30014, 20013 as HTTP 400
    ProblemDetails carrying `errorCode`), per-point results (unknown point 20008, scripted
    per-point codes scriptable per point **and per request window**, such as 30015 or
    30018; results returnable out of order, missing or repeated; Points with `A02` and no
    quantity; request-level 30017 for a `dateTo` on or before the history horizon),
    generated CIM data under the stated assumptions
    (`dateTo` exclusive, Danish dates, `A04` quality, DST-correct), optional per-point
    resolution override and gaps, an `X-Correlation-ID` on every response, every request
    recorded; plus `eloverblik_service` fixture and small builders.
11. **Live suite** `tests/test_eloverblik_live.py` (see T11) and the `pyproject` marker text.
    One module-scoped `EloverblikClient`, passed to every function, so the run spends one
    `/token` call. Its fixture, when `ELOVERBLIK_LIVE_MOCK=1`, calls
    `block_real_transports` first and builds the client on an `EloverblikService` transport
    with instant retries.
12. **Runner** at the scratchpad path above (see T12). Not committed; the build writes it,
    step 6 checks it exists and runs.
13. **Docs**: README section (token from the portal: *Datadeling → Opret token*; the
    variable; functions, columns, units, quality codes; pass one client for several calls,
    because `/token` allows two calls a minute; the interval holding today may be
    incomplete; the five-year horizon; the runner and the live command; never add `-l`), `STRUCTURE.md` tree and subpackage section,
    `.claude/rules/structure-eloverblik.md`, `structure-utils.md`. `DEVELOPMENT.md`: the
    calendar-helper duplication note.

### Test intents

| # | Must prove | Covers |
|---|---|---|
| T1 | `get_metering_points` columns, dtypes, child-id tuples, `include_unlinked` sending `includeAll=true`, empty account → empty frame with the same columns; no name or CPR/CVR column. | A1 |
| T2 | `get_metering_point_details` snake_case mapping for every scalar spec field, `power_limit_kw_decimal` float, nested fields absent, request order kept, `None` → linked points, none linked → `ValueError`, a per-point error raising with its code and point. | A2 |
| T3 | `get_meter_data` at every aggregation: index (23/25 hours and 92/100 quarters on DST days, local midnight/month/year starts), two-level columns and dtypes, `attrs`, missing interval NaN never zero, sub-day `[start, end)` trimmed from whole-day requests, `end=None` to the last returned interval and empty frame when nothing returned, misaligned bounds → `ValueError`, unknown aggregation → `ValueError`. | A3 |
| T4 | Resolution mismatch, unit not kWh, unknown `mRID`, malformed position, non-numeric quantity text, conflicting duplicates → `EloverblikError` naming the point; identical duplicates kept once; a Point with `A02` and a null or absent quantity gives NaN energy and quality `A02` in that row, other rows unaffected; `30015` → all-NaN column while other points have data; the tolerated per-point codes (F1, decided with the user) give NaN for that point in that window only — a point whose first window fails that way while later windows have data gives NaN then values. | A3, A7 |
| T4b | Result mapping: per-point results returned out of order are matched by `id`; a requested id missing from the response, or an `id` not requested, raises naming it; a repeated id's series are merged under the duplicate rule. | A3, A4 |
| T4c | `_common`: a string becomes one id after stripping; an empty sequence, a 17-digit, 19-digit or non-digit id, or a duplicate is a `ValueError` naming it, before any request; a created client is closed afterwards (also on error) and a passed one left open. | A2, A3, A5 |
| T5 | Dates, with the clock pinned through `pd.Timestamp.now`: `dateTo` clamped to tomorrow and `dateFrom` to the history horizon; a part wholly in the future or wholly before the horizon not requested (NaN with `end`); request dates exactly as in guide step 5; a lone date with no `end` runs past that day. | A3, A4 |
| T6 | Long periods and many points: windows ≤ 730 days / 12 months / 1 year on calendar dates, abutting, no interval twice, ≤ 10 points per request, 23 points → 3 batches, result equal to a cap-free mock's single call; the lowest-index failure raised, never an `ExceptionGroup`. | A4 |
| T7 | `get_charges` rows, kinds, positions (string → `Int64`, non-integer → `EloverblikError`), a tariff with empty `prices[]` → one row with NaN price, dtypes, timestamp parsing, ordering, empty → empty frame, malformed date → `EloverblikError`. | A5 |
| T8 | Token: argument beats environment, stripping, blank = missing, `MissingTokenError` only at first request naming the variable, one `/token` call shared by concurrent requests, reuse across calls, one refresh on 401 then success, two concurrent requests that both get 401 causing exactly one `/token` call, a second 401 raising, a rejected refresh token, unusable token refused unechoed; neither token in any message, `repr` or `str(exc)` across every error path. | A6 |
| T9 | Errors: ProblemDetails with and without `errorCode`, non-JSON error body, per-point failures, correlation id carried, 429/503 retried then mapped, transport failure stays `RetriesExhaustedError`, 2xx non-object body. | A7 |
| T10 | Contract: every path, method, parameter and field the package reads exists in the spec fixture; the mock's bodies validate against it; exports; showcases run (offline ones fully, live ones print the no-token line); `ApiClient.request` passes `headers`/`json` and stays identical without them; `gather_ordered` behaves as `_gather_ordered` did. | A3, A4, A6, A9 |
| T11 | Live suite: every test marked `live`, skipped without `ELOVERBLIK_REFRESH_TOKEN`, deselected by plain `pytest`; covers token, metering points, details, every aggregation on a recent period, an autumn-DST hourly day, `dateTo` exclusivity (a one-day `day` request yields one row; raw `timeInterval` reported), quality codes seen, `quarter` on each point (result or error recorded), charges, the shape of one deliberately invalid request's error body, a one-day `day` request for each child id from `get_metering_points` (result or error code recorded — production for net-settled solar lives there), and one 730-day `day` request on one point (success or `30014` recorded). One module-scoped client, so one `/token` call. Each test writes report lines to the file named by `ELOVERBLIK_LIVE_REPORT` — point ids as `…` plus the last four digits, never names, addresses, CPR/CVR or tokens. The suite also runs offline against `EloverblikService` when `ELOVERBLIK_LIVE_MOCK=1`; an offline test runs it so in a subprocess and asserts all pass, nothing private leaks and no real transport was reached. | A8 |
| T12 | Runner: prompts for the token with `getpass` unless `ELOVERBLIK_REFRESH_TOKEN` is set, runs the live suite from the repo root with the token and a report file in the child's environment, scrubs the output (the refresh token, any JWT, any 18-digit number → `…` + last four), prints the pytest summary and an `ELOVERBLIK LIVE REPORT` block. The scrub function lives in `tests/test_eloverblik_live.py` and is tested offline; step 6 runs the runner once with `ELOVERBLIK_LIVE_MOCK=1` and a dummy token. | A8 |
| T13 | Docs: README names every function and the variable; `STRUCTURE.md` and `structure-eloverblik.md` list every module and public signature. | A9 |

**Coverage check:** every criterion A1–A9 has a Public API entry (A8 is the live suite and
runner rather than a signature; A9 the docs) and at least one test intent; every Public API
entry traces to a criterion (`main()` is the repo convention, not a feature).

### Risks

- **The real API differs from the spec or the stated assumptions** (`dateTo` inclusive,
  error bodies without `errorCode`, `quarter` behaviour, date formats in charges). Do not
  halt: build to the spec and the assumptions in section 1; the live run after the build
  finds the differences, and they are fixed before the pull request.
- **The `utils` changes break an existing caller.** Defaults must reproduce today's
  behaviour exactly; the full existing suite must stay green — a failure there is the
  build's to fix, not a halt.
- **The mock mirrors the package's mistakes.** The mock is written from the spec and the
  section-1 assumptions first (guide step 1a), before any Eloverblik code; the contract
  test checks its bodies against the spec.
- **The interval holding today is partial.** `dateTo` is clamped to tomorrow, so the
  current day, month or year row is a partial sum, not NaN. Docstring and README say so; no
  code change.
- **Calendar arithmetic on DST days** (`P1D` positions, 23/25-hour days, quarter counts):
  tests pin both changes of 2025 and 2026.
- **Token lock across the loop.** The `asyncio.Lock` must be created on the client's loop
  (lazily), never in `__init__`.
- **The runner lives outside the repo.** If the session is lost before the live run, step 8
  regenerates it from T12; it holds no secret.
- If any of the above would change a criterion (e.g. the API cannot give `end=None`
  semantics as written), halt.

### Critique

`plan-critic`, 2026-11-02 — verdict **accept with the changes above**; it judged the
`utils` amendment the right call.

- **F1** Per-point `30018`/`30010`/`30016` are routine (point registered later, authorization
  or relation period), and "any other per-point error raises" would break ordinary and
  windowed calls. — Applied after the user's decision: NaN for that point in that window, like
  `30015` (section 1 amended; T4, mock).
- **F2** `A02` Points arrive with no quantity. — Applied: null/absent quantity → NaN with quality kept (step 6, T4, mock).
- **F3** The five-year horizon was unhandled. — Applied: `dateFrom` clamped to 1 January of (year − 5), parts before it unrequested and NaN, clock read through `pd.Timestamp.now` (step 5, T5, mock 30017).
- **F4** Results must map by `id`, not position. — Applied (step 3, T4b, mock).
- **F5** Lone date without `end` was ambiguous. — Applied: open period, `resolve_period` only for alignment (step 5, T5).
- **F6** Live-on-mock wiring unspecified. — Applied: fixture calls `block_real_transports` and uses the mock when `ELOVERBLIK_LIVE_MOCK=1`; the subprocess test asserts no real transport (step 11, T11).
- **F7** `/token` limit and untested unknowns in the live run. — Applied: one module-scoped client; child-point and 730-day probes; README advice (step 11, 13, T11).
- **F8** Concurrent 401s would each refresh. — Applied: refresh only if the cached token is the rejected one (step 2, T8).
- **F9** Untested `ValueError` and parse branches. — Applied: T4c; tariff position parsing and empty `prices[]` decided (step 8, T7).
- **F10** Mock order contradicted the risk. — Applied: mock first, step 1a.
- **F11** Why `combine_levels` is not used. — Applied (step 7).
- **F12** Partial current interval. — Applied as a risk and doc note (step 7, 13, Risks).

Not clean: F1 and the `utils` amendment both change section 1, so the user decides.
The user chose the recommended answers for both; section 1 amended accordingly.

Accepted: by the user, 2026-11-02

---

## 3. Implementation notes

Built in the guide's order: the `utils` changes, then the spec fixture and `EloverblikService`
(step 1a, before any Eloverblik code), the client, `_common`, the three function modules, the
live suite, the runner, the docs. The Public API table is unchanged: every signature exists as
written. Departures and decisions the plan left open:

- **Lenient absent fields.** In the point listing and in the details, a text field that is
  absent counts as null (only the point id and the two booleans of the listing are required);
  the spec marks them all nullable and the real API may omit nulls. A field present with the
  wrong type is an `EloverblikError`.
- **Charges.** A subscription or fee needs `price` (number) and `quantity` (integer, number or
  integer text); both validity dates are optional; a naive date text is read as Danish time.
- **Windows.** A month window is the first of the month plus 12 months, a year window one
  calendar year, a day/hour/quarter window 730 days; the date after `end` is added when `end`
  is not local midnight.
- **`EloverblikClient.fetch_json`** retries once on any `EloverblikError` with status 401 from
  the data endpoint; a 401 or 403 from `/token` is mapped with a "refresh token was rejected"
  message and is never retried.
- **Mock.** `EloverblikService` reports an id that is not 18 digits as `20003` and a well-formed
  id that is not linked as `20008` (also in `getdetails` and `getcharges`); the child-point test
  of the live suite therefore records `20008` offline.
- **Existing suite.** Plain `pytest` after the `utils` change: 2043 passed, 2 errors, both in
  `tests/test_readers.py` (a `[[[[...` parametrize id); `readers.py` and its tests are untouched
  by this round and the errors reproduce on their own, so they pre-date it.
- **Runner.** Written at the scratchpad path of guide step 12 (outside the tree, not committed);
  run once offline with `ELOVERBLIK_LIVE_MOCK=1` and a dummy token: it prints the pytest tail and
  the `ELOVERBLIK LIVE REPORT` block, all ids shortened.
- **`DEVELOPMENT.md`.** The `_gather_ordered` entry is deleted (resolved); the `__cause__`
  remark in it is kept as a shorter entry; the calendar-helper duplication note is added.

---

## 4. Verification log

| Check | Result |
|---|---|
| `ruff check .` | `All checks passed!` |
| `ruff format --check .` | `101 files already formatted` |
| `mypy` | `Success: no issues found in 64 source files` |
| `pytest` | first run: 1 failed, 1681 passed, 38 deselected (`-x`): `test_minstroem_spec.py::test_the_pyproject_registers_the_live_marker_and_deselects_it_by_default` asserted the old marker text; the plan changes that text on purpose (guide: `pyproject.toml` marker names both live APIs), so the assertion was updated to the new text. Re-run (whole suite): `2725 passed, 38 deselected, 2 errors` — the 2 errors are the `tests/test_readers.py` ones below, present on `origin/main` too |
| Plan completeness | every signature in the Public API table exists as written (checked with `inspect.signature`: `ApiClient.request` with `headers`/`json`, `gather_ordered`, the constants, `EloverblikError`, `MissingTokenError`, `EloverblikClient` and its `has_refresh_token`, `fetch_json`, `get_json`, the four `get_*`, `Aggregation`, `AGGREGATIONS`; `main()` in every module but `__init__.py`). No Missing, Deviation or Unplanned. Extra public name `BATCH_SIZE` in `_common.py` is a module constant of a private module, now listed in the rules file |
| `STRUCTURE.md` | in sync after the auditor's edits below |
| `python -m energydata.eloverblik.<module>` | `_common`: batches `[10, 10, 3]`, padded id accepted, 17-digit id refused, then the no-token line. `client`, `meteringpoints`, `charges`: the no-token line (`Set ELOVERBLIK_REFRESH_TOKEN to run this showcase against the live Eloverblik API.`). `meterdata`: offline windows and alignment lines, then the no-token line. All exit 0; only the expected `RuntimeWarning` about re-export. |

**Structure-auditor findings (5), all applied:** (1) `structure-eloverblik.md` `_common.py` section listed private helpers and omitted `BATCH_SIZE` — rewritten as a signature table. (2) `structure-utils.md` conftest bullet now names the Eloverblik support. (3) `STRUCTURE.md` tests line names `test_eloverblik_*.py` and the Eloverblik spec fixture. (4) `structure-eloverblik.md` mock sentence now lists `points`, `unlinked`, `details`, `charges`, `point_ids`, `token_calls`. (5) `STRUCTURE.md` README line and `chunking.py` description updated. Each checked against the code as it stands (`BATCH_SIZE` at `_common.py:18`, attributes at `conftest.py:717-740`).

**`tests/test_readers.py` errors (step 3's note):** the 2 errors are not from this round. In a throwaway worktree of `origin/main` (removed afterwards), `pytest tests/test_readers.py` gives `151 passed, 2 errors`: a `[[[[…` parametrize id in `test_read_json_raises_a_parse_error_naming_json` makes `PYTEST_CURRENT_TEST` longer than 32767 characters, which Windows refuses (`ValueError: the environment variable is longer than 32767 characters`) at teardown. A pre-existing Windows-only defect on `main`; not fixed here (out of scope).

---

## 5. Test log

Reader reports merged: `input-space` and `contract` (both saved verbatim in the session scratchpad).

| Intent | Test names (file) | Result |
|---|---|---|
| T1 | `test_metering_points_*` (`test_eloverblik_meteringpoints.py`): columns, dtypes, child tuples, `includeAll`, empty frame, no name/CPR/CVR | pass |
| T2 | `test_details_*`, `test_snake_case_mapping` (same file): every spec scalar in order, float column, nested dropped, order, none-linked, per-point error | pass |
| T3 | `test_the_hour_frame_*`, `test_dst_days_*`, `test_day_month_and_year_*`, `test_bounds_not_on_the_aggregation_*`, `test_an_unknown_aggregation_*`, `test_a_gap_is_nan_*`, `test_an_open_period_*` (`test_eloverblik_meterdata.py`) | pass |
| T4 | `test_a_resolution_*`, `test_a_unit_*`, `test_a_series_for_a_point_*`, `test_a_malformed_position_*`, `test_quantity_*`, `test_30015_*`, `test_a_tolerated_code_*`, `test_a_repeated_*` (same) | pass |
| T4b / T4c | `test_results_returned_out_of_order_*`, `test_a_requested_id_without_a_result_*`, `test_a_malformed_id_*`, `test_a_created_client_is_closed_*`, ... (`test_eloverblik_common.py`) | pass |
| T5 | `test_the_request_dates_*`, `test_the_end_date_is_clamped_*`, `test_dates_before_the_history_horizon_*`, `test_a_period_wholly_*`, `test_the_horizon_moves_with_the_year` (meterdata, clock pinned) | pass |
| T6 | `test_windows_follow_each_aggregations_cap_*`, `test_a_long_period_equals_*`, `test_each_window_and_batch_*`, `test_the_lowest_failing_request_*`; `gather_ordered` group in `test_chunking.py` | pass |
| T7 | `test_charges.py`-style group in `test_eloverblik_charges.py` (rows, order, sorted positions, null position, empty prices, dtypes, dates, empty frame) | pass |
| T8 | token group in `test_eloverblik_client.py` (argument vs env, blank, unusable unechoed, one `/token` call, concurrent 401s, second 401, rejected refresh token, redaction of echoed tokens) | pass |
| T9 | error-mapping group in `test_eloverblik_client.py` (ProblemDetails shapes, non-JSON body, correlation id, 429/503, transport failure, 2xx non-object) | pass |
| T10 | `test_eloverblik_spec.py` (paths, parameters, fields, mock bodies validated against schemas, exports, showcases incl. subprocess `-m`), `test_api_client.py` (headers/json), `test_chunking.py` (`gather_ordered`) | pass |
| T11 | `test_eloverblik_spec.py`: deselected by plain `pytest`, skipped without/blank token, 17 live tests collected, offline run with `ELOVERBLIK_LIVE_MOCK=1` in a subprocess (17 passed, no token/id/name/CVR in output or report, no real transport) | pass |
| T12 | `test_scrub_*` (spec file). The runner itself (outside the repo) is run by step 6 | pass |
| T13 | `test_the_readme_names_*`, `test_structure_md_*`, `test_the_rules_file_*`, `test_the_utils_rules_file_*` | pass |

Whole tree: `ruff check .`, `ruff format --check .`, `mypy` clean; `pytest`: 3274 passed, 38 deselected, 2 errors. The 2 errors are the pre-existing Windows-only `tests/test_readers.py` ones (section 4), excluded: not this round's.

**Bugs the tests found, fixed in code:** (1) ids accepted non-ASCII digits (`\d` -> `[0-9]`); (2) a point listed twice by the account multiplied rows (linked ids de-duplicated); (3) a server error text echoing a token leaked into the message and `error_text` (tokens of 6+ characters replaced by `<token>`); (4) an empty/`NaT`/out-of-range `timeInterval.start` silently dropped data (now `EloverblikError`); (5) a huge `position` leaked a raw pandas overflow (now `EloverblikError`); (6) parse errors in `_collect` did not carry `metering_point_id` (now set); (7) a result carrying another requested point's series was accepted (mRID must equal the result's id); (8) tariff rows were in API order (now by position, API order for ties); (9) a null tariff `position` (nullable in the spec) raised (now `<NA>`, sorted last); (10) a charge date beyond the ns range leaked a raw pandas error (now `EloverblikError`); (11) `bytes` as ids named the first byte (now refused by name); (12) `get_meter_data` docstring overstated `MissingTokenError`. Decisions on the record: (7) and (9) read section 2's column contract and the spec; `9999-12-31` charge dates raise a clean `EloverblikError` rather than becoming NaT (the live run shows whether the API sends them).

Reader cases not written, with reasons: `_unpack` string-coded `errorCode` is pinned (not tolerated) pending the live run; `1e400` charge price cannot be serialised by the mock (covered through `_number`); non-ASCII digits in a `position` text are accepted by `int()` (low value); nonexistent naive charge timestamps are covered by the ambiguity case; purity and idempotency are covered once per function.

**Open (halt): per-point errors carry no `correlation_id`.** See Halted.

Edge cases considered and deliberately skipped: a real transport reaching the network (the autouse guard and the live-mock subprocess run cover it); the live runner script (outside the repo, step 6 runs it).

---

## 6. Concept check

| # | Criterion | Met | Evidence |
|---|---|---|---|
| A1 | | | |

Drift found, and what was done about it:

### Earlier rounds still hold

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

| # | Recommendation | Why it is critical | Effort | Decision |
|---|---|---|---|---|
| R1 | | | | |

Decisions: `deferred`, `rejected`, or `next round` — a new numbered file in this folder,
taken back through steps 1 to 7 on the same branch.

---

## 9. Pull request

| Field | Value |
|---|---|
| URL | opened by step 9 — see the branch's pull request |
| Opened as | ready for review |

---

## Halted

Step 5 is complete except for one decision that is the user's. **A7 says a per-point API error carries "the correlation id" (also guide step 3), but a per-point failure arrives inside a 200 response and `ApiClient.request` returns only the parsed body, so the response headers are gone by the time `_unpack` raises: `correlation_id` is `None` for every per-point error** (the request-level 4xx/429/503 errors do carry it, tested). Either answer is a change to section 1: (a) add a third backward-compatible `utils` change (e.g. `ApiClient` exposing the response, or a `request_response` variant) so the header reaches `_unpack`, amending "touches no existing module beyond..." again; or (b) amend A7 to say per-point failures carry status, code, text and point, and the correlation id only for request-level errors. No test pins the missing id; resume with `/build` once answered (the marker stays at step 5 and the suite is otherwise green).

**Answered by the user, 2026-11-02: (a).** Per-point errors carry the correlation id. A third
backward-compatible `utils` change is added: `ApiClient.request_response`, returning the raw
response, with `request` built on it (section 1 "How it connects" and the section 2 Modules
and Public API tables amended). `EloverblikClient` uses it so the `X-Correlation-ID` of the
200 response reaches every per-point `EloverblikError`. The build resumes at step 5: implement
it, test it (A7: a per-point error's `correlation_id` equals the response header, for meter
data, details and charges), update `structure-utils.md`, `structure-eloverblik.md` and
`STRUCTURE.md`.
