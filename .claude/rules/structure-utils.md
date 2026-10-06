---
paths:
  - "src/energydata/utils/**"
  - "tests/test_retry.py"
  - "tests/test_readers.py"
  - "tests/test_chunking.py"
  - "tests/test_api_client.py"
  - "tests/test_periods.py"
  - "tests/test_zones.py"
  - "tests/test_frames.py"
  - "tests/conftest.py"
---

# Structure: `src/energydata/utils/`

Shared utilities. `utils/__init__.py` re-exports every public name below (not `main`),
so callers use `from energydata.utils import ...`. Every module has a `main()` showcase,
runnable as `python -m energydata.utils.<module>`.

## `src/energydata/utils/retry.py`

| Signature | Description |
|---|---|
| `RETRY_STATUSES: frozenset[int]` | `{429, 500, 502, 503, 504}`, the retried statuses. |
| `RETRY_EXCEPTIONS: tuple[type[Exception], ...]` | `httpx.TimeoutException`, `NetworkError`, `RemoteProtocolError`. |
| `DEFAULT_TIMEOUT: float` | 30.0 s, for a client the wrappers create. |
| `type HoldoffFn = Callable[[httpx.Response], float \| None]` | Reads a holdoff in seconds from a response. |
| `RetryPolicy(max_attempts=5, base_delay=1.0, max_delay=60.0, max_holdoff=300.0, holdoff_reader=None)` | Frozen, validated dataclass; `ValueError` naming the bad value. |
| `RetryError(message: str, attempts: int, response: httpx.Response \| None)` | Base error; attributes `attempts`, `response`. |
| `RetriesExhaustedError(attempts, response, exception)` | Attempts ran out; exactly one of `response`/`exception` set; exception chained as `__cause__`. |
| `HoldoffTooLongError(attempts, response, holdoff, max_holdoff)` | Server asked for more than the cap; attribute `holdoff`. |
| `retry_after_seconds(response: httpx.Response, now: datetime \| None = None) -> float \| None` | Parse `Retry-After` (seconds or HTTP date); naive `now` is a `ValueError`. |
| `backoff_delay(retry: int, policy: RetryPolicy, rng: random.Random \| None = None) -> float` | Equal-jitter exponential delay in `[cap/2, cap]`. |
| `request_with_retry(method, url, *, client=None, policy=None, params=None, headers=None, content=None, json=None, timeout=None, sleep=time.sleep) -> httpx.Response` | Sync wrapper. |
| `async_request_with_retry(method, url, *, client: httpx.AsyncClient \| None = None, policy=None, params=None, headers=None, content=None, json=None, timeout=None, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> httpx.Response` | Async wrapper; same behaviour as `request_with_retry`, both share one private decision function; the waits are awaited, so the event loop keeps running. |
| `main() -> None` | Showcase. |

## `src/energydata/utils/readers.py`

| Signature | Description |
|---|---|
| `type Format = Literal["json", "xml", "csv", "zip"]`, `FORMATS` | The formats. |
| `type JsonValue`, `type Parsed` | Return types of the readers. |
| `ParseError(fmt: Format \| None, message: str)` | `ValueError` subclass; attributes `format`, `detail`. |
| `read_json(data: bytes) -> JsonValue` | JSON. |
| `read_xml(data: bytes) -> ET.Element` | XML root element. |
| `read_csv(data: bytes, *, delimiter: str \| None = None, encoding: str = "utf-8-sig") -> list[dict[str, str]]` | CSV rows; sniffs `,` `;` tab. |
| `read_zip(data: bytes) -> dict[str, Parsed]` | Members parsed by extension; unknown as bytes. |
| `format_from_content_type(content_type: str) -> Format \| None` | By media type. |
| `format_from_filename(name: str) -> Format \| None` | By extension. |
| `read_bytes(data: bytes, fmt: Format) -> Parsed` | Dispatch. |
| `read_response(response: httpx.Response, fmt: Format \| None = None) -> Parsed` | By `Content-Type` unless `fmt` given. |
| `main() -> None` | Showcase. |

## `src/energydata/utils/chunking.py`

| Signature | Description |
|---|---|
| `date_windows(start: datetime, end: datetime, span: timedelta) -> list[tuple[datetime, datetime]]` | Half-open windows covering `[start, end)`, stepped in elapsed UTC time. |
| `fetch_chunked[T](fetch: Callable[[datetime, datetime], T], start, end, span) -> list[T]` | One call per window, in order. |
| `async_fetch_chunked[T](fetch: Callable[[datetime, datetime], Awaitable[T]], start, end, span) -> list[T]` | Same, awaiting each window before the next. |
| `async gather_chunked[T](fetch: Callable[[datetime, datetime], Awaitable[T]], start: datetime, end: datetime, span: timedelta, *, limit: int \| None = None) -> list[T]` | Every window concurrently, at most `limit` at once (`None` unbounded), results in window order. On failure the rest are cancelled and the lowest-index failed window's exception is raised, chained from the group (never an `ExceptionGroup`). `limit < 1` is a `ValueError`. |
| `main() -> None` | Showcase. |

## `src/energydata/utils/api_client.py`

| Signature | Description |
|---|---|
| `ApiClient(base_url: str, *, policy: RetryPolicy \| None = None, timeout: float = DEFAULT_TIMEOUT, max_concurrency: int = 4, headers: Mapping[str, str] \| None = None, transport: httpx.AsyncBaseTransport \| None = None)` | Source-agnostic base. Owns one asyncio loop on a daemon thread, started on first use, with an `httpx.AsyncClient` and a semaphore on it. Empty `base_url` or `max_concurrency < 1` is a `ValueError`. Properties `base_url`, `max_concurrency`, `closed`. |
| `async ApiClient.request(method: str, path: str, *, params: Mapping[str, str \| int \| float] \| None = None, fmt: Format \| None = None) -> Parsed` | One request under the semaphore through `async_request_with_retry`, parsed with `read_response`. Subclass extension point; `RuntimeError` unless awaited on the client's own loop (inside `run`). |
| `ApiClient.run[T](work: Callable[[], Awaitable[T]]) -> T` | Run `work()` on the client's loop and block. Safe from any thread, including one with a running loop. `RuntimeError` when closed or called from the loop thread. An interrupt cancels the work. |
| `ApiClient.close() -> None` | Cancel remaining work, close the HTTP client, stop the loop, join the thread. Idempotent. `RuntimeError` if called from the client's own loop thread. |
| `ApiClient.__enter__() -> Self`, `__exit__(*exc_info) -> None` | Context manager; exit closes. |
| `main() -> None` | Showcase, offline through `httpx.MockTransport`. |

## `src/energydata/utils/periods.py`

| Signature | Description |
|---|---|
| `type TimeLike = date \| datetime \| str` | `start`/`end` input; a string is ISO 8601, date-only counts as a date. |
| `DANISH_TZ: str` | `"Europe/Copenhagen"`. |
| `resolve_period(start: TimeLike, end: TimeLike \| None = None, *, resolution: timedelta = timedelta(minutes=15), tz: str = DANISH_TZ) -> tuple[pd.Timestamp, pd.Timestamp]` | Half-open `[start, end)` in `tz`. Naive is read as `tz`; a date is local midnight; no `end` means the whole day for a date and one slot for a timestamp. `ValueError` for `end <= start`, a misaligned timestamp bound, a nonexistent or ambiguous naive time, an unparseable string, an unknown `tz`, or a `resolution` that is not a positive whole divisor of an hour. |
| `main() -> None` | Showcase. |

## `src/energydata/utils/zones.py`

| Signature | Description |
|---|---|
| `type BiddingZone = Literal["DK1", "DK2"]`, `BIDDING_ZONES: tuple[BiddingZone, ...]` | The supported zones, in default order. |
| `normalize_bidding_zones(bidding_zones: BiddingZone \| Sequence[BiddingZone]) -> tuple[BiddingZone, ...]` | One zone or many, order kept. `ValueError` for an unknown (case-sensitive), duplicate or empty input. |
| `main() -> None` | Showcase. |

## `src/energydata/utils/frames.py`

The only pandas-using module besides `periods.py`. Every frame it builds has a
`datetime64[ns, tz]` index named `"time"`.

| Signature | Description |
|---|---|
| `period_index(start: pd.Timestamp, end: pd.Timestamp, resolution: timedelta) -> pd.DatetimeIndex` | Every slot of `[start, end)`, stepped in elapsed time. `ValueError` for a naive bound, `start >= end`, a bad resolution or a span that is not a whole multiple of it. |
| `block_index(start: pd.Timestamp, end: pd.Timestamp, hours: int) -> pd.DatetimeIndex` | Every block start in `[start, end)`: local wall-clock hours that are multiples of `hours` (a positive divisor of 24), minute 0. A DST change shortens or lengthens the block holding it but never moves a start; a repeated wall-clock start is kept once, the earlier, and one inside the spring gap is absent. `ValueError` for a naive bound, `start >= end`, a bad `hours` or a bound that is not a block start. |
| `records_to_wide(records: Sequence[Mapping[str, object]], *, time: str, column: str, value: str, tz: str = DANISH_TZ) -> pd.DataFrame` | Pivot long records to a float frame (naive times are UTC, `None` is NaN, empty input gives an empty tz-aware frame). `ValueError` for a missing field, an unparseable value or a duplicate (`time`, `column`). |
| `expand_to_resolution(frame: pd.DataFrame, source: timedelta, target: timedelta) -> pd.DataFrame` | Repeat each row over the finer slots it covers; gaps stay gaps. `ValueError` unless `source` is a positive whole multiple of `target`. |
| `conform(frame: pd.DataFrame, index: pd.DatetimeIndex, columns: Sequence[str]) -> pd.DataFrame` | Reindex to exactly `index` x `columns` as float64, NaN where missing, never filled from a neighbour. `ValueError` for a duplicate index. |
| `combine_levels(parts: Mapping[str, pd.DataFrame], index: pd.DatetimeIndex, outer: Sequence[str]) -> pd.DataFrame` | Conform each part to `index` and `outer`, then join with MultiIndex columns `(outer, part key)`, outer first then `parts` order; float64. `ValueError` for empty `parts` or `outer`, a repeated `outer` name, or a part with a duplicate index entry. |
| `main() -> None` | Showcase. |

## Tests

One suite per module, written at step 5. No test touches the network.

- `tests/test_retry.py` — every public name, driven through `httpx.MockTransport`
  with an injected `sleep`. The autouse fixture in `tests/conftest.py` patches the real httpx transports to
  raise, so a test that reaches the network fails, and one test checks the guard
  itself. Also covers: backoff bounds and overflow, `Retry-After` in seconds and in
  all three HTTP-date forms, the holdoff cap and the holdoff reader, exhaustion
  versus holdoff errors, closing a created client on every exit (cancellation
  included), leaving a passed client open, sync/async parity, the async wrapper not
  blocking the event loop, and that `main()` runs.
- `tests/test_readers.py` — each reader's happy path, malformed input and
  `ParseError` naming; CSV delimiter sniffing, BOM and encoding handling; ZIP
  recursion, member naming and damaged archives; mapping by content type and by
  filename; `read_bytes` dispatch; `read_response` charset handling. Also asserts
  that `httpx` and `pandas` are the only runtime dependencies in `pyproject.toml`.
- `tests/test_chunking.py` — `date_windows` coverage and contiguity, both DST
  changes, mixed zones, the repeated hour, and every validation error;
  `fetch_chunked` and `async_fetch_chunked` run through the same parametrised
  cases (order, stopping at the first exception, no call when validation fails),
  plus a check that async windows never overlap, and `gather_chunked` (order,
  concurrency cap, lowest-index failure, validation).
- `tests/test_api_client.py` — constructor validation, no thread before first use, URL
  joining, parsing, retry of 503 and not of 400, the concurrency cap (also across
  threads), `run` inside a running loop and from other threads, reuse, `run`/`close` from
  the loop thread and `request` on a foreign loop raising, idempotent `close`, the context
  manager, cancellation on close and on interrupt, the `run`/`close` race.
- `tests/test_periods.py` — `resolve_period`: whole local day (92/96/100), lone
  timestamp, date versus timestamp strings, naive as Danish time, exclusive end, aware
  input converted, the repeated hour, every `ValueError` naming the value.
- `tests/test_zones.py` — `normalize_bidding_zones`: order kept, string wrapped, unknown,
  empty and duplicate input refused naming the value, purity.
- `tests/test_frames.py` — `period_index` across DST and its rejections; `block_index` (DST days, repeated hour, rejections); `combine_levels` (order, padding, foreign columns dropped, float64, empty and repeated input, purity); `records_to_wide`
  time parsing, `None` values, empty input, duplicates, missing fields;
  `expand_to_resolution` gaps and the autumn DST hour; `conform` padding, order, never
  filling, duplicates, float64.
- `tests/conftest.py` — the autouse no-network guard for the whole suite, plus the market-test support (see `structure-energidataservice.md`).
