# Energi Data Service market price endpoints

<!-- claude-plan step=9 status=active -->

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
| 2 | Plan | `/plan` | with the user | done |
| 3 | Implement | `/implement` | in `/build` | done |
| 4 | Verify | `/verify` | in `/build` | done |
| 5 | Test | `/test` | in `/build` | done |
| 6 | Concept check | `/concept-check` | in `/build` | done |
| 7 | Ship | `/ship` | in `/build` | done |
| 8 | Recommend | `/recommend` | with the user | done |
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
| A3 | Imbalance, aFRR energy, mFRR energy, mFRR capacity and aFRR capacity take `bidding_zones` (`DK1`/`DK2`, both by default, order kept) and return columns with zone on top and `up`/`down` below: imbalance repeats its single price in both, aFRR energy is the volume-weighted up/down price and is NaN in a slot where that direction's activated aFRR MW is 0 (the API publishes 0.0 there), mFRR energy the SA marginal price as published; zones other than DK1/DK2 are dropped. Capacity prices are returned as published, 0.0 included; a direction or zone with no record or a null value is NaN. |
| A4 | FCR-N, FCR-D up, FCR-D down and FFR (DK2 only) take no zone argument and return a `price` column — for FCR the auctions' `Total`; FCR DK1 takes no zone argument and returns `cross_border` and `danish` columns. |
| A5 | Prices are in EUR: EUR/MWh for imbalance and the energy markets, EUR/MW/h for the capacity markets. |
| A6 | Capacity functions take `include_volumes=False`; when true, the dataset's volume fields are added as further columns in MW (demand and procured per direction for mFRR/aFRR; purchased local and total for FCR-N/D; domestic and abroad for FCR DK1; demand and purchased for FFR). |
| A7 | Unpublished slots and periods before a dataset starts are NaN in a full-shape frame; every call goes through `EnergiDataServiceClient`, concurrently where it needs several requests; every function works when called from inside a running event loop. |
| A8 | The README endpoints table and each docstring give the dataset, its start date, currency, unit, resolution, format and zones; dataset and field names come from the probe output, a trimmed copy of which is committed as test fixtures, and tests run on those real records without the network. |

### Open questions

None.

### Amendments

- **2026-10-06, before step 5 — user decisions.** The test designers found that the real
  data contradicts two readings of A3. (1) An aFRR direction with no activation is
  published as price 0.0 with 0 MW: the user chose NaN where the matching activated MW is
  0. (2) Capacity directions with nothing procured are published as 0.0, and DK1 mFRR down
  is in fact procured today, so "a direction a zone does not procure is a NaN column" no
  longer describes the data: the user chose to return capacity prices as published,
  NaN only where the API has no record or a null. A3 is rewritten accordingly.

---

## 2. Plan

### Approach

**One private fetch-and-shape path, ten thin public functions.** The ten functions differ
only in data: dataset, time field, filters, which fields become which columns, resolution,
and whether there is a zone level. So each is a short function that declares those facts
and calls one private helper module, `energidataservice/_markets.py`. The helper:

- resolves the period with `resolve_period`;
- builds the index with `period_index`, or with the new `block_index` for FCR DK1;
- fetches through `EnergiDataServiceClient.fetch_dataset` with `filters`, `columns` and
  `sort_by`, owning and closing a client when none is passed (the same pattern as
  `get_day_ahead_prices`);
- pivots each wanted field with `records_to_wide`;
- assembles the columns with the new `combine_levels`, which conforms each field to the
  index and builds `(zone, field)` MultiIndex columns.

Every request goes through the client's `run`, so the existing loop thread, the
concurrency cap and the retries (including the API's 429 holdoff) apply unchanged.

Two generic pieces go to `energydata.utils.frames`, because they name no source:

- `block_index`: local wall-clock blocks of N hours (FCR DK1's 4-hour blocks).
- `combine_levels`: per-field wide frames to MultiIndex columns.

The public functions sit in two modules by market family:

- `balancing.py`: the 15-minute imbalance and activation-energy prices.
- `reserves.py`: the hourly and block capacity markets.

Rejected:

- **One generic public function** such as `get_market_prices(market, ...)`. It is less
  code, but the spec names ten functions with different arguments (zone or no zone,
  volumes or not), and one signature would have to accept arguments that half the markets
  reject.
- **One module per function.** That gives ten near-empty files and makes `STRUCTURE.md`
  longer for nothing.
- **Copying day-ahead's fetch code into each function.** Ten copies of the
  owned-client/run/shape block would drift.

### Modules

| Path | New or changed | Purpose |
|---|---|---|
| `src/energydata/utils/frames.py` | changed | Adds `block_index` and `combine_levels`. |
| `src/energydata/utils/__init__.py` | changed | Re-exports both. |
| `src/energydata/energidataservice/_markets.py` | new | Private: the market spec, plus the shared fetch, shape and owned-client path. No public names, but listed in `STRUCTURE.md` because the stop gate matches every `.py` path. |
| `src/energydata/energidataservice/balancing.py` | new | `get_imbalance_prices`, `get_afrr_energy_prices`, `get_mfrr_energy_prices` (15 min, zone × up/down). |
| `src/energydata/energidataservice/reserves.py` | new | `get_mfrr_capacity_prices`, `get_afrr_capacity_prices`, `get_fcr_n_prices`, `get_fcr_d_up_prices`, `get_fcr_d_down_prices`, `get_fcr_dk1_prices`, `get_ffr_prices`. |
| `src/energydata/energidataservice/__init__.py` | changed | Re-exports the ten functions. |
| `tests/fixtures/energidataservice_markets.json` | new | The probe's sample and latest records for the seven datasets, copied from `development/feat/energidataservice-markets/eds_probe.json` (records only, no metadata). |
| `tests/conftest.py` | changed | Adds a `markets_records` fixture that loads that file, plus a factory that builds an `httpx.MockTransport` serving those records. It filters by the request's `start`/`end` (UTC), `filter` and `columns`, as the API would. |
| `tests/test_balancing.py`, `tests/test_reserves.py` | new | One suite per new public module. `tests/test_frames.py` is extended for the two new utils. |
| `README.md` | changed | Ten rows in the Energi Data Service endpoints table. |
| `DEVELOPMENT.md` | changed | Narrow the "Remaining Energi Data Service endpoints are next" entry to tariffs, subscriptions and elafgift. |
| `STRUCTURE.md`, `.claude/rules/structure-utils.md`, `.claude/rules/structure-energidataservice.md` | changed | New modules (full paths, `_markets.py` included), signatures and test files. |

### Public API

Common argument types come from PR #3: `TimeLike`, `BiddingZone`, `BIDDING_ZONES`,
`EnergiDataServiceClient`.

| Signature | Module | Purpose | Covers |
|---|---|---|---|
| `block_index(start: pd.Timestamp, end: pd.Timestamp, hours: int) -> pd.DatetimeIndex` | `utils.frames` | Every block start in `[start, end)`: the local wall-clock times in `start`'s zone whose hour is a multiple of `hours`, at minute 0. A DST change makes the block that contains it shorter or longer, but never moves a start; a wall-clock start that occurs twice (the repeated autumn hour) is kept once, the earlier. The result is `datetime64[ns, tz]`, named `"time"`. Raises `ValueError` naming the value for: a naive bound; `start >= end`; `hours` not a positive divisor of 24; or a bound that is not a block start. | A2 |
| `combine_levels(parts: Mapping[str, pd.DataFrame], index: pd.DatetimeIndex, outer: Sequence[str]) -> pd.DataFrame` | `utils.frames` | Each part is a wide frame whose columns are drawn from `outer`. Each part is conformed to `index` and `outer` with NaN padding, and the result has MultiIndex columns `(outer, part key)`. Ordering is `outer` first, then `parts`' order, so `outer=["DK1","DK2"]` with parts `up` and `down` gives `DK1/up, DK1/down, DK2/up, DK2/down`. The values are float64. Empty `parts` or empty `outer` raise `ValueError`. | A3, A6 |
| `get_imbalance_prices(start: TimeLike, end: TimeLike \| None = None, bidding_zones: BiddingZone \| Sequence[BiddingZone] = BIDDING_ZONES, *, client: EnergiDataServiceClient \| None = None) -> pd.DataFrame` | `energidataservice.balancing` | Reads *ImbalancePrice* (from 2025-03-04) at 15 minutes. Columns are `(zone, up/down)`, and both directions hold `ImbalancePriceEUR`. Values are in EUR/MWh. | A1–A3, A5, A7, A8 |
| `get_afrr_energy_prices(start: TimeLike, end: TimeLike \| None = None, bidding_zones: BiddingZone \| Sequence[BiddingZone] = BIDDING_ZONES, *, client: EnergiDataServiceClient \| None = None) -> pd.DataFrame` | `energidataservice.balancing` | Reads *ImbalancePrice* at 15 minutes. `up` is `aFRRVWAUpEUR` and `down` is `aFRRVWADownEUR`. Values are in EUR/MWh. | A1–A3, A5, A7, A8 |
| `get_mfrr_energy_prices(start: TimeLike, end: TimeLike \| None = None, bidding_zones: BiddingZone \| Sequence[BiddingZone] = BIDDING_ZONES, *, client: EnergiDataServiceClient \| None = None) -> pd.DataFrame` | `energidataservice.balancing` | Reads *MfrrEnergyActivationMarket* (from 2025-03-04) at 15 minutes. `up` is `mFRRSAUpEUR` and `down` is `mFRRSADownEUR`. Values are in EUR/MWh. | A1–A3, A5, A7, A8 |
| `get_mfrr_capacity_prices(start: TimeLike, end: TimeLike \| None = None, bidding_zones: BiddingZone \| Sequence[BiddingZone] = BIDDING_ZONES, *, include_volumes: bool = False, client: EnergiDataServiceClient \| None = None) -> pd.DataFrame` | `energidataservice.reserves` | Reads *MfrrCapacityMarket* (from 2023-06-21), hourly. `up` is `UpPriceEUR` and `down` is `DownPriceEUR`, in EUR/MW/h. With volumes, `up_demand`, `up_procured`, `down_demand` and `down_procured` are added, in MW. | A1–A3, A5–A8 |
| `get_afrr_capacity_prices(start: TimeLike, end: TimeLike \| None = None, bidding_zones: BiddingZone \| Sequence[BiddingZone] = BIDDING_ZONES, *, include_volumes: bool = False, client: EnergiDataServiceClient \| None = None) -> pd.DataFrame` | `energidataservice.reserves` | Reads *AfrrReservesNordic* (from 2022-12-08), hourly, with the same fields and columns as mFRR capacity. The request filters to the selected DK zones, so Nordic rows never arrive. | A1–A3, A5–A8 |
| `get_fcr_n_prices(start: TimeLike, end: TimeLike \| None = None, *, include_volumes: bool = False, client: EnergiDataServiceClient \| None = None) -> pd.DataFrame` | `energidataservice.reserves` | Reads *FcrNdDK2* (from 2021-11-10), hourly, with the filter `PriceArea = "DK2"`, `ProductName = "FCR-N"`, `AuctionType = "Total"` (the dataset also carries SE1–SE4 rows with the same price but Swedish volumes). Column `price` is `PriceTotalEUR`, in EUR/MW/h. With volumes, `purchased_local` and `purchased_total` are added, in MW. | A1, A2, A4–A8 |
| `get_fcr_d_up_prices(start: TimeLike, end: TimeLike \| None = None, *, include_volumes: bool = False, client: EnergiDataServiceClient \| None = None) -> pd.DataFrame` | `energidataservice.reserves` | The same as `get_fcr_n_prices`, with `ProductName = "FCR-D upp"`. | A1, A2, A4–A8 |
| `get_fcr_d_down_prices(start: TimeLike, end: TimeLike \| None = None, *, include_volumes: bool = False, client: EnergiDataServiceClient \| None = None) -> pd.DataFrame` | `energidataservice.reserves` | The same, with `ProductName = "FCR-D ned"`. | A1, A2, A4–A8 |
| `get_fcr_dk1_prices(start: TimeLike, end: TimeLike \| None = None, *, include_volumes: bool = False, client: EnergiDataServiceClient \| None = None) -> pd.DataFrame` | `energidataservice.reserves` | Reads *FcrDK1* (from 2021-01-19). The index is the 4-hour block starts (`block_index(..., 4)`), and each block takes the value of its first hour's record. Columns are `cross_border` (`FCRcross_EUR`) and `danish` (`FCRdk_EUR`), in EUR/MW/h. With volumes, `domestic` and `abroad` are added, in MW. A lone date gives that day's blocks. A lone timestamp gives one block and must be a block start. | A1, A2, A4–A8 |
| `get_ffr_prices(start: TimeLike, end: TimeLike \| None = None, *, include_volumes: bool = False, client: EnergiDataServiceClient \| None = None) -> pd.DataFrame` | `energidataservice.reserves` | Reads *FfrDK2* (from 2021-04-26), hourly. Column `price` is `FFR_PriceEUR`, in EUR/MW/h. With volumes, `demand` and `purchased` are added, in MW. | A1, A2, A4–A8 |
| `main() -> None` in `balancing.py`, `reserves.py` and `frames.py` | — | Showcases: the market ones call the live API, and the `frames` one stays offline. | — |

Shared behaviour for all ten functions:
- **Docstrings:** each has a property table like `get_day_ahead_prices`, giving currency and unit, resolution, format, zones, source dataset with its fields, and the date the data starts. They also list `Raises:` (`ValueError`, `EnergiDataServiceError`, `httpx.HTTPStatusError`, and `RuntimeError` for a closed passed client).
- **Client ownership:** without a `client`, the function creates one and closes it; a passed client is left open.
- **Period:** a period before the dataset's start returns all NaN; the request is still made and comes back empty.
- **Columns:** the zone-split functions have two column levels. The DK2-only functions and FCR DK1 have one level, in the documented order: price columns first, then volumes.

### Implementation guide

1. **`utils/frames.py` → `block_index`.** Validate the arguments. Walk from `start` in
   whole local hours, using `pd.date_range(start, end, freq="h", inclusive="left")`, and
   keep the stamps whose `.hour % hours == 0` and `.minute == 0`. A bound that is not a
   block start raises. Apply `.as_unit("ns")` and name the index `"time"`.
2. **`utils/frames.py` → `combine_levels`.** For each part, call
   `conform(part, index, outer)`. Concatenate them with `pd.concat(..., axis=1, keys=…)`,
   then reorder the columns into outer-major order with `pd.MultiIndex.from_product`
   (`outer` × part keys) and `reindex(columns=…)`. Re-export it in `utils/__init__.py`.
3. **`energidataservice/_markets.py`.** Define a frozen private `_Market` dataclass:
   - `dataset`, `time_field`;
   - `fields`: a mapping from output column name to source field, in order;
   - `resolution`;
   - `zoned: bool`;
   - `extra_filters`: a mapping, for FCR-N/D's `PriceArea = ["DK2"]`, `ProductName` and `AuctionType = ["Total"]`;
   - `volume_fields`: a mapping from output name to source field.

   Then add two private functions.

   `_get(market, start, end, zones, include_volumes, client)`:
   - resolve the period at `market.resolution`;
   - build the index;
   - pick the fields, adding volumes when asked;
   - fetch inside `client.run` with `filters` (`PriceArea` for zoned markets, plus the
     extra filters), `columns` (the time field, `PriceArea` when zoned, and the fields) and
     `sort_by`;
   - pivot each field. Zoned markets use `records_to_wide(column="PriceArea")` and
     `combine_levels`. Unzoned markets pivot through `records_to_wide` too, so its
     parsing and duplicate check are reused: FCR-N/D with `column="PriceArea"`, taking
     `"DK2"`; FcrDK1 and FfrDK2 (no `PriceArea` field) by passing
     `[{**r, "_key": "v"} for r in records]` with `column="_key"`. Each field's single
     column is renamed to its output name and `conform`ed to the index.

   `_get_blocks(...)` does the same for FCR DK1:
   - It fetches hourly data over the block period.
   - It handles a lone timestamp itself: only when `end is None` and
     `resolve_period(..., resolution=1h)` returns a one-hour span does it move `end` to the
     next block start in wall-clock time,
     `(first.tz_localize(None) + pd.Timedelta(hours=4)).tz_localize(first.tz)` (safe,
     because 04/08/… never fall in a DST hour). An explicit `end` is never widened, so a
     one-hour explicit period is rejected by `block_index` as a misaligned bound, as is a
     start that is not on a block.
   - It conforms the hourly frame to `block_index(first, last, 4)`, which picks each
     block's first hour.

   Reuse the owned-client pattern from `day_ahead.py` verbatim.
4. **`balancing.py`.** Write three `_Market` constants and three functions that call
   `_get`. Imbalance maps both `up` and `down` to `ImbalancePriceEUR`, so the field is
   requested once.
5. **`reserves.py`.** Write the remaining `_Market` constants and seven functions. The
   showcase fetches one recent day for two markets, one with volumes.
6. **`energidataservice/__init__.py`.** Re-export the ten functions.
7. **Tests.** Copy the fixture records into `tests/fixtures/energidataservice_markets.json`,
   then add the conftest fixture and transport factory, then the suites (intents below). The factory is new rather than `test_day_ahead.py`'s `Service`, because it must also filter on `PriceArea`/`ProductName`/`AuctionType` and serve seven datasets; `Service` stays as is.
8. **Docs.** Update the README rows, `STRUCTURE.md`, and both rules files, with full
   paths.

### Test intents

| # | Must prove | Covers |
|---|---|---|
| T1 | `block_index`: 6 blocks on a normal day; 6 on both DST days, with starts at local 00/04/…/20 and the shift-containing block 3 h or 5 h long; `hours=2` on the autumn day keeps the repeated 02:00 once; half-open; `ns` unit, name `"time"`; every `ValueError` case (naive, order, bad `hours`, misaligned bound). `combine_levels`: outer-major order, NaN padding for missing parts or zones, float64, rejection of empty input. | A2, A3, A6 |
| T2 | On the fixture day 2026-09-15, each zone-split function returns the right shape: 96 × 4 for 15-minute markets and 24 × 4 for hourly. Columns are `(DK1, up), (DK1, down), (DK2, up), (DK2, down)`. Values equal the fixture records: imbalance DK1 23:45 = 171.04 in both directions; aFRR energy 179.52/86.09; mFRR energy 254.17/171.04; mFRR capacity DK1 23:00 = 2.01/0.11; aFRR capacity DK1 23:00 = 0.4/0.02. A zone subset and its order are respected. aFRR capacity never contains Nordic zones, even if the transport returns them. | A1–A3, A5 |
| T3 | The DK2-only functions return a `price` column equal to the fixture: FCR-D down 12:00 = 2.872822 (Total, not an auction); FCR-D up and FCR-N at fixture hours; FFR 23:00 = 23.0. FCR DK1 on the fixture day gives 6 rows at 00/04/…/20 with `cross_border` 18.22, 13.0, 34.79, 52.61, 49.2, 25.0 and `danish` 3.69, 11.62, 34.79, …. A lone block-start timestamp gives one row (also `00:00` on 2026-03-29 and 2026-10-25), a mid-block timestamp raises `ValueError`, an explicit one-hour `end` raises, and a lone date gives the day's blocks. FCR-N/D take only DK2 rows: SE rows the transport serves never reach the frame, and FCR-N/FCR-D up are asserted at fixture hours 13:00–23:00 (12:00 lacks their Total). | A2, A4, A5 |
| T4 | `include_volumes=True` adds exactly the documented columns in the documented order, with fixture values: mFRR capacity DK1 23:00 `up_demand` 319, `up_procured` 390; FCR DK1 00:00 `domestic` 5, `abroad` 24; FFR 23:00 `demand` 5.485, `purchased` 6.2. `False` adds none. | A6 |
| T5 | Period rules for one function per resolution (15 min, hourly, blocks): naive input is Danish time; a lone date gives a whole day (92/96/100 at 15 min, 23/24/25 hourly); a lone timestamp gives one slot; `end` is exclusive; a misaligned timestamp, `start >= end` and an unknown zone raise `ValueError` naming the value, with no request made. | A1, A2 |
| T6 | NaN behaviour: a period before the dataset starts returns a full all-NaN frame; a `None` price in a record (as in the probe's latest imbalance record) is NaN; a direction with no data for a zone is an all-NaN column; nothing is ever filled from a neighbour. | A3, A7 |
| T7 | Requests: each function asks for its own dataset with the exact `filter` (zones, plus FCR's product and `Total`), `columns` and `sort`. FCR-N/D never ask for other products or areas. A passed client is left open, an own client is closed (also on HTTP 400). A call from inside a running event loop works. A long period is split into windows that run concurrently. FCR-N/D filter `PriceArea` to `DK2`. A closed passed client raises `RuntimeError`; a payload without `records` raises `EnergiDataServiceError` and an own client is still closed. | A7 |
| T8 | README rows and docstrings name the dataset, start date, currency and unit, resolution, format and zones for all ten functions. The existing `utils` source-scan test still passes with the new `frames` code, which names no Energi Data Service specifics. The fixture is used and the no-network guard holds. | A8 |

### Coverage check

- **Every criterion has an API entry:**
  - A1: the ten functions.
  - A2: `block_index`, resolutions.
  - A3: the zone-split functions, `combine_levels`.
  - A4: the DK2 functions, FCR DK1.
  - A5: all ten (units in the docstrings).
  - A6: `include_volumes` on the seven capacity functions, `combine_levels`.
  - A7: the shared `_get` path through the client.
  - A8: the docstrings, README and fixtures.
- **Every criterion has a test intent:** A1: T2, T5. A2: T1, T3, T5. A3: T1, T2, T6.
  A4: T3. A5: T2, T3. A6: T1, T4. A7: T6, T7. A8: T8.
- **Every API entry traces to a criterion:** `block_index` and `combine_levels` trace to
  A2, A3 and A6. The showcases follow the module convention.

### Critique

Read by the `plan-critic` (verdict: accept with changes). All applied:

1. FcrNdDK2 also carries SE1–SE4 rows (same price, Swedish volumes) → FCR-N/D filter
   `PriceArea = DK2` (API rows, guide 3, T3, T7).
2. FCR DK1 lone-timestamp rule broke on DST days and widened an explicit `end` → only
   when `end is None`, end = next block in wall-clock time; explicit one-hour end raises
   (guide 3, T3).
3. The unzoned pivot was left open → `records_to_wide` with an injected constant key, or
   `PriceArea = DK2` for FCR-N/D (guide 3, Risks).
4. `block_index` with `hours=2` duplicated the repeated autumn hour → any divisor of 24;
   a repeated wall-clock start is kept once (API, T1).
5. `RuntimeError` / `EnergiDataServiceError` lacked intents → added to T7.
6. The `DEVELOPMENT.md` entry this branch resolves → narrowed in the same branch
   (Modules).

Minor, applied: the 12:00 FCR fixture gap (Risks, T3) and why the conftest transport is
new rather than `Service` (guide 7).

### Risks

- **The fixture has 12 FcrNdDK2 hours** (the probe stopped at 500 records; 12:00 lacks the FCR-N and FCR-D up `Total` rows, so assert those at 13:00–23:00), and no DST day
  for any dataset. Tests for DST days and for hours outside the fixture build synthetic
  records in the same shape. This is not a halt.
- **The fixture's `TimeUTC`/`HourUTC` values are naive UTC strings**, which
  `records_to_wide` already reads as UTC. The mock transport must filter by the request's
  UTC `start`/`end` exactly as the client sends them. If a test shows the client's window
  bounds and the fixture disagree on exclusivity, fix the mock, not the client: the
  client's behaviour is from the previous round.
- **FCR DK1 on a DST day.** The probe shows only a normal day. If the block grid there
  turns out to differ from local 00/04/…/20 (it cannot be checked offline), the code keeps
  the agreed grid and `DEVELOPMENT.md` gets a note to verify on a real DST day. This is
  not a halt.
- **Rate limiting.** A burst of windows across ten functions may hit HTTP 429. The client
  already honours `Retry-After` up to 300 s. No change is planned; the showcase may be
  slow if it is rate-limited.
- **The unzoned fetch path.** FcrDK1 and FfrDK2 have no `PriceArea` field, so their
  requests send no zone filter and pivot on an injected constant key (guide 3). Do not
  change `records_to_wide`'s signature.
- **Anything that would change section 1** halts the build: for example, a field the
  concept names turning out absent, or a market whose data is not at the agreed
  resolution.

---

## 3. Implementation notes

- **One `_get`, not `_get` plus `_get_blocks`.** The two paths differed only in how the period
  and index are built, so `_Market.block_hours` selects `block_index` (and the lone-timestamp
  rule) inside a private `_period` helper and everything else is shared.
- **`_markets.py` has an offline `main()`** (period handling of the FCR DK1 market), since every
  module needs one.
- **Unzoned fetch:** FCR-N/D filter `PriceArea = DK2` and pivot on it; FcrDK1 and FfrDK2 inject a
  constant key, as planned.
- **Live checks (2026-10-06):** both showcases ran against the real API; values match the
  probe (imbalance DK1 23:45 = 171.04, aFRR 179.52/86.09, mFRR 254.17/171.04, mFRR capacity
  DK1 23:00 2.01/0.11, FCR DK1 blocks 18.22/13.0/34.79/52.61/49.2/25.0, FFR 23:00 23.0,
  FCR-D down 22:00 2.8728). FCR DK1 on 2026-03-29 (spring DST) gives six blocks at local
  00/04/.../20; a pre-start period gives an all-NaN full frame; a mid-block lone timestamp
  raises. The autumn DST day could not be checked live (rate limit).
- **DEVELOPMENT.md:** narrowed the endpoints entry to tariffs, subscriptions and elafgift, and
  removed "Live API constants unconfirmed" (from `feat/energidataservice-client`), resolved by
  the live day-ahead showcase run.
- **Left for step 5:** `tests/fixtures/energidataservice_markets.json`, the `conftest.py`
  fixture and transport factory, and all test files.
- **Step 5 changes to code** (from the amended A3 and test findings): aFRR energy requests
  `aFRRUpMW`/`aFRRDownMW` and masks a direction's price to NaN where its MW is 0
  (`_Market.activation_fields`, private); docstrings corrected (aFRR/mFRR energy, capacity
  "as published", FCR DK1 `end`/`Raises`); `block_index` now rejects a bound with
  microseconds and its docstring says a start inside the spring gap is absent;
  `combine_levels` now raises `ValueError` naming a repeated `outer` name. No public
  signature changed.

---

## 4. Verification log

| Check | Result |
|---|---|
| `ruff check .` | All checks passed! |
| `ruff format --check .` | 67 files already formatted |
| `mypy` | Success: no issues found in 35 source files |
| `pytest` | 1143 passed (existing suite; tests for the new code are step 5's) |
| Plan completeness | all 12 Public API rows exist with the planned signatures (checked with `inspect.signature` and against `block_index`/`combine_levels` definitions); no Missing, no Deviation beyond section 3, no Unplanned public surface (`AREA` in `_markets.py` is a module constant of a private module) |
| `STRUCTURE.md` | auditor's four items applied: `get_mfrr_capacity_prices` row now zone-major `(zone, field)`; `_markets.py` entry rewritten (no private names, names `AREA` and `main()`); Modules sentence wording fixed. The auditor's note on the `reserves.py` Returns docstrings (zone-major order) applied to both capacity functions. Test-file entries for step 5 deliberately not added yet (stop gate flags absent files) |
| `python -m energydata.utils.frames` | runs offline, shows 6 blocks and combined frame |
| `python -m energydata.energidataservice._markets` | runs offline, 6 blocks 00/04/.../20 |
| balancing / reserves showcases | ran live in step 3 and matched the probe; not re-run here (rate limit); form checked: named arguments, call on own line, result named, fixed-set values commented |

---|---|
| `ruff check .` | |
| `ruff format --check .` | |
| `mypy` | |
| Plan completeness | every signature in the Public API table exists as written |
| `STRUCTURE.md` | in sync |
| `python -m <package>.<module>` | |

---

## 5. Test log

Suites: `tests/test_frames.py` (extended, +66 cases), `tests/test_balancing.py` (107),
`tests/test_reserves.py` (183), on `tests/fixtures/energidataservice_markets.json` and the
`MarketsService` mock in `tests/conftest.py`. Whole suite: 1484 passed; ruff, ruff format and
mypy clean.

| Intent | Test names | Result |
|---|---|---|
| T1 `block_index` | `test_block_index_gives_six_wall_clock_blocks_on_every_kind_of_day`, `..._is_half_open_named_time_and_in_nanoseconds`, `..._returns_nanoseconds_for_a_coarse_unit_input`, `..._counts_blocks_across_dst_days`, `..._keeps_the_repeated_autumn_hour_once_at_the_earlier_stamp`, `..._skips_a_start_inside_the_spring_gap`, `..._start_on_either_repeated_hour_survives`, `..._rejects_a_bound_with_a_sub_second_part`, `..._rejects_bad_bounds`, `..._rejects_hours_that_do_not_divide_a_day`, `..._converts_end_to_the_start_zone_before_checking_it`, `..._is_idempotent_...` | pass |
| T1 `combine_levels` | `test_combine_levels_orders_outer_first_then_parts`, `..._keeps_given_order_and_never_sorts`, `..._pads_a_missing_zone_and_missing_slots_with_nan`, `..._drops_columns_that_are_not_in_outer`, `..._casts_to_float64_...`, `..._over_an_empty_index_...`, `..._gives_the_same_frame_under_two_keys_...`, `..._does_not_mutate_a_part_...`, `..._rejects_empty_parts_or_outer`, `..._rejects_a_part_with_a_duplicate_index_entry`, `..._rejects_a_repeated_outer_name_naming_it` | pass |
| T2 zone-split values and shape | `test_prices_on_the_fixture_day_equal_the_records`, `test_imbalance_repeats_its_single_price_...`, `test_capacity_prices_on_the_fixture_day_equal_the_records`, `test_capacity_volumes_are_zone_major_...`, `test_afrr_capacity_never_returns_a_nordic_zone_even_if_one_is_served`, `test_a_zone_subset_and_its_order_are_kept`, `test_capacity_zone_subset_and_order_are_kept`, `test_the_latest_capacity_record_has_a_zero_down_price_not_nan` | pass |
| T3 DK2-only and FCR DK1 | `test_dk2_prices_equal_the_fixture_totals`, `test_an_hour_without_a_total_row_is_nan_not_filled`, `test_fcr_takes_the_dk2_row_not_a_swedish_row_with_the_same_price`, `test_fcr_dk1_fixture_day_has_six_four_hour_blocks`, `test_fcr_dk1_lone_block_start_is_one_row_fetching_four_wall_clock_hours`, `test_fcr_dk1_rejects_a_bound_that_is_not_a_block_start_with_no_request`, `test_fcr_dk1_a_lone_date_is_a_day_and_a_lone_midnight_timestamp_is_a_block`, `test_fcr_dk1_partial_day_and_aware_start`, `test_fcr_dk1_dst_day_has_six_blocks_each_holding_its_first_hours_value`, `test_fcr_dk1_takes_the_first_hour_of_a_block_never_a_later_one` | pass |
| T4 volumes | `test_capacity_volumes_are_zone_major_in_the_documented_order`, `test_capacity_without_volumes_requests_and_returns_only_prices`, `test_the_volumes_flag_is_not_sticky`, `test_ffr_values_zero_and_spike_are_kept`, `test_fcr_dk1_volumes_and_the_exact_request`, `test_dk2_volumes_are_off_by_default` | pass |
| T5 period rules | `test_the_period_gives_every_quarter_hour_and_end_is_exclusive`, `test_a_naive_time_is_danish_time`, `test_a_bad_period_raises_naming_the_value_with_no_request` (balancing and reserves), `test_an_hourly_lone_date_has_the_length_of_the_local_day`, `test_an_hourly_market_rejects_a_quarter_hour_timestamp`, `test_an_hourly_lone_timestamp_is_one_slot_or_one_block`, `test_an_aware_time_in_the_repeated_hour_picks_one_quarter`, `test_a_dst_day_has_a_full_nan_shape`, `test_a_bad_zone_raises_before_any_request`, `test_capacity_bad_zones_raise_before_any_request` | pass |
| T6 NaN behaviour | `test_a_period_before_the_dataset_is_all_nan_and_still_requested` (both modules), `test_a_null_imbalance_price_is_nan_while_afrr_of_the_same_record_is_not`, `test_a_null_mfrr_price_is_nan`, `test_a_zone_without_records_is_an_all_nan_column_pair`, `test_nothing_is_filled_from_a_neighbour`, `test_a_null_value_is_nan_and_independent_of_the_other_fields`, `test_a_null_dk2_price_is_nan`, `test_an_empty_response_keeps_the_named_columns_and_the_full_index`; amended A3: `test_afrr_energy_is_nan_for_a_direction_with_zero_activated_volume`, `..._masks_each_direction_and_zone_on_its_own_volume`, `..._keeps_the_price_when_the_activated_volume_is_missing`, `..._keeps_a_zero_price_when_volume_was_activated`, `..._null_price_is_nan_whatever_the_volume`, `test_imbalance_price_is_never_masked_by_the_afrr_volumes`, `test_mfrr_energy_price_is_published_even_without_a_requested_volume`, `test_mfrr_energy_keeps_a_zero_price_as_published`, `test_capacity_prices_are_returned_as_published_zero_included` | pass |
| T7 requests and client | `test_imbalance_asks_once_for_its_one_price_field`, `test_afrr_energy_asks_for_prices_and_the_volumes_that_gate_them`, `test_mfrr_energy_asks_for_its_dataset_and_two_price_fields`, `test_capacity_asks_for_its_dataset_with_the_zone_filter_and_sort`, `test_fcr_asks_only_for_its_product_total_and_dk2`, `test_fcr_calls_send_the_same_filter_each_time`, `test_ffr_sends_no_zone_filter_and_asks_only_for_its_fields`, `test_dk2_functions_take_no_zone_argument`, `test_an_owned_client_is_created_and_closed`, `..._closed_when_the_service_refuses`, `test_a_payload_without_records_raises_and_still_closes_an_owned_client`, `test_a_passed_client_is_left_open_...`, `test_a_closed_passed_client_raises_without_a_request`, `test_a_call_works_inside_a_running_event_loop`, `test_windows_of_a_long_period_run_concurrently`, `test_a_period_is_split_into_windows_that_give_the_same_frame`, `test_records_outside_the_period_are_ignored`, `test_a_duplicate_record_raises_naming_it`, `test_fcr_trusts_the_server_filter_...` | pass |
| T8 fixtures, no network | every market test runs on the fixture through `MarketsService`; the autouse no-network guard stays on; the existing utils source-scan test still passes (README/docstring content is step 6's read) | pass |

Bugs the tests found, fixed in this step:

1. `block_index` accepted a bound with microseconds (checked `.second` and `.nanosecond`
   only). Fixed; covered by `..._rejects_a_bound_with_a_sub_second_part`.
2. `combine_levels` returned duplicate columns for a repeated `outer` name instead of
   refusing. Now `ValueError` naming it (a function that returns a plausible wrong answer
   is worse than one that raises).
3. aFRR energy returned 0.0 for a direction with no activation (the user decision on A3);
   now NaN via the activated-volume fields. Docstrings that claimed "not activated is NaN"
   for mFRR energy and capacity corrected to match the data (user decision).
4. Stale `get_fcr_dk1_prices` `end` and `Raises` text corrected to say 4-hour block.

Designer contradictions, on the record:

| # | Item | Disposition |
|---|---|---|
| in-1 / co-3 | `block_index` accepts microseconds | Applied: bug fixed (above). |
| in-2 / co-1 | aFRR "not activated is NaN" vs 0.0 | Settled by user decision 1: code masks on MW 0; tests above. |
| in-3 / co-2 | mFRR "not activated is NaN" | Settled by user decision 1: SA price as published; docstring fixed, test pins it. |
| in-4 | A3 "not procured is a NaN column" vs 0.0 | Settled by user decision 2: capacity as published; tests pin 0.0 and the null-only NaN. |
| in-5 | FCR products trusted to the server filter | Rebutted, pinned: `ProductName`/`AuctionType` are filtered server-side and never requested as columns; an unfiltered response raises `ValueError` (duplicate), never a wrong price. Test `test_fcr_trusts_the_server_filter_...` documents it. Area is also filtered client-side by pivoting on `PriceArea` (`..._not_a_swedish_row_...`). |
| in-6 / co-6 | stale FCR DK1 docstring | Applied: `end` and `Raises` reworded. |
| in-7 / co-4 | `block_index` and a start in the spring gap | Applied as wording: docstring says such a start is absent; test `..._skips_a_start_inside_the_spring_gap`. Behaviour kept (a nonexistent wall-clock start has no instant; `hours=4` unaffected). |
| in-8 / co-7 | `bidding_zones=None` means both zones in `_get` | Rebutted: private path, public signatures do not admit `None`; FCR DK1 and the DK2 markets pass `None` as "no zone level". Not exposed. |
| in-9 | hourly market silently drops off-grid records | Pinned, not changed: `test_capacity_drops_an_off_grid_record_without_error`; `conform` never fills or invents slots. Noted in the skipped list. |
| co-5 | service data faults raise `ValueError`, not `EnergiDataServiceError` | Rebutted: `EnergiDataServiceError` is the payload-shape error of the client; `records_to_wide` raises `ValueError` for duplicate/unparseable values as `get_day_ahead_prices` already does (previous round, merged). Tests pin the duplicate case. A uniform wrapping is a possible later round, not a defect here. |

Edge cases considered and deliberately skipped, with reasons:

- Text/non-ASCII inputs: the only text is the zone, owned and tested by `normalize_bidding_zones`.
- A live DST autumn day for FCR DK1: not checkable offline; synthetic hourly records prove the block grid (`..._dst_day_has_six_blocks_...`).
- Retry/429 behaviour: belongs to `ApiClient` and the client suites of the previous rounds.
- README and docstring wording (T8): read by step 6, no executable assertion added.
- A 15-minute capacity dataset (Nordic MTU change): off-grid rows are dropped and pinned; supporting them would be a new concept.

---

## 6. Concept check

| # | Criterion | Met | Evidence |
|---|---|---|---|
| A1 | Ten functions, tz-aware Copenhagen index over exactly `[start, end)`, day-ahead period rules | yes | All ten import from `energydata.energidataservice` (docstring dump). `test_the_period_gives_every_quarter_hour_and_end_is_exclusive`, `test_a_naive_time_is_danish_time`, `test_a_bad_period_raises_naming_the_value_with_no_request` (both modules), `test_an_hourly_lone_date_has_the_length_of_the_local_day`, `test_an_hourly_lone_timestamp_is_one_slot_or_one_block`. Live: imbalance 2026-09-15 gives 96 rows. |
| A2 | Resolutions: 15 min / hourly not filled / FCR DK1 4-hour blocks | yes | `test_fcr_dk1_fixture_day_has_six_four_hour_blocks`, `test_fcr_dk1_dst_day_has_six_blocks_each_holding_its_first_hours_value`, `block_index` DST tests. Live: aFRR capacity 2026-09-15 is 24 rows at freq `h`; FCR DK1 gives 6 blocks at 00/04/.../20; imbalance 96 rows. Hourly markets never fill (`test_nothing_is_filled_from_a_neighbour`). |
| A3 | Zone x up/down columns; imbalance repeated; aFRR NaN where activated MW is 0 (amended); mFRR SA as published; capacity as published, NaN only for no record or null; non-DK zones dropped | yes | `test_imbalance_repeats_its_single_price_...`, `test_afrr_energy_is_nan_for_a_direction_with_zero_activated_volume`, `..._masks_each_direction_and_zone_on_its_own_volume`, `test_mfrr_energy_keeps_a_zero_price_as_published`, `test_capacity_prices_are_returned_as_published_zero_included`, `test_the_latest_capacity_record_has_a_zero_down_price_not_nan`, `test_afrr_capacity_never_returns_a_nordic_zone_even_if_one_is_served`. Live 2026-09-15 DK1 aFRR energy: up NaN at 23:00/23:15 (no activation), down 100.91/66.46; mFRR capacity down 0.00 shown as published; imbalance and mFRR values match the probe (171.04, 254.17). |
| A4 | FCR-N, FCR-D up/down, FFR: `price` column, no zone argument; FCR DK1: `cross_border`/`danish` | yes | `test_dk2_functions_take_no_zone_argument`, `test_dk2_prices_equal_the_fixture_totals`, `test_fcr_takes_the_dk2_row_not_a_swedish_row_with_the_same_price`. Live showcase: FCR-D down `price` 22:00 = 2.872822 (Total), FCR DK1 columns `cross_border`/`danish`. |
| A5 | EUR; EUR/MWh energy, EUR/MW/h capacity | yes | Property tables in all ten docstrings and README Currency column (read, and printed by script); no FX code (`grep` of `src/energydata/energidataservice`). Values match EUR probe fields. |
| A6 | `include_volumes=False` default; volumes added in MW per market | yes | `test_capacity_volumes_are_zone_major_in_the_documented_order`, `test_capacity_without_volumes_requests_and_returns_only_prices`, `test_fcr_dk1_volumes_and_the_exact_request`, `test_ffr_values_zero_and_spike_are_kept`, `test_dk2_volumes_are_off_by_default`. Live: FCR-D down with volumes gives `price, purchased_local, purchased_total` (59.8 / 574.0). |
| A7 | NaN for unpublished and pre-start; through client, concurrent; works in a running loop | yes | `test_a_period_before_the_dataset_is_all_nan_and_still_requested`, `test_an_empty_response_keeps_the_named_columns_and_the_full_index`, `test_windows_of_a_long_period_run_concurrently`, `test_a_call_works_inside_a_running_event_loop`; `_markets._get` fetches only via `EnergiDataServiceClient.fetch_dataset` inside `client.run`. |
| A8 | README table and each docstring give dataset, start date, currency, unit, resolution, format, zones; names from probe; fixtures; tests offline | yes | README.md lines 37-46: ten rows each with currency/unit, resolution, format, zones, dataset and start date. All ten docstrings carry the property table with Currency and unit, Resolution, Format, Zones, Source dataset and Data from (printed and read in this step); dataset and field names match the live API (every showcase and call above returned data from these datasets) and `tests/fixtures/energidataservice_markets.json` (seven datasets). Tests use `MarketsService` with the autouse no-network guard. The documentation content itself has no executable assertion (T8 was skipped by step 5); A8 asks for the documentation to exist, not for a test of its wording, so this is judged met on the read, and the gap is recorded below. |

Drift found, and what was done about it:

- No code drift and no out-of-scope items (no tariffs, no DA mFRR, no FX, no change to `get_day_ahead_prices`; `git diff main --stat` touches only the planned files).
- Surface: exactly the ten functions plus `block_index` and `combine_levels`, as planned; `_markets.py` is private.
- Showcases: `balancing` and `reserves` ran live this step and read as worked examples (named inputs, one call, a named result).
- Gates: `pytest` 1484 passed (including `tests/test_day_ahead.py` and `tests/test_energidataservice_client.py`, 115 passed on their own), `ruff check` clean, `mypy` clean on 37 files.
- Structure auditor, three items, all applied: (1) `combine_levels` row in `.claude/rules/structure-utils.md` now lists the duplicate-index `ValueError`; (2) the `MarketsService` description in `.claude/rules/structure-energidataservice.md` now names `client(...)`, the 400 for unknown fields, and the request-reading helpers; (3) `tests/conftest.py` added to that file's `paths`. `STRUCTURE.md` needed no change.
- Gap, not drift: no test pins the docstring or README wording (T8). Recorded for step 8 as a possible low-priority note, not critical.

### Earlier rounds still hold

| Round | # | Criterion | Still met | Evidence |
|---|---|---|---|---|

---

## 7. Ship log

| Field | Value |
|---|---|
| Commits | 8d8dcc8 Concept; dd6ea61 WIP step 2; 4bc3601 Plan; 2d85265 Plan accepted; c2bb425 WIP step 3; 039fe24 Add market price endpoints; da0d134 Verify; ca434b0 Concept amended before step 5; ce846e4 WIP step 5; a6ded7e Test; 955a44c Concept check; plus the Ship commit |
| Pushed to | `origin/feat/energidataservice-markets` |

Whole-tree gates at ship: `ruff check` clean, `ruff format --check` clean (69 files), `mypy` clean (37 files), `pytest` 1484 passed. Every step from 1 left a commit. Diff review against `origin/main`: no stray files. `development/feat/energidataservice-markets/eds_probe.json` is the user's probe from step 1 that the test fixture was derived from; kept deliberately.

---

## 8. Recommendations

None.

---|---|---|---|---|
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

