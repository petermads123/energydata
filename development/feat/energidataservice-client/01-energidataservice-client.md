# Energi Data Service client and day-ahead prices

<!-- claude-plan step=2 status=active -->

| Field | Value |
|---|---|
| Feature | `feat/energidataservice-client` |
| Round | `1` |
| Branch | `feat/energidataservice-client` |
| Started | `2026-10-06` |

## Progress

| # | Step | Skill | Runs | Status |
|---|---|---|---|---|
| 1 | Conceptualize | `/conceptualize` | with the user | done |
| 2 | Plan | `/plan` | with the user | in progress |
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

A first source subpackage, `energydata.energidataservice`, serving one endpoint —
`get_day_ahead_prices` — on top of a reusable foundation in `energydata.utils`:

- **A generic API client base class** in `utils`: base URL, an `httpx` client it owns or is
  given, retries through the existing `request_with_retry` / `async_request_with_retry`,
  bodies parsed through the existing readers, reusable and closable. Source-agnostic, so
  ENTSO-E and Eloverblik subclass it later.
- **A concurrent chunked fetch** in `utils`: several requests run concurrently on the async
  retry wrapper, with a cap on how many run at once.
- **Period resolution** in `utils`: `start`/`end` as dates or timestamps, naive read as
  `Europe/Copenhagen`, a lone date meaning the whole local day, a lone timestamp meaning
  that one slot, half-open `[start, end)`.
- **Bidding-zone normalisation** in `utils`: one zone or a list, `DK1`/`DK2`, both by
  default.
- **Frame shaping** in `utils`: a tz-aware Copenhagen index over the full requested period
  at a given resolution, padding unpublished slots with NaN, and forward-filling coarser
  (hourly) data to the finer (15 min) grid.
- **An Energi Data Service client class** in the subpackage, subclassing the base: fetches
  a dataset by name with period, filter and columns, and returns every record. Every Energi
  Data Service call goes through it.
- **`get_day_ahead_prices`**, combining *Elspotprices* (hourly, historical) and
  *DayAheadPrices* (15 min, current).

The public function is a plain synchronous call. When one call needs several requests (the
two datasets, or a long period split into windows), those run concurrently internally on
the async wrapper; when the caller is already inside a running event loop (Jupyter), the
internal async work runs on a worker thread so the sync call still works.

The output conventions are those of `ideas/energydata/energidataservice-datasets.md`.

### Why it is worth building

Every dataset on that list needs the same calling, period handling and output shaping.
Built once in `utils` and proven by one real endpoint, the following 15-odd endpoint
functions stay thin, and the parts that are not Energi Data Service specific are reused by
the ENTSO-E and Eloverblik subpackages.

### Inputs and outputs

- **`get_day_ahead_prices`**: `start` (date or timestamp), optional `end` (exclusive),
  `bidding_zones` (one or a list of `DK1`/`DK2`, default both) → `pd.DataFrame`, wide: a
  tz-aware `Europe/Copenhagen` 15-minute index covering exactly `[start, end)`, one float
  column per selected zone, EUR/MWh excl. VAT.
- **Client**: dataset name, period, optional filter and columns → list of record dicts.
- **Utils helpers**: plain values in (dates, timestamps, zone names, records or frames),
  plain values or DataFrames out; exact shapes are step 2's.

### How it connects to the rest of the repo

Calls `utils.retry` (`request_with_retry`, `async_request_with_retry`, `RetryPolicy`) and
`utils.readers` (`read_json` / `read_response`); `utils.chunking.date_windows` for splitting
a period. Nothing calls it yet; `heatingsystem` (another repo) is the expected consumer.
`pandas` becomes the second runtime dependency, kept out of the existing pandas-free
`retry`, `readers` and `chunking` modules. `hello_world` is not touched.

### Explicitly out of scope

- Every other dataset: imbalance, mFRR, aFRR, FCR (DK1 and DK2), FFR, DSO and Energinet
  tariffs, subscriptions, elafgift.
- Async public functions — async is internal only.
- Caching, FX conversion (day-ahead has a EUR column), ENTSO-E and Eloverblik.
- Deleting `hello_world` and resolving the flat-`tests/` layout question in
  `DEVELOPMENT.md` (still only one source subpackage after this round).
- A live-API test: the suite mocks HTTP; the module showcase is the live check.

Assumptions:

- Day-ahead fetches both *Elspotprices* and *DayAheadPrices* over the requested period;
  where both have a value for a slot, the 15-minute dataset wins. No switch date is
  hardcoded.
- A lone timestamp not on a 15-minute boundary is a `ValueError`, not rounded.
- The development container cannot reach `api.energidataservice.dk` (proxy 403), so dataset
  and column names are taken from the API documentation and confirmed by the user running
  the showcase.

### Acceptance criteria

| # | The finished feature... |
|---|---|
| A1 | `get_day_ahead_prices(start, end=None, bidding_zones=...)` returns a DataFrame with a tz-aware `Europe/Copenhagen` 15-minute index covering exactly `[start, end)` and one column per selected zone (`DK1`, `DK2`; both by default; in the order given), values EUR/MWh floats. |
| A2 | Periods before the 15-minute switch return the hourly Elspot price forward-filled to all four quarter-hours; a period spanning the switch combines both datasets, and on overlap the 15-minute value wins. |
| A3 | Naive input is read as Danish local time; a lone date returns that whole local day (92 / 96 / 100 rows on DST days); a lone timestamp returns one row; `end` is exclusive. `start >= end`, a misaligned lone timestamp, an unknown zone or an empty zone list raise `ValueError` naming the value. |
| A4 | Slots with no published data are present as NaN — e.g. tomorrow before publication, or a period entirely in the future returns an all-NaN frame of the right shape. |
| A5 | A source-agnostic `ApiClient` base in `energydata.utils` does the calling (base URL, retries via the existing wrapper, reusable and closable); the Energi Data Service client subclasses it, fetches a dataset by name with period, filter and columns, and returns every record, never a truncated page. Every Energi Data Service call goes through it. |
| A6 | When a call needs several requests (two datasets, or a period split into windows over a maximum span), they run concurrently up to a configurable cap; the public function is a plain sync call that also works when called from inside a running event loop (Jupyter). |
| A7 | Period resolution, zone normalisation and frame shaping live in `energydata.utils`, contain no Energi Data Service specifics, and `get_day_ahead_prices` uses them. |
| A8 | The README lists the endpoint with currency, unit, resolution, format, zones and source datasets, and the function's docstring says the same; tests make no network calls; `pandas` is the only new runtime dependency. |

### Open questions

None.

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

