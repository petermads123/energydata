# Min Strøm Consumer API, with price forecasts first

<!-- claude-plan step=2 status=active -->

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
| 2 | Plan | `/plan` | with the user | pending |
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

> Written in step 2, accepted by the user before step 3 starts. Concrete enough that
> step 3 is transcription, not invention.

### Approach

One paragraph on the chosen approach, and one on what was rejected and why.

### Modules

| Path | New or changed | Purpose |
|---|---|---|

### Public API

> Every public class and function, with its full signature as it will be written.
> `Covers` links back to the acceptance criteria above.

| Signature | Module | Purpose | Covers |
|---|---|---|---|

### Implementation guide

Ordered. Each entry small enough to finish and check.

1.
2.

### Test intents

> High-level: what a test must prove, not how it is written. Step 5 turns each of these
> into concrete cases, including the edge cases.

| # | Must prove | Covers |
|---|---|---|
| T1 | | |

### Risks

What could make this harder than it looks, and what the build should do if it does —
including whether it should halt.

---

## 3. Implementation notes

> Written in step 3. Only deviations from the plan above, each with its reason. "Built as
> planned" is a complete and good entry. On a fix round, also the reproduction test's red
> run, pasted here before the fix was written — step 6 cites it.

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
