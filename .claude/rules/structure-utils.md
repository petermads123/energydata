---
paths:
  - "src/energydata/utils/**"
  - "tests/test_retry.py"
  - "tests/test_readers.py"
  - "tests/test_chunking.py"
---

# Structure: `src/energydata/utils/`

Shared HTTP utilities. `utils/__init__.py` re-exports every public name below (not `main`),
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
| `async_request_with_retry(...same..., sleep=asyncio.sleep) -> httpx.Response` | Async wrapper; both share one private decision function. |
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
| `main() -> None` | Showcase. |

## Tests

`tests/test_retry.py`, `tests/test_readers.py` and `tests/test_chunking.py`: one suite per
module, written at step 5. No test touches the network.
