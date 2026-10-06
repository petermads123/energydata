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
| `STRUCTURE.md`, `.claude/rules/structure-utils.md`, `.claude/rules/structure-energidataservice.md` | changed | New modules (full paths, `_markets.py` included), signatures and test files. |

### Public API

Common argument types come from PR #3: `TimeLike`, `BiddingZone`, `BIDDING_ZONES`,
`EnergiDataServiceClient`.

| Signature | Module | Purpose | Covers |
|---|---|---|---|
| `block_index(start: pd.Timestamp, end: pd.Timestamp, hours: int) -> pd.DatetimeIndex` | `utils.frames` | Every block start in `[start, end)`: the local wall-clock times in `start`'s zone whose hour is a multiple of `hours`, at minute 0. A DST change makes the block that contains it shorter or longer, but never moves a start. The result is `datetime64[ns, tz]`, named `"time"`. Raises `ValueError` naming the value for: a naive bound; `start >= end`; `hours` not in `{1, 2, 3, 4, 6, 8, 12, 24}`; or a bound that is not a block start. | A2 |
| `combine_levels(parts: Mapping[str, pd.DataFrame], index: pd.DatetimeIndex, outer: Sequence[str]) -> pd.DataFrame` | `utils.frames` | Each part is a wide frame whose columns are drawn from `outer`. Each part is conformed to `index` and `outer` with NaN padding, and the result has MultiIndex columns `(outer, part key)`. Ordering is `outer` first, then `parts`' order, so `outer=["DK1","DK2"]` with parts `up` and `down` gives `DK1/up, DK1/down, DK2/up, DK2/down`. The values are float64. Empty `parts` or empty `outer` raise `ValueError`. | A3, A6 |
| `get_imbalance_prices(start: TimeLike, end: TimeLike \| None = None, bidding_zones: BiddingZone \| Sequence[BiddingZone] = BIDDING_ZONES, *, client: EnergiDataServiceClient \| None = None) -> pd.DataFrame` | `energidataservice.balancing` | Reads *ImbalancePrice* (from 2025-03-04) at 15 minutes. Columns are `(zone, up/down)`, and both directions hold `ImbalancePriceEUR`. Values are in EUR/MWh. | A1–A3, A5, A7, A8 |
| `get_afrr_energy_prices(start: TimeLike, end: TimeLike \| None = None, bidding_zones: BiddingZone \| Sequence[BiddingZone] = BIDDING_ZONES, *, client: EnergiDataServiceClient \| None = None) -> pd.DataFrame` | `energidataservice.balancing` | Reads *ImbalancePrice* at 15 minutes. `up` is `aFRRVWAUpEUR` and `down` is `aFRRVWADownEUR`. Values are in EUR/MWh. | A1–A3, A5, A7, A8 |
| `get_mfrr_energy_prices(start: TimeLike, end: TimeLike \| None = None, bidding_zones: BiddingZone \| Sequence[BiddingZone] = BIDDING_ZONES, *, client: EnergiDataServiceClient \| None = None) -> pd.DataFrame` | `energidataservice.balancing` | Reads *MfrrEnergyActivationMarket* (from 2025-03-04) at 15 minutes. `up` is `mFRRSAUpEUR` and `down` is `mFRRSADownEUR`. Values are in EUR/MWh. | A1–A3, A5, A7, A8 |
| `get_mfrr_capacity_prices(start: TimeLike, end: TimeLike \| None = None, bidding_zones: BiddingZone \| Sequence[BiddingZone] = BIDDING_ZONES, *, include_volumes: bool = False, client: EnergiDataServiceClient \| None = None) -> pd.DataFrame` | `energidataservice.reserves` | Reads *MfrrCapacityMarket* (from 2023-06-21), hourly. `up` is `UpPriceEUR` and `down` is `DownPriceEUR`, in EUR/MW/h. With volumes, `up_demand`, `up_procured`, `down_demand` and `down_procured` are added, in MW. | A1–A3, A5–A8 |
| `get_afrr_capacity_prices(start: TimeLike, end: TimeLike \| None = None, bidding_zones: BiddingZone \| Sequence[BiddingZone] = BIDDING_ZONES, *, include_volumes: bool = False, client: EnergiDataServiceClient \| None = None) -> pd.DataFrame` | `energidataservice.reserves` | Reads *AfrrReservesNordic* (from 2022-12-08), hourly, with the same fields and columns as mFRR capacity. The request filters to the selected DK zones, so Nordic rows never arrive. | A1–A3, A5–A8 |
| `get_fcr_n_prices(start: TimeLike, end: TimeLike \| None = None, *, include_volumes: bool = False, client: EnergiDataServiceClient \| None = None) -> pd.DataFrame` | `energidataservice.reserves` | Reads *FcrNdDK2* (from 2021-11-10), hourly, with the filter `ProductName = "FCR-N"`, `AuctionType = "Total"`. Column `price` is `PriceTotalEUR`, in EUR/MW/h. With volumes, `purchased_local` and `purchased_total` are added, in MW. | A1, A2, A4–A8 |
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
   - `extra_filters`: a mapping, for FCR's `ProductName` and `AuctionType`;
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
     `combine_levels`. Unzoned markets set a constant key and take the single column, then
     `conform` it to the index with the output names.

   `_get_blocks(...)` does the same for FCR DK1:
   - It fetches hourly data over the block period.
   - It handles a lone timestamp itself: it calls `resolve_period(..., resolution=1h)` and,
     when the span is one hour, extends `end` to `start + 4h`. `block_index` then rejects a
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
   then add the conftest fixture and transport factory, then the suites (intents below).
8. **Docs.** Update the README rows, `STRUCTURE.md`, and both rules files, with full
   paths.

### Test intents

| # | Must prove | Covers |
|---|---|---|
| T1 | `block_index`: 6 blocks on a normal day; 6 on both DST days, with starts at local 00/04/…/20 and the shift-containing block 3 h or 5 h long; half-open; `ns` unit, name `"time"`; every `ValueError` case (naive, order, bad `hours`, misaligned bound). `combine_levels`: outer-major order, NaN padding for missing parts or zones, float64, rejection of empty input. | A2, A3, A6 |
| T2 | On the fixture day 2026-09-15, each zone-split function returns the right shape: 96 × 4 for 15-minute markets and 24 × 4 for hourly. Columns are `(DK1, up), (DK1, down), (DK2, up), (DK2, down)`. Values equal the fixture records: imbalance DK1 23:45 = 171.04 in both directions; aFRR energy 179.52/86.09; mFRR energy 254.17/171.04; mFRR capacity DK1 23:00 = 2.01/0.11; aFRR capacity DK1 23:00 = 0.4/0.02. A zone subset and its order are respected. aFRR capacity never contains Nordic zones, even if the transport returns them. | A1–A3, A5 |
| T3 | The DK2-only functions return a `price` column equal to the fixture: FCR-D down 12:00 = 2.872822 (Total, not an auction); FCR-D up and FCR-N at fixture hours; FFR 23:00 = 23.0. FCR DK1 on the fixture day gives 6 rows at 00/04/…/20 with `cross_border` 18.22, 13.0, 34.79, 52.61, 49.2, 25.0 and `danish` 3.69, 11.62, 34.79, …. A lone block-start timestamp gives one row, a mid-block timestamp raises `ValueError`, and a lone date gives the day's blocks. | A2, A4, A5 |
| T4 | `include_volumes=True` adds exactly the documented columns in the documented order, with fixture values: mFRR capacity DK1 23:00 `up_demand` 319, `up_procured` 390; FCR DK1 00:00 `domestic` 5, `abroad` 24; FFR 23:00 `demand` 5.485, `purchased` 6.2. `False` adds none. | A6 |
| T5 | Period rules for one function per resolution (15 min, hourly, blocks): naive input is Danish time; a lone date gives a whole day (92/96/100 at 15 min, 23/24/25 hourly); a lone timestamp gives one slot; `end` is exclusive; a misaligned timestamp, `start >= end` and an unknown zone raise `ValueError` naming the value, with no request made. | A1, A2 |
| T6 | NaN behaviour: a period before the dataset starts returns a full all-NaN frame; a `None` price in a record (as in the probe's latest imbalance record) is NaN; a direction with no data for a zone is an all-NaN column; nothing is ever filled from a neighbour. | A3, A7 |
| T7 | Requests: each function asks for its own dataset with the exact `filter` (zones, plus FCR's product and `Total`), `columns` and `sort`. FCR-N/D never ask for other products. A passed client is left open, an own client is closed (also on HTTP 400). A call from inside a running event loop works. A long period is split into windows that run concurrently. | A7 |
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

### Risks

- **The fixture has 12 FcrNdDK2 hours** (the probe stopped at 500 records), and no DST day
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
  requests send no zone filter and pivot on a constant. If `records_to_wide` cannot take a
  constant column key, use a private one-column frame instead. Do not change
  `records_to_wide`'s signature.
- **Anything that would change section 1** halts the build: for example, a field the
  concept names turning out absent, or a market whose data is not at the agreed
  resolution.

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

