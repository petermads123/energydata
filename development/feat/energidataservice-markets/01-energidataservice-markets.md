# Energi Data Service market price endpoints

<!-- claude-plan step=2 status=active -->

| Field | Value |
|---|---|
| Feature | `feat/energidataservice-markets` |
| Round | `1` |
| Branch | `feat/energidataservice-markets` |
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

Nothing — this is the first round. It builds on what PR #3 (`feat/energidataservice-client`) merged to `main`.

---

## 1. Concept

### What this is

Ten new public functions in `energydata.energidataservice` that return the Danish market
prices listed in `ideas/energydata/energidataservice-datasets.md`, built on the foundation
merged in PR #3 (`EnergiDataServiceClient`, `resolve_period`, `normalize_bidding_zones`,
`period_index` / `records_to_wide` / `expand_to_resolution` / `conform`, concurrent
fetching):

| Function | Dataset (data from) | Resolution | Source fields |
|---|---|---|---|
| `get_imbalance_prices` | `ImbalancePrice` (2025-03-04) | 15 min | `ImbalancePriceEUR` (single price → both `up` and `down`) |
| `get_afrr_energy_prices` | `ImbalancePrice` (2025-03-04) | 15 min | `aFRRVWAUpEUR`, `aFRRVWADownEUR` |
| `get_mfrr_energy_prices` | `MfrrEnergyActivationMarket` (2025-03-04) | 15 min | `mFRRSAUpEUR`, `mFRRSADownEUR` (scheduled activation) |
| `get_mfrr_capacity_prices` | `MfrrCapacityMarket` (2023-06-21) | 1 h | `UpPriceEUR`, `DownPriceEUR`; volumes `Up/DownDemandMW`, `Up/DownProcuredMW` |
| `get_afrr_capacity_prices` | `AfrrReservesNordic` (2022-12-08) | 1 h | same fields as mFRR capacity; rows for NO/SE/FI dropped |
| `get_fcr_n_prices` | `FcrNdDK2` (2021-11-10) | 1 h | `PriceTotalEUR` where `ProductName = "FCR-N"`, `AuctionType = "Total"`; volumes `PurchasedVolumeLocal`, `PurchasedVolumeTotal` |
| `get_fcr_d_up_prices` | `FcrNdDK2` | 1 h | as above, `ProductName = "FCR-D upp"` |
| `get_fcr_d_down_prices` | `FcrNdDK2` | 1 h | as above, `ProductName = "FCR-D ned"` |
| `get_fcr_dk1_prices` | `FcrDK1` (2021-01-19) | 4-hour blocks (published hourly, constant per block) | `FCRcross_EUR`, `FCRdk_EUR`; volumes `FCRdomestic_MW`, `FCRabroad_MW` |
| `get_ffr_prices` | `FfrDK2` (2021-04-26) | 1 h | `FFR_PriceEUR`; volumes `FFR_DemandMW`, `FFR_PurchasedMW` |

Dataset names, field names, start dates and sample records come from the user's local probe
of the live API on 2026-10-06, committed as `eds_probe.json` beside this file (the
development container cannot reach the API). Each function reads only the dataset in use
today; earlier periods come back as NaN.

### Why it is worth building

These are the price signals the `heatingsystem` repo and any flexibility analysis need
beyond day-ahead: what imbalance costs, and what reserve capacity and activation pay. One
consistent shape (tz-aware Copenhagen index, EUR, zone/direction columns) across all of
them means a caller can join any of them with day-ahead prices directly.

### Inputs and outputs

- **Inputs:** `start`, optional `end` (same rules as `get_day_ahead_prices`); `bidding_zones`
  for the zone-split markets; `include_volumes=False` for the capacity markets; an optional
  `client` to reuse.
- **Outputs:** a `pd.DataFrame`, wide, tz-aware `Europe/Copenhagen` index covering exactly
  `[start, end)` at the function's resolution, float values.
  - Zone/direction markets: MultiIndex columns `(zone, "up" | "down")`, plus volume
    columns at the second level when requested.
  - DK2-only markets: a `price` column (plus volume columns).
  - FCR DK1: `cross_border` and `danish` columns (plus volume columns), indexed by block
    start.

### How it connects to the rest of the repo

Calls `EnergiDataServiceClient.fetch_dataset` and the `energydata.utils` helpers merged in
PR #3; touches nothing in `get_day_ahead_prices`. Generic shaping a later source could use
(e.g. building MultiIndex zone/direction frames, a calendar block grid) belongs in
`energydata.utils`, as last round established. Nothing calls the new functions yet;
`heatingsystem` is the expected consumer.

### Explicitly out of scope

- Tariffs, subscriptions and elafgift (the next round on a new branch).
- Discontinued predecessor datasets (`RegulatingBalancePowerdata`, `MfrrReservesDK1/DK2`,
  `AfrrActivatedAutomatic`, `FcrReservesDK1/DK2`).
- Per-auction FCR prices (`D-1 early`, `D-1 late`) — only `Total`.
- Direct-activation (DA) mFRR prices and the other `MfrrEnergyActivationMarket` /
  `ImbalancePrice` fields (dominating direction, satisfied demand, spot).
- FX conversion; DKK output.
- Changes to `get_day_ahead_prices`.

Assumptions:

- Imbalance is a single price in Denmark; `up` and `down` carry the same value, as the spec
  says for a one-price system.
- FCR DK1 blocks start at 00, 04, 08, 12, 16 and 20 Danish local time (seen in the probe on
  a normal day). On a DST day the block containing the shift is one hour shorter or longer;
  the index still labels block starts.
- FCR `Total` is the auction prices weighted by each auction's total purchased volume
  (verified against the probe: e.g. FCR-D down 12:00, 3.5 and 1.0 → 2.873).
- The API rate-limits (an HTTP 429 with "try again in 120 seconds" appeared during the
  probe); the existing retry wrapper honours that holdoff.

### Acceptance criteria

| # | The finished feature... |
|---|---|
| A1 | Exposes `get_imbalance_prices`, `get_mfrr_capacity_prices`, `get_mfrr_energy_prices`, `get_afrr_capacity_prices`, `get_afrr_energy_prices`, `get_fcr_n_prices`, `get_fcr_d_up_prices`, `get_fcr_d_down_prices`, `get_fcr_dk1_prices` and `get_ffr_prices`, each returning a DataFrame with a tz-aware `Europe/Copenhagen` index covering exactly `[start, end)`, with the same `start`/`end` rules as day-ahead (naive = Danish time, lone date = whole day, lone timestamp = one slot at the function's resolution, exclusive end, `ValueError` naming the value). |
| A2 | Resolution follows the market: imbalance, aFRR energy and mFRR energy are 15-minute; mFRR capacity, aFRR capacity, FCR-N, FCR-D up/down and FFR are hourly and not forward-filled; FCR DK1 is indexed by its 4-hour blocks in Danish time (00, 04, …, 20). |
| A3 | Imbalance, aFRR energy, mFRR energy, mFRR capacity and aFRR capacity take `bidding_zones` (`DK1`/`DK2`, both by default, order kept) and return columns with zone on top and `up`/`down` below: imbalance repeats its single price in both, aFRR energy is the volume-weighted up/down price, mFRR energy the SA marginal price; zones other than DK1/DK2 are dropped; a direction a zone does not procure is a NaN column. |
| A4 | FCR-N, FCR-D up, FCR-D down and FFR (DK2 only) take no zone argument and return a `price` column — for FCR the auctions' `Total`; FCR DK1 takes no zone argument and returns `cross_border` and `danish` columns. |
| A5 | Prices are in EUR: EUR/MWh for imbalance and the energy markets, EUR/MW/h for the capacity markets. |
| A6 | Capacity functions take `include_volumes=False`; when true, the dataset's volume fields are added as further columns in MW (demand and procured per direction for mFRR/aFRR; purchased local and total for FCR-N/D; domestic and abroad for FCR DK1; demand and purchased for FFR). |
| A7 | Unpublished slots and periods before a dataset starts are NaN in a full-shape frame; every call goes through `EnergiDataServiceClient`, concurrently where it needs several requests; every function works when called from inside a running event loop. |
| A8 | The README endpoints table and each docstring give the dataset, its start date, currency, unit, resolution, format and zones; dataset and field names come from the probe output, a trimmed copy of which is committed as test fixtures, and tests run on those real records without the network. |

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

