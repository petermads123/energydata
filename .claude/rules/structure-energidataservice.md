---
paths:
  - "src/energydata/energidataservice/**"
  - "tests/test_energidataservice_client.py"
  - "tests/test_day_ahead.py"
  - "tests/test_balancing.py"
  - "tests/test_reserves.py"
  - "tests/fixtures/**"
---

# Structure: `src/energydata/energidataservice/`

Energi Data Service endpoints. Every call to the service goes through
`EnergiDataServiceClient`. `__init__.py` re-exports `EnergiDataServiceClient`,
`EnergiDataServiceError`, `Record`, `get_day_ahead_prices` and the ten market price functions below. Every module has a `main()`
showcase; the ones here call the live API.

## `src/energydata/energidataservice/client.py`

| Signature | Description |
|---|---|
| `BASE_URL: str` | `"https://api.energidataservice.dk"`. |
| `type Record = dict[str, JsonValue]` | One dataset row. |
| `EnergiDataServiceError(Exception)` | Payload that is not a JSON object, has no `records` list, has a record that is not a JSON object, or has fewer records than its `total`. |
| `EnergiDataServiceClient(*, policy: RetryPolicy \| None = None, timeout: float = DEFAULT_TIMEOUT, max_concurrency: int = 4, max_span: timedelta = timedelta(days=31), transport: httpx.AsyncBaseTransport \| None = None)` | `ApiClient` on `BASE_URL`. A `max_span` that is not a positive whole number of minutes, or `max_concurrency < 1`, is a `ValueError`. |
| `async EnergiDataServiceClient.fetch_dataset(dataset: str, start: datetime, end: datetime, *, filters: Mapping[str, Sequence[str]] \| None = None, columns: Sequence[str] \| None = None, sort_by: str \| None = None) -> list[Record]` | `GET /dataset/{dataset}` per `max_span` window, gathered concurrently; params `start`/`end` (UTC `YYYY-MM-DDTHH:MM`), `timezone=UTC`, `limit=0`, `filter` (compact JSON), `columns`, `sort`. Records concatenated in window order. `ValueError` for a naive bound, a bound with seconds (the API takes whole minutes) or `start >= end`. Only awaitable on the client's loop. |
| `EnergiDataServiceClient.get_dataset(dataset: str, start: datetime, end: datetime, *, filters=None, columns=None, sort_by=None) -> list[Record]` | The sync form, through `run`. |
| `main() -> None` | Showcase (live API). |

## `src/energydata/energidataservice/day_ahead.py`

| Signature | Description |
|---|---|
| `get_day_ahead_prices(start: TimeLike, end: TimeLike \| None = None, bidding_zones: BiddingZone \| Sequence[BiddingZone] = BIDDING_ZONES, *, client: EnergiDataServiceClient \| None = None) -> pd.DataFrame` | EUR/MWh excl. VAT, 15-minute, wide, tz-aware Copenhagen index over exactly `[start, end)`, one column per zone. Slots before `SWITCH` (2025-10-01 00:00 Danish time) hold the *Elspotprices* hourly price repeated over four quarter-hours; slots from it on hold only *DayAheadPrices*, a null or missing value being NaN, never filled from hourly data. A dataset is requested only when the period overlaps its side; both run concurrently when both are needed. Unpublished slots are NaN. Without `client` it creates and closes one. |
| `main() -> None` | Showcase (live API). |

Module constants: `SWITCH` (the `pd.Timestamp` of the switch) and the dataset and field names (`QUARTER_DATASET`, `QUARTER_TIME`,
`QUARTER_VALUE`, `HOURLY_DATASET`, `HOURLY_TIME`, `HOURLY_VALUE`, `AREA`).

## `src/energydata/energidataservice/_markets.py`

Private; no public names. A frozen `_Market` dataclass (dataset, time field, output-name to source-field
`fields`, `resolution`, `zoned`, `area`, `extra_filters`, `volume_fields`, `block_hours`) and
`_get(market, start, end, bidding_zones, include_volumes, client)`, the one path every market
function uses: resolve the period, fetch through the client (own client closed, passed one left open),
pivot with `records_to_wide`, assemble with `combine_levels` (zoned) or per-field `conform` (unzoned),
and index by `block_index` for 4-hour FCR DK1 blocks. Has an offline `main()`.

## `src/energydata/energidataservice/balancing.py`

All 15-minute, EUR/MWh, columns `(zone, "up" | "down")`, from 2025-03-04. Each takes
`start: TimeLike, end: TimeLike | None = None, bidding_zones: BiddingZone | Sequence[BiddingZone] = BIDDING_ZONES, *, client: EnergiDataServiceClient | None = None` and returns `pd.DataFrame`.

| Signature | Description |
|---|---|
| `get_imbalance_prices(...)` | *ImbalancePrice* `ImbalancePriceEUR`, repeated in `up` and `down`. |
| `get_afrr_energy_prices(...)` | *ImbalancePrice* `aFRRVWAUpEUR` / `aFRRVWADownEUR`. |
| `get_mfrr_energy_prices(...)` | *MfrrEnergyActivationMarket* `mFRRSAUpEUR` / `mFRRSADownEUR`. |
| `main() -> None` | Showcase (live API). |

## `src/energydata/energidataservice/reserves.py`

Hourly capacity markets in EUR/MW/h; `include_volumes=True` appends volume columns in MW.

| Signature | Description |
|---|---|
| `get_mfrr_capacity_prices(start, end=None, bidding_zones=BIDDING_ZONES, *, include_volumes: bool = False, client=None) -> pd.DataFrame` | *MfrrCapacityMarket* (from 2023-06-21); `(zone, up/down)` plus `up_demand`, `up_procured`, `down_demand`, `down_procured`. |
| `get_afrr_capacity_prices(start, end=None, bidding_zones=BIDDING_ZONES, *, include_volumes: bool = False, client=None) -> pd.DataFrame` | *AfrrReservesNordic* (from 2022-12-08); same columns, request filtered to the DK zones. |
| `get_fcr_n_prices(start, end=None, *, include_volumes: bool = False, client=None) -> pd.DataFrame` | *FcrNdDK2* (from 2021-11-10), `PriceArea = DK2`, `FCR-N`, `Total`; `price`, then `purchased_local`, `purchased_total`. |
| `get_fcr_d_up_prices(...)` / `get_fcr_d_down_prices(...)` | Same with `ProductName` `FCR-D upp` / `FCR-D ned`. |
| `get_fcr_dk1_prices(start, end=None, *, include_volumes: bool = False, client=None) -> pd.DataFrame` | *FcrDK1* (from 2021-01-19), indexed by 4-hour block starts (00, 04, ..., 20 Danish time); `cross_border`, `danish`, then `domestic`, `abroad`. A lone timestamp must be a block start. |
| `get_ffr_prices(start, end=None, *, include_volumes: bool = False, client=None) -> pd.DataFrame` | *FfrDK2* (from 2021-04-26), hourly; `price`, then `demand`, `purchased`. |
| `main() -> None` | Showcase (live API). |

## Tests

Written at step 5, none touching the network: `tests/test_energidataservice_client.py`,
`tests/test_day_ahead.py`, `tests/test_balancing.py` and `tests/test_reserves.py`, all on
`httpx.MockTransport`; the market suites run on the real records in
`tests/fixtures/energidataservice_markets.json`.
