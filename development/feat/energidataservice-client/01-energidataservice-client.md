# Energi Data Service client and day-ahead prices

<!-- claude-plan step=5 status=active -->

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
| 2 | Plan | `/plan` | with the user | done |
| 3 | Implement | `/implement` | in `/build` | done |
| 4 | Verify | `/verify` | in `/build` | done |
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

- Day-ahead uses a fixed switch instant, 2025-10-01 00:00 `Europe/Copenhagen`: hourly
  *Elspotprices* before it, 15-minute *DayAheadPrices* from it. Amended by the user during
  step 5 (see the Amendments note below); it replaces the earlier "15-minute wins on
  overlap, no switch date hardcoded" assumption.
- A lone timestamp not on a 15-minute boundary is a `ValueError`, not rounded.
- The development container cannot reach `api.energidataservice.dk` (proxy 403), so dataset
  and column names are taken from the API documentation and confirmed by the user running
  the showcase.

### Acceptance criteria

| # | The finished feature... |
|---|---|
| A1 | `get_day_ahead_prices(start, end=None, bidding_zones=...)` returns a DataFrame with a tz-aware `Europe/Copenhagen` 15-minute index covering exactly `[start, end)` and one column per selected zone (`DK1`, `DK2`; both by default; in the order given), values EUR/MWh floats. |
| A2 | Slots before the switch to 15-minute prices (2025-10-01 00:00 Danish time) hold the hourly *Elspotprices* price repeated at :00, :15, :30 and :45; slots from the switch on hold only the *DayAheadPrices* value, and a null or missing one is NaN, never filled from hourly data. A period spanning the switch combines the two, and each side's dataset is requested only when the period overlaps that side. |
| A3 | Naive input is read as Danish local time; a lone date returns that whole local day (92 / 96 / 100 rows on DST days); a lone timestamp returns one row; `end` is exclusive. `start >= end`, a misaligned lone timestamp, an unknown zone or an empty zone list raise `ValueError` naming the value. |
| A4 | Slots with no published data are present as NaN — e.g. tomorrow before publication, or a period entirely in the future returns an all-NaN frame of the right shape. |
| A5 | A source-agnostic `ApiClient` base in `energydata.utils` does the calling (base URL, retries via the existing wrapper, reusable and closable); the Energi Data Service client subclasses it, fetches a dataset by name with period, filter and columns, and returns every record, never a truncated page. Every Energi Data Service call goes through it. |
| A6 | When a call needs several requests (two datasets, or a period split into windows over a maximum span), they run concurrently up to a configurable cap; the public function is a plain sync call that also works when called from inside a running event loop (Jupyter). |
| A7 | Period resolution, zone normalisation and frame shaping live in `energydata.utils`, contain no Energi Data Service specifics, and `get_day_ahead_prices` uses them. |
| A8 | The README lists the endpoint with currency, unit, resolution, format, zones and source datasets, and the function's docstring says the same; tests make no network calls; `pandas` is the only new runtime dependency. |

### Open questions

None.

### Amendments

- **2026-10-06, during step 5 — user decision.** The day-ahead rule was "fetch both datasets
  over the whole period; where both have a value the 15-minute one wins". The user changed
  it: hourly prices apply only to the period when prices were hourly, repeated over the four
  quarter-hours; in the 15-minute period a missing price is NaN and never filled from
  hourly data ("no prices should be missing"). A2 and the assumption above are rewritten
  accordingly; section 2's guide 8, T7 and the Public API purpose of `get_day_ahead_prices`
  follow it (fixed switch constant, per-side requests, no `combine_first` across the switch).

---

## 2. Plan

### Approach

**The client owns a background event loop.** `ApiClient` (in `utils`) runs one private
asyncio loop on a daemon thread, created on first use, together with an `httpx.AsyncClient`
and an `asyncio.Semaphore(max_concurrency)` that live on that loop. All HTTP goes through
`async request(...)`, which takes the semaphore and calls the existing
`async_request_with_retry`, then parses with `read_response`. Sync callers use
`run(work)`, which submits a coroutine to the loop with `asyncio.run_coroutine_threadsafe`
and blocks on the result. This gives the three things the concept asks for at once:

- the public functions are plain sync calls;
- they work inside Jupyter, because the work never runs on the caller's loop;
- the client really is reusable (the connection pool survives between calls) and closable
  (`close()` closes the `AsyncClient`, stops the loop and joins the thread).

Because the cap sits on `request`, every request the client makes counts toward one
`max_concurrency`, however the requests are grouped: windows of one dataset, or two
datasets at once.

Period resolution, zone normalisation and frame shaping are small pure functions in
`utils`, each in its own module. They take explicit field names, time zone and resolution,
so nothing in them names Energi Data Service.

The subpackage has two parts:

- **`EnergiDataServiceClient`** subclasses `ApiClient`. It turns one dataset request into
  `date_windows` over `max_span` and gathers the windows concurrently through the new
  `utils.chunking.gather_chunked`. Each window asks for every record (`limit=0`) and checks
  the payload's `total` against what came back.
- **`get_day_ahead_prices`** splits the period at the fixed switch instant `SWITCH`
  (2025-10-01 00:00 `Europe/Copenhagen`). It requests *Elspotprices* only if the period
  starts before it and *DayAheadPrices* only if the period ends after it, concurrently when
  both are needed. It pivots each to wide, spreads the hourly one to quarter-hours within
  its own hour, conforms each side to its own slots and joins them. (User decision during
  step 5.)

Rejected:

- **`asyncio.run` per sync call, with a worker thread when a loop is already running.**
  This has less machinery, but an `httpx.AsyncClient` and a semaphore are bound to the loop
  that created them. A new loop per call means a new connection pool per call, and a
  client that has nothing to close, which defeats A5's "reusable and closable".
- **A sync `httpx.Client` with a thread pool for concurrency.** It is simple, but it
  ignores the async wrapper that already exists and the user's stated preference for
  internal async.
- **Pagination by `offset`/`limit`.** The API returns everything with `limit=0`. Splitting
  by time instead keeps windows independent, so they can be fetched concurrently and in
  any order.

### Modules

| Path | New or changed | Purpose |
|---|---|---|
| `src/energydata/utils/api_client.py` | new | `ApiClient` base: background loop, async HTTP client, concurrency cap, retries, parsing. Not `client.py`, so it cannot collide with the subpackage's `client.py` in the flat `tests/` (see `DEVELOPMENT.md`). |
| `src/energydata/utils/periods.py` | new | `start`/`end` argument resolution to a half-open tz-aware period. |
| `src/energydata/utils/zones.py` | new | `BiddingZone`, `BIDDING_ZONES`, argument normalisation. |
| `src/energydata/utils/frames.py` | new | Period index, records → wide frame, coarse → fine expansion, conform to index with NaN padding. The only pandas-using utils module, apart from `periods.py` returning `pd.Timestamp`. |
| `src/energydata/utils/chunking.py` | changed | Adds `gather_chunked`, the concurrent counterpart of `async_fetch_chunked`. |
| `src/energydata/utils/__init__.py` | changed | Re-exports the new public names. |
| `src/energydata/energidataservice/__init__.py` | new | Re-exports `EnergiDataServiceClient`, `EnergiDataServiceError`, `Record`, `get_day_ahead_prices`. |
| `src/energydata/energidataservice/client.py` | new | `EnergiDataServiceClient` and its error type. |
| `src/energydata/energidataservice/day_ahead.py` | new | `get_day_ahead_prices`. One module per market family, so later rounds add siblings. |
| `pyproject.toml` | changed | Runtime `pandas>=3.0,<4`; dev `pandas-stubs`. |
| `tests/conftest.py` | new | The autouse no-network guard, moved here from `test_retry.py` so it covers the whole suite. |
| `tests/test_retry.py` | changed | Its local guard is removed (now in `conftest.py`); its guard-check test stays. |
| `tests/test_readers.py` | changed | The dependency test now asserts the runtime dependencies are exactly `httpx` and `pandas`. |
| `tests/test_api_client.py`, `test_periods.py`, `test_zones.py`, `test_frames.py`, `test_energidataservice_client.py`, `test_day_ahead.py` | new | One suite per new module. `test_chunking.py` is extended for `gather_chunked`. |
| `README.md` | changed | An "Endpoints" section with an Energi Data Service table, plus the conventions: Danish local time, half-open periods, NaN for unpublished slots. |
| `STRUCTURE.md`, `.claude/rules/structure-utils.md`, `.claude/rules/structure-energidataservice.md` (new) | changed / new | Tree line and summary per subpackage; signature detail in the rules files, following the Growth rule. |

### Public API

`TimeLike = date | datetime | str` (a `pd.Timestamp` is a `datetime`).

| Signature | Module | Purpose | Covers |
|---|---|---|---|
| `type TimeLike = date \| datetime \| str` | `utils.periods` | Accepted `start`/`end` input. A string is ISO 8601: date-only (`"2025-10-01"`) counts as a date, anything with a time as a timestamp. | A3 |
| `DANISH_TZ: str = "Europe/Copenhagen"` | `utils.periods` | The default zone. | A1, A3 |
| `resolve_period(start: TimeLike, end: TimeLike \| None = None, *, resolution: timedelta = timedelta(minutes=15), tz: str = DANISH_TZ) -> tuple[pd.Timestamp, pd.Timestamp]` | `utils.periods` | Half-open `[start, end)` in `tz`. Naive input is localised to `tz`. A date means local midnight. With `end=None`, a date gives the whole day and a timestamp gives one `resolution` slot. Raises `ValueError` naming the value when: `end <= start`; any timestamp bound (`start` or `end`, lone or not) is not on a `resolution` boundary; a naive time does not exist or is ambiguous in `tz`; a string does not parse; or `resolution` is not a positive whole divisor of one hour (calendar resolutions such as a day are left for the round that needs one). | A3, A7 |
| `type BiddingZone = Literal["DK1", "DK2"]` | `utils.zones` | | A1 |
| `BIDDING_ZONES: tuple[BiddingZone, ...] = ("DK1", "DK2")` | `utils.zones` | Supported zones, in default order. | A1 |
| `normalize_bidding_zones(bidding_zones: BiddingZone \| Sequence[BiddingZone]) -> tuple[BiddingZone, ...]` | `utils.zones` | A single string becomes a one-tuple, and the given order is kept. An unknown zone (case-sensitive), a duplicate or an empty sequence raises `ValueError` naming the value and listing `BIDDING_ZONES`. | A1, A3, A7 |
| `period_index(start: pd.Timestamp, end: pd.Timestamp, resolution: timedelta) -> pd.DatetimeIndex` | `utils.frames` | Every slot in `[start, end)`, stepped in elapsed time, in `start`'s zone, named `"time"`, dtype `datetime64[ns, tz]`. A naive bound, `start >= end`, a resolution that is not a positive whole divisor of one hour, or a span that is not a whole multiple of it raises `ValueError`. | A1, A3, A4, A7 |
| `records_to_wide(records: Sequence[Mapping[str, object]], *, time: str, column: str, value: str, tz: str = DANISH_TZ) -> pd.DataFrame` | `utils.frames` | Pivot long records into a float frame: index from field `time` (an ISO string, naive means UTC, converted to `tz`, named `"time"`, dtype `datetime64[ns, tz]`), one column per distinct `column` value, holding `value` (`None` becomes NaN). Empty records give an empty frame with a tz-aware index. A duplicate (`time`, `column`) pair or a missing field raises `ValueError` naming it. | A1, A2, A7 |
| `expand_to_resolution(frame: pd.DataFrame, source: timedelta, target: timedelta) -> pd.DataFrame` | `utils.frames` | Each row at `t`, covering `[t, t + source)`, is repeated at `t, t + target, …`. Values never cross into a slot whose own source row is missing, so a gap stays a gap. Raises `ValueError` unless `source` is a positive whole multiple of `target`. | A2, A7 |
| `conform(frame: pd.DataFrame, index: pd.DatetimeIndex, columns: Sequence[str]) -> pd.DataFrame` | `utils.frames` | Reindex to exactly `index` × `columns`, as float64, with NaN where `frame` has nothing; rows outside `index` are dropped; the output index is exactly `index`. A duplicate index in `frame` raises `ValueError`. | A1, A4, A7 |
| `async gather_chunked[T](fetch: Callable[[datetime, datetime], Awaitable[T]], start: datetime, end: datetime, span: timedelta, *, limit: int \| None = None) -> list[T]` | `utils.chunking` | Every window from `date_windows` runs concurrently, at most `limit` at a time (`None` means unbounded), and the results come back in window order. If windows fail, the remaining ones are cancelled and the exception of the lowest-index failed window is raised, chained from the group; callers never see an `ExceptionGroup`. `limit < 1` raises `ValueError`. | A6 |
| `ApiClient(base_url: str, *, policy: RetryPolicy \| None = None, timeout: float = DEFAULT_TIMEOUT, max_concurrency: int = 4, headers: Mapping[str, str] \| None = None, transport: httpx.AsyncBaseTransport \| None = None)` | `utils.api_client` | Source-agnostic base. `max_concurrency < 1` and an empty `base_url` raise `ValueError`. `transport` exists for tests (`httpx.MockTransport`). Read-only properties: `base_url: str`, `max_concurrency: int`, `closed: bool`. | A5, A6 |
| `async ApiClient.request(self, method: str, path: str, *, params: Mapping[str, str \| int \| float] \| None = None, fmt: Format \| None = None) -> Parsed` | `utils.api_client` | One request on the client's loop, under the semaphore, through `async_request_with_retry`, parsed with `read_response`. It errors exactly as those do. Awaiting it on any loop other than the client's raises `RuntimeError`: it is the extension point for subclasses and may only run inside `run`. | A5, A6 |
| `ApiClient.run[T](self, work: Callable[[], Awaitable[T]]) -> T` | `utils.api_client` | Run `work()` on the client's loop and block until it finishes, re-raising its exception. Safe from any thread, including one with a running loop. Raises `RuntimeError` when the client is closed, or when called from the client's own loop thread (which would deadlock). | A6 |
| `ApiClient.close(self) -> None` | `utils.api_client` | Close the HTTP client, stop the loop and join the thread. Idempotent. | A5 |
| `ApiClient.__enter__(self) -> Self`, `ApiClient.__exit__(self, *exc_info: object) -> None` | `utils.api_client` | Context manager; exit calls `close()`. | A5 |
| `type Record = dict[str, JsonValue]` | `energidataservice.client` | One dataset row. | A5 |
| `EnergiDataServiceError(Exception)` | `energidataservice.client` | The payload is not what the API promises: no `records` list, or fewer records than its `total`. | A5 |
| `EnergiDataServiceClient(*, policy: RetryPolicy \| None = None, timeout: float = DEFAULT_TIMEOUT, max_concurrency: int = 4, max_span: timedelta = timedelta(days=31), transport: httpx.AsyncBaseTransport \| None = None)` | `energidataservice.client` | `ApiClient` on `BASE_URL = "https://api.energidataservice.dk"`. A non-positive `max_span` raises `ValueError`. | A5 |
| `async EnergiDataServiceClient.fetch_dataset(self, dataset: str, start: datetime, end: datetime, *, filters: Mapping[str, Sequence[str]] \| None = None, columns: Sequence[str] \| None = None, sort_by: str \| None = None) -> list[Record]` | `energidataservice.client` | `GET /dataset/{dataset}` per `max_span` window, gathered concurrently with `gather_chunked`. The params are: `start`/`end` in UTC as `YYYY-MM-DDTHH:MM`, `timezone=UTC`, `limit=0`, `filter` as compact JSON, and `columns` comma-joined. The windows' records are concatenated in window order. Raises `EnergiDataServiceError` for an unexpected payload, plus the errors of `request`. Naive bounds raise `ValueError`. Like `request`, it raises `RuntimeError` when awaited outside the client's loop. Sends `sort=<sort_by> asc` when `sort_by` is given. | A5, A6 |
| `EnergiDataServiceClient.get_dataset(self, dataset: str, start: datetime, end: datetime, *, filters: Mapping[str, Sequence[str]] \| None = None, columns: Sequence[str] \| None = None, sort_by: str \| None = None) -> list[Record]` | `energidataservice.client` | The sync form: `self.run(lambda: self.fetch_dataset(...))`. | A5, A6 |
| `get_day_ahead_prices(start: TimeLike, end: TimeLike \| None = None, bidding_zones: BiddingZone \| Sequence[BiddingZone] = BIDDING_ZONES, *, client: EnergiDataServiceClient \| None = None) -> pd.DataFrame` | `energidataservice.day_ahead` | See A1 to A4. The docstring carries the output table: EUR/MWh excl. VAT, 15-minute, wide, `DK1`/`DK2`, sources *DayAheadPrices* (`TimeUTC`, `PriceArea`, `DayAheadPriceEUR`) and *Elspotprices* (`HourUTC`, `PriceArea`, `SpotPriceEUR`), hourly prices before `SWITCH` (2025-10-01 00:00 Danish time) repeated over four quarter-hours, 15-minute prices from it, a null or missing 15-minute value NaN, NaN for unpublished slots; a dataset is requested only when the period overlaps its side of `SWITCH`. (User decision during step 5.) Without `client`, it creates one and closes it; a given client is left open. | A1–A4, A6, A8 |
| `main() -> None` in each new module | all | Showcase. The utils showcases are offline (a `MockTransport` for `api_client`). `client` and `day_ahead` call the live API. | — |

### Implementation guide

1. **`pyproject.toml`.** Add `pandas>=3.0,<4` to `dependencies` and `pandas-stubs` to
   `dev`, pinned to the minor series matching pandas 3.0 (e.g. `pandas-stubs>=3.0,<3.1`). Create `.venv` with `pip install -e ".[dev]"` if it is missing, so the gates run
   the project's tools.
2. **`utils/zones.py`**, then **`utils/periods.py`**.
   - String parsing: try `date.fromisoformat` first. If that fails, use `pd.Timestamp(s)`;
     if that fails too, raise `ValueError` naming the string.
   - Check `datetime` before `date`, since a `datetime` is a `date`.
   - Localise naive values with `tz_localize(tz, nonexistent="raise", ambiguous="raise")`
     and re-raise as `ValueError`.
   - A date `end` is midnight of that date. The alignment check is
     `(ts - epoch) % resolution == 0` on the UTC value.
3. **`utils/frames.py`.**
   - `period_index`: `pd.date_range(start, end, freq=resolution, inclusive="left")`, which
     steps in absolute time.
   - `expand_to_resolution`: repeat each row `source // target` times and add offsets
     `k * target`.
   - `conform`: `reindex(index=…, columns=…)` then `astype("float64")`.
   - pandas 3 infers datetime units, so `period_index` and `records_to_wide` both call
     `.as_unit("ns")`; every frame they build has a `datetime64[ns, tz]` index.
4. **`utils/chunking.py` → `gather_chunked`.** Use `date_windows`, an optional
   `asyncio.Semaphore`, and an `asyncio.TaskGroup`, so the first failure cancels the rest.
   Collect results by window position. Catch the `ExceptionGroup` and re-raise the
   lowest-index failed window's exception `from` the group. Put that re-raise rule in a
   private helper the day-ahead module reuses.
5. **`utils/api_client.py`.**
   - The loop thread is created lazily under a `threading.Lock`. The `AsyncClient` and the
     semaphore are created on the loop thread by an init coroutine.
   - `run` checks `threading.get_ident()` against the loop thread, then submits a private
     `async def _call(): return await work()` with `run_coroutine_threadsafe`, so `work()`
     is invoked on the loop thread. If `.result()` is interrupted (KeyboardInterrupt),
     cancel the concurrent future before re-raising, so nothing keeps running on the loop.
   - `request` checks `asyncio.get_running_loop() is self._loop`, else `RuntimeError`.
   - `close` runs `aclose()` on the loop, then `loop.call_soon_threadsafe(loop.stop)`,
     joins the thread and closes the loop.
   - The thread is a daemon, so a client that is never closed does not hang interpreter
     exit.
   - The showcase uses a `MockTransport`.
6. **`utils/__init__.py`** re-exports.
7. **`energidataservice/client.py`**, then **`energidataservice/__init__.py`**. Validate
   the payload: `records` is a list and `len(records) >= total` when `total` is an int.
   Only a shortfall is an error.
8. **`energidataservice/day_ahead.py`.**
   - Resolve the period, normalise the zones and build the index.
   - `SWITCH` is a module constant. Request *Elspotprices* only when the period starts
     before it, over `[start, min(end, SWITCH))` widened to whole UTC hours (floor the
     start, ceil the end), so a period starting mid-hour still gets its hour's price.
     Request *DayAheadPrices* only when the period ends after it, over
     `[max(start, SWITCH), end)`.
   - Run the needed `fetch_dataset` calls in one `client.run` through `_gather_ordered`
     (lowest-index re-raise rule, so a failure in one cancels the other before an own
     client is closed). The client's semaphore caps the total.
   - Pass `filters={"PriceArea": list(zones)}`, `columns=[<time field>, "PriceArea",
     <EUR field>]` and `sort_by=<time field>` to each.
   - Pivot each with `records_to_wide`, expand the hourly one 1 h → 15 min, conform the
     hourly side to the slots before `SWITCH` and the 15-minute side to the slots from it,
     and concatenate. No `combine_first` across the switch: a null 15-minute value stays
     NaN. (User decision during step 5.)
   - The dataset and field names are module constants.
9. **Tests**, per the intents below. Move the no-network guard to `tests/conftest.py` and
   update the dependency test in `test_readers.py`. Retry tests use
   `RetryPolicy(base_delay=0.0, max_delay=0.0)`.
10. **Docs.** Add the README endpoints section and update `STRUCTURE.md` and the two rules
    files. `STRUCTURE.md` must name every new `.py` path in full — `src/…` modules and
    `tests/…` files, `tests/conftest.py` included — because the stop gate matches paths
    verbatim.

### Test intents

| # | Must prove | Covers |
|---|---|---|
| T1 | `resolve_period` covers: a date alone gives a whole day of 92, 96 or 100 quarter-hours (spring DST, normal, autumn DST); a timestamp alone gives one slot; `end` is exclusive; naive, aware and string inputs; aware input in another zone is converted; and every `ValueError` case names the value, including a misaligned `start` or `end` when both are given and a resolution that does not divide one hour. | A3 |
| T2 | `normalize_bidding_zones`: a string, a list in a non-default order and the default; unknown, lowercase, duplicate and empty inputs raise. | A1, A3 |
| T3 | `period_index` is half-open, `datetime64[ns, tz]`, correct across both DST changes, and rejects a resolution that does not divide one hour. `records_to_wide` handles UTC strings, `None` values, empty input and duplicates. `expand_to_resolution` fills exactly the quarters of present hours and leaves gaps as gaps. `conform` pads with NaN, keeps column order and drops out-of-period rows. | A1, A2, A4, A7 |
| T4 | `gather_chunked` returns results in window order even when windows finish out of order. Its in-flight count never exceeds `limit` and does exceed 1 when allowed. A failure cancels the rest and raises the lowest-index window's own exception, never an `ExceptionGroup`. | A6 |
| T5 | `ApiClient`: it hits `base_url + path`; a 503 then 200 is retried through the wrapper; in-flight requests never exceed `max_concurrency`; `run` works from inside a running event loop and from several threads; the client is reusable across many `run` calls; `close` is idempotent; `run` after close raises; the context manager closes; a `run` from the loop thread raises; awaiting `request` on a foreign loop raises `RuntimeError`; interrupting `run` cancels the work. | A5, A6 |
| T6 | `EnergiDataServiceClient` sends the exact params (UTC start/end, `timezone`, `limit=0`, `filter` JSON, `columns`, `sort`). A period longer than `max_span` becomes several concurrent requests whose records are concatenated in window order. A `total` larger than the records count and a missing `records` key both raise `EnergiDataServiceError`. An HTTP 400 surfaces as `httpx.HTTPStatusError`. | A5, A6 |
| T7 | `get_day_ahead_prices` on mocked data: a 15-minute-era period; an hourly-era period repeated over quarters; a null 15-minute value is NaN even when hourly data exists; a period spanning the switch combining the two; single-dataset requests per side; a lone date and a lone mid-hour timestamp; a future period returning all NaN of the right shape; a zone subset and its order; both datasets requested only when the period spans the switch; a passed client left open and an own client closed; the captured requests carry the `PriceArea` filter and the columns; when one dataset returns HTTP 400 the call raises `httpx.HTTPStatusError` and its own client is still closed cleanly. | A1–A4, A6 |
| T8 | No module under `utils/` names Energi Data Service, its datasets or its fields (a source scan). The runtime dependencies are exactly `httpx` and `pandas`. No test reaches the network (the conftest guard and its check). The README endpoints table and `get_day_ahead_prices.__doc__` both name EUR/MWh, 15 min, wide, DK1/DK2, *DayAheadPrices* and *Elspotprices*. | A7, A8 |

### Coverage check

- **Every criterion has an API entry.** A1: `get_day_ahead_prices`, `period_index`,
  `conform`, zones. A2: `expand_to_resolution`, `records_to_wide`. A3: `resolve_period`,
  `normalize_bidding_zones`. A4: `conform`, `period_index`. A5: `ApiClient`, the client.
  A6: `gather_chunked`, `ApiClient.run`/`request`. A7: the utils modules. A8: the docstring
  and README.
- **Every criterion has a test intent.** A1: T2, T3, T7. A2: T3, T7. A3: T1, T2, T7.
  A4: T3, T7. A5: T5, T6. A6: T4, T5, T6, T7. A7: T3, T8. A8: T8.
- **Every API entry traces to a criterion.** `DANISH_TZ` traces to A1 and A3, `Record`
  and `EnergiDataServiceError` to A5, and each `main` follows the module convention.

### Critique

Read by the `plan-critic` (verdict: accept with changes). Every finding applied:

1. `TaskGroup` raises `ExceptionGroup`, not the first exception → applied: lowest-index
   window's exception re-raised from the group (API, guide 4, T4).
2. `asyncio.gather` leaves the other dataset running on failure → applied: TaskGroup with
   the same rule in guide 8; T7 covers an HTTP 400 with clean close.
3. `run` did not say which thread calls `work()` → applied: `work()` runs on the loop
   thread via a private wrapper coroutine; interrupted `run` cancels the work (guide 5, T5).
4. Public async methods widen "async is internal only" and break on a foreign loop →
   applied option (a): they stay public as the subclass extension point but raise
   `RuntimeError` unless awaited on the client's loop (API, guide 5, T5). Public *dataset
   functions* stay sync only, as agreed.
5. Generic `resolution` breaks above one hour → applied: must divide one hour exactly,
   else `ValueError` (API, T1, T3).
6. pandas 3 unit inference → applied: `datetime64[ns, tz]` everywhere (API, guide 3, Risks).
7. Endpoint did not use `filters`/`columns` → applied (guide 8, T7).
8. `end` exclusivity and sort order assumed → applied: Risks entry, `sort_by` parameter,
   T6 says window order.
9. A8 docstring not tested; `pandas-stubs` unpinned → applied (T8, guide 1).
10. Shorthand test names would fail the stop gate → applied (guide 10).

Minor: a misaligned `start`/`end` is rejected even when both are given — stated in the API
and T1, wider than section 1 only by applying the same rule to both bounds.

### Risks

- **The live API is unreachable from the build container** (proxy 403). The `client` and
  `day_ahead` showcases fail there with a transport error. Step 4 records that output and
  does not count it as a gate failure; every other showcase must run. This is not a halt.
- **Dataset or field names may be wrong**, since they are taken from the API docs without a
  live check. They are module constants. A mismatch found later is a one-line fix, not a
  halt; the user confirms by running the showcase.
- **pandas-stubs strictness.** mypy may reject idiomatic pandas calls. Narrow with typed
  locals or a `cast`. A `# type: ignore[code]  # reason` is allowed where the stub is
  wrong. Do not halt.
- **pandas 3 datetime units.** Strings, `Timestamp` and `date_range` may infer different
  units (`s`/`us`/`ns`), and `assert_frame_equal` compares dtypes. Normalise with
  `.as_unit("ns")` as in guide 3; do not loosen the assertions.
- **Assumed API semantics:** that `end` is exclusive and that `sort=… asc` is honoured.
  These are constants the user confirms by running the showcase. If `end` were inclusive,
  boundary records would repeat across windows and `records_to_wide` would raise on the
  duplicate. Fixing that is a one-line change to the window end, not a halt.
- **Thread and loop lifecycle bugs** (a hang on `close`, a leaked thread in tests). Every
  test that creates a client closes it via the context manager. A test that hangs gets a
  timeout via `concurrent.futures` in the test itself. A hang the build cannot fix in two
  attempts is a gate failing twice: halt.
- **Editing earlier-round tests.** Moving the guard and changing the dependency assertion
  modifies `feat/shared-http-utils` tests. Their criterion A9 was round-scoped ("the only
  *new* dependency"), so this does not amend that round's concept. Behaviour must not
  change: `test_retry.py`'s guard-check test must still pass.
- **A slot with no data must be NaN, never filled from a neighbour.** If shaping ever
  forward-fills across a gap, A4 is broken. T3 and T7 pin this; do not use a plain
  `ffill()`.
- **Anything that would change section 1** (for example, the API refusing `limit=0`, or
  the frame needing a different shape) is a halt.

---

## 3. Implementation notes

No deviation from the Public API table; every signature is as written. Smaller points:

- **Tests left to step 5.** `/implement` writes production code only, so `tests/conftest.py`
  (the guard moved out of `test_retry.py`), the `test_retry.py` edit and the new suites are
  not written. `tests/test_readers.py::test_httpx_is_the_only_runtime_dependency` is red
  until step 5 changes it to assert `httpx` and `pandas`; that is the plan's listed edit,
  not a regression. STRUCTURE.md already names the test paths step 5 creates.
- **Absolute imports across subpackages.** Ruff's `TID252` forbids `from ..utils import`,
  so `energidataservice/client.py` and `day_ahead.py` import `energydata.utils.<module>`.
  Within `utils`, relative sibling imports are unchanged.
- **`_gather_ordered` is imported across modules.** The plan asked for the lowest-index
  re-raise rule as a private helper the day-ahead module reuses, so
  `day_ahead.py` imports `_gather_ordered` from `energydata.utils.chunking`. It is not in
  the public API or `utils/__init__.py`.
- **`gather_chunked` also re-exported.** `utils/__init__.py` re-exports the new public
  names (`ApiClient`, `gather_chunked`, `resolve_period`, `TimeLike`, `DANISH_TZ`,
  `BiddingZone`, `BIDDING_ZONES`, `normalize_bidding_zones`, the four `frames` functions).
- **`ApiClient.close`** also cancels work still running on the loop before closing the HTTP
  client, so a `run` in flight in another thread ends with a cancellation instead of hanging.
- Offline check: `get_day_ahead_prices` against a `MockTransport` returned the expected
  frame (mid-hour start, 15-minute value winning, hourly forward-fill; superseded by the user decision during step 5). The live showcases
  fail here with `httpx.ProxyError: 403 Forbidden`, as the plan's Risks expected.

- **User decision during step 5.** `get_day_ahead_prices` now uses the fixed switch
  `SWITCH` = 2025-10-01 00:00 `Europe/Copenhagen` instead of `combine_first` over both
  datasets: hourly (repeated over quarters) before it, DayAheadPrices only from it, a null
  or missing 15-minute value NaN, each dataset requested only when the period overlaps its
  side. Section 2's approach, guide 8, T7 and the Public API purpose text were edited to
  match; the README row, the docstring and `structure-energidataservice.md` too. No
  signature changed; `SWITCH` is a new public module constant.

---

## 4. Verification log

| Check | Result |
|---|---|
| `ruff check .` | All checks passed |
| `ruff format --check .` | 56 files already formatted |
| `mypy` | Success: no issues found in 25 source files |
| `pytest` | 802 passed, 1 failed: `tests/test_readers.py::test_httpx_is_the_only_runtime_dependency` (`2 == 1` dependencies), the planned step 5 edit, not a regression |
| Plan completeness | Every row of the Public API table checked with `inspect.signature` against the code: all 16 signatures, constants (`DANISH_TZ`, `BIDDING_ZONES`, `BASE_URL`) and re-exports match. Missing: none. Deviation: none. Unplanned: none (`_gather_ordered` is private) |
| `STRUCTURE.md` | Auditor items 3, 4 and 5 applied (error and constructor descriptions in `structure-energidataservice.md`, `ApiClient.close` `RuntimeError` in `structure-utils.md`). Items 1 and 2 are the test files and `tests/conftest.py` step 5 creates, so no edit. Otherwise in sync |
| `python -m <package>.<module>` | `utils.api_client`, `periods`, `zones`, `frames`, `chunking`: exit 0, output informative, in the required showcase form (the re-export `RuntimeWarning` is expected). `energidataservice.client` and `day_ahead`: `httpx.ProxyError: 403 Forbidden` (container cannot reach the live API; per Risks, not a gate failure) |

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

