# Eloverblik Customer API: meter data and charges

<!-- claude-plan step=2 status=active -->

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
| 2 | Plan | `/plan` | with the user, unless the critique is clean | pending |
| 3 | Implement | `/implement` | in `/build` | pending |
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
point — never placed on the wrong grid. A point whose result is `30015` (no data) gives an
all-NaN column; any other per-point error raises.

### How it connects to the rest of the repo

Calls `utils`: `ApiClient` (loop, concurrency, retry — 429 and 503 retried with backoff),
`resolve_period`, the period index and frame helpers in `utils/frames.py` (including the
two-level column combining), and the chunked fetch helpers where they fit. Nothing calls
it, and it touches no existing module beyond re-exports and docs. The `DEVELOPMENT.md` note
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

One paragraph on the chosen approach, and one on what was rejected and why.

### Modules

| Path | New or changed | Purpose |
|---|---|---|

### Public API

| Signature | Module | Purpose | Covers |
|---|---|---|---|

### Implementation guide

Ordered. Each entry small enough to finish and check.

1.
2.

### Test intents

| # | Must prove | Covers |
|---|---|---|
| T1 | | |

### Risks

What could make this harder than it looks, and what the build should do if it does —
including whether it should halt.

### Critique

---

## 3. Implementation notes

---

## 4. Verification log

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

| Intent | Test names | Result |
|---|---|---|

Edge cases considered and deliberately skipped, with reasons:

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

