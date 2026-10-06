---
paths:
  - "src/energydata/energidataservice/**"
  - "tests/test_energidataservice_client.py"
  - "tests/test_day_ahead.py"
---

# Structure: `src/energydata/energidataservice/`

Energi Data Service endpoints. Every call to the service goes through
`EnergiDataServiceClient`. `__init__.py` re-exports `EnergiDataServiceClient`,
`EnergiDataServiceError`, `Record` and `get_day_ahead_prices`. Every module has a `main()`
showcase; the ones here call the live API.

## `src/energydata/energidataservice/client.py`

| Signature | Description |
|---|---|
| `BASE_URL: str` | `"https://api.energidataservice.dk"`. |
| `type Record = dict[str, JsonValue]` | One dataset row. |
| `EnergiDataServiceError(Exception)` | Payload that is not a JSON object, has no `records` list, has a record that is not a JSON object, or has fewer records than its `total`. |
| `EnergiDataServiceClient(*, policy: RetryPolicy \| None = None, timeout: float = DEFAULT_TIMEOUT, max_concurrency: int = 4, max_span: timedelta = timedelta(days=31), transport: httpx.AsyncBaseTransport \| None = None)` | `ApiClient` on `BASE_URL`. Non-positive `max_span` or `max_concurrency < 1` is a `ValueError`. |
| `async EnergiDataServiceClient.fetch_dataset(dataset: str, start: datetime, end: datetime, *, filters: Mapping[str, Sequence[str]] \| None = None, columns: Sequence[str] \| None = None, sort_by: str \| None = None) -> list[Record]` | `GET /dataset/{dataset}` per `max_span` window, gathered concurrently; params `start`/`end` (UTC `YYYY-MM-DDTHH:MM`), `timezone=UTC`, `limit=0`, `filter` (compact JSON), `columns`, `sort`. Records concatenated in window order. Only awaitable on the client's loop. |
| `EnergiDataServiceClient.get_dataset(dataset: str, start: datetime, end: datetime, *, filters=None, columns=None, sort_by=None) -> list[Record]` | The sync form, through `run`. |
| `main() -> None` | Showcase (live API). |

## `src/energydata/energidataservice/day_ahead.py`

| Signature | Description |
|---|---|
| `get_day_ahead_prices(start: TimeLike, end: TimeLike \| None = None, bidding_zones: BiddingZone \| Sequence[BiddingZone] = BIDDING_ZONES, *, client: EnergiDataServiceClient \| None = None) -> pd.DataFrame` | EUR/MWh excl. VAT, 15-minute, wide, tz-aware Copenhagen index over exactly `[start, end)`, one column per zone. Fetches *DayAheadPrices* and *Elspotprices* concurrently; hourly history is expanded to quarter-hours and the 15-minute value wins; unpublished slots are NaN. Without `client` it creates and closes one. |
| `main() -> None` | Showcase (live API). |

Module constants name the datasets and fields (`QUARTER_DATASET`, `QUARTER_TIME`,
`QUARTER_VALUE`, `HOURLY_DATASET`, `HOURLY_TIME`, `HOURLY_VALUE`, `AREA`).

## Tests

Written at step 5, none touching the network: `tests/test_energidataservice_client.py`
and `tests/test_day_ahead.py`, both on `httpx.MockTransport`.
