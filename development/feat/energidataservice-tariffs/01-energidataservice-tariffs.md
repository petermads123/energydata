# Energi Data Service tariffs, subscriptions and elafgift

<!-- claude-plan step=2 status=active -->

| Field | Value |
|---|---|
| Feature | `feat/energidataservice-tariffs` |
| Round | `1` |
| Branch | `feat/energidataservice-tariffs` |
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

Nothing — this is the first round. The branch is cut from `feat/energidataservice-markets` (PR #4, open), so it carries the market endpoints and `version = "1.0.0"`; neither may break.

---

## 1. Concept

### What this is

Five public functions in `energydata.energidataservice` that read Energi Data Service's
*DatahubPricelist* (subscriptions, tariffs and fees; filter column `ValidFrom`; data from
2014) and return the C-customer grid charges and the electricity tax named in
`ideas/energydata/energidataservice-datasets.md`. All values are DKK, excl. VAT.

| Function | Returns | Source rows |
|---|---|---|
| `get_dso_tariffs(dso, start, end=None)` | the DSO's standard C-customer consumption tariff, one row per hour | `ChargeType = D03`, the DSO's GLN and its mapped C tariff code |
| `get_energinet_tariffs(start, end=None)` | `system_tariff` and `transmission_tariff`, one row per hour | GLN `5790000432752`, codes `41000` (Systemtarif) and `40000` (Transmissions nettarif) |
| `get_dso_subscriptions(dso, start, end=None)` | the DSO's standard C consumption subscription, one row per validity period | `ChargeType = D01`, the DSO's mapped C subscription code |
| `get_energinet_subscriptions(start, end=None)` | the TSO system subscription, one row per validity period | GLN `5790000432752`, code `41004` |
| `get_electricity_tax(start, end=None)` | `electricity_tax`, the normal rate, one row per hour | GLN `5790000432752`, code `EA-001` |

What the data looks like (live API, 2026-10-06):
- A price-list row has a validity period, `ValidFrom` and `ValidTo` (local Danish dates),
  and `Price1`–`Price24` in DKK/kWh. `Price1` covers 00:00–01:00 local.
- `ResolutionDuration` is `PT1H` (24 hourly prices), `P1D` (one price per day, in
  `Price1`) or `P1M` (a monthly subscription, in `Price1`, DKK/month).
- Energinet's tariffs and the tax are `P1D`:
  - 2026: system 0.072, transmission 0.043, elafgift 0.008 DKK/kWh.
  - Elafgift was 0.72 in 2025.
- The TSO subscription is 15.58 DKK/month in 2026.
- About 36 DSOs publish a C-customer tariff, each under its own codes:
  - Radius `DT_C_01`, Cerius `30TR_C_ET`, N1-131 `CD`.
  - Several DSOs publish more than one C variant: plain, "time", Flex, below/above
    100 MWh, and discount rows. Which code is "the standard C consumption tariff" and
    "subscription" is therefore a curated mapping, built from the data in step 2 and shown
    to the user before the build.

### Why it is worth building

These are the remaining cost components a consumer in Denmark pays per kWh on top of the
spot price: the DSO tariff, Energinet's tariffs and the electricity tax. With them, the
`heatingsystem` repo can compute the full variable price of an hour. It joins on the
`start`/`end` columns against the 15-minute day-ahead prices.

### Inputs and outputs

- **Inputs:** `dso` (a friendly name, for the DSO functions), `start` and an optional `end`
  (the same rules as the market functions, at hourly resolution), and an optional `client`.
- **Hourly outputs** (tariffs, tax):
  - A long `pd.DataFrame` with tz-aware `Europe/Copenhagen` `start` and `end` columns, one
    half-open row per hour of `[start, end)` in order (23 or 25 rows on DST days), plus the
    value column(s) as float DKK/kWh.
  - Each hour takes the row valid on its local date. A `PT1H` row contributes the price for
    its local wall-clock hour, so the repeated autumn hour gets that hour's price twice; a
    `P1D` row contributes the same price to every hour.
- **Subscription outputs:**
  - One row per validity period overlapping `[start, end)`, with `start` and `end`
    clipped to the request, and a `subscription` column in DKK/month.
  - A stretch with no row is a row with NaN.

### How it connects to the rest of the repo

It uses `EnergiDataServiceClient` and the period helpers (`resolve_period`,
`period_index`), and, where they fit, the private market path or its pieces. It touches
none of the existing functions. Branch: `feat/energidataservice-tariffs`, cut from
`feat/energidataservice-markets` (open as PR #4), so the markets code is available.

### Explicitly out of scope

- Discount rows ("Rabat …").
- Every tariff and subscription variant other than the standard C consumption one: "time"
  (hourly metered) where a plain one exists, Flex, above 100 MWh, production/feed-in
  (indfødning), availability (rådighed), and A/B-customer tariffs.
- One-off fees (`D02`).
- The reduced (electric-heating) elafgift. It is not published in *DatahubPricelist*.
- VAT.
- Energinet's other charges: Nettab, Balancetarif, Effektabonnement, and the
  large-consumer system tariff.

Assumptions:

- `ValidTo` is exclusive (a row valid 2026-01-01 → 2027-01-01 covers all of 2026), and a
  missing `ValidTo` means "until further notice". Step 2 confirms both against the data.
- Where two rows of the same code overlap in time, the one with the later `ValidFrom`
  wins. Step 2 checks whether this happens.
- Friendly DSO names are lowercase, built from the company name, with the grid-area
  number where one company runs several areas: `n1-131`, `n1-344`, `konstant-151`.

### Acceptance criteria

| # | The finished feature... |
|---|---|
| A1 | `get_dso_tariffs(dso, start, end=None)` returns a long DataFrame with tz-aware `Europe/Copenhagen` `start`/`end` columns, one half-open row per hour of `[start, end)` in order, and a float `tariff` column in DKK/kWh excl. VAT. Each hour takes the price-list row valid on its local date: a `PT1H` row's `Price{n}` applies to local hour n−1, and a `P1D` row's price applies to every hour. DST days give 23 or 25 rows, with the repeated autumn hour priced as its wall-clock hour. The `start`/`end` rules are the same as the market functions, at hourly resolution. |
| A2 | `dso` is a friendly name from a public mapping, also listed in the README. It resolves to the DSO's GLN and its standard C tariff and C subscription codes. Every DSO in the price list that publishes a C-customer tariff is supported. An unknown name raises `ValueError` listing the valid names. |
| A3 | `get_energinet_tariffs(start, end=None)` has the same hourly shape, with `system_tariff` (code 41000) and `transmission_tariff` (code 40000) columns. |
| A4 | `get_dso_subscriptions(dso, start, end=None)` and `get_energinet_subscriptions(start, end=None)` (code 41004) return one row per validity period overlapping `[start, end)`. Each row's `start`/`end` is clipped to the request, and a `subscription` column holds DKK/month. |
| A5 | `get_electricity_tax(start, end=None)` has the same hourly shape, with an `electricity_tax` column in DKK/kWh at the normal rate (code EA-001). Its docstring says the reduced electric-heating rate is not published in this dataset. |
| A6 | The full requested period is always returned. An hour with no valid price row is NaN; a stretch with no subscription row is a row with NaN. |
| A7 | Every call goes through `EnergiDataServiceClient` and works inside a running event loop. Tests run on real price-list records committed as a fixture, without the network. The README rows and docstrings give the dataset, charge codes, currency, unit, resolution, format and the supported DSOs. |

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

