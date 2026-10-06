# Shared HTTP utilities

<!-- claude-plan step=6 status=active -->

| Field | Value |
|---|---|
| Feature | `feat/shared-http-utils` |
| Round | `1` |
| Branch | `feat/shared-http-utils` |
| Started | `2026-10-06` |

## Progress

| # | Step | Skill | Runs | Status |
|---|---|---|---|---|
| 1 | Conceptualize | `/conceptualize` | with the user | done |
| 2 | Plan | `/plan` | with the user | done |
| 3 | Implement | `/implement` | in `/build` | done |
| 4 | Verify | `/verify` | in `/build` | done |
| 5 | Test | `/test` | in `/build` | done |
| 6 | Concept check | `/concept-check` | in `/build` | in progress |
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

An `energydata.utils` subpackage that the three source subpackages (Energi Data Service,
Eloverblik, ENTSO-E Transparency Platform) build on. Three parts:

- **A retrying request wrapper**, sync and async with identical behaviour, on `httpx`.
  Transient failures are retried with exponential backoff and jitter; a holdoff the server
  states (a `Retry-After` header, or a value a caller-supplied function reads from the
  response) replaces the computed delay.
- **Response readers** that turn JSON, XML, CSV and ZIP bodies into plain Python data.
- **A date-range chunker** ("stepback" in the sense of splitting a too-large request) that
  splits a period an API refuses as too long into windows and runs a fetch per window.

### Why it is worth building

All three sources need the same handling of rate limits and outages, and ENTSO-E and
Eloverblik both cap the period one request may cover. Without a shared layer each
subpackage grows its own slightly different copy.

### Inputs and outputs

- **Wrapper:** an `httpx` client (or one it creates), method, URL and the usual request
  arguments; returns the `httpx.Response` on success. Configurable: maximum attempts, base
  delay, maximum delay, the longest server-requested holdoff it will honour (default five
  minutes), and an optional function reading a holdoff from a response, for APIs that put
  it in the body rather than a header.
- **Readers:** bytes or a response in; out: JSON → `dict`/`list`, XML → `ElementTree`
  element, CSV → `list[dict[str, str]]`, ZIP → `{member name: parsed content}` with each
  member parsed by its file extension and an unknown extension returned as raw bytes.
  Plain Python rather than DataFrames because much of the data is not tabular (nested
  ENTSO-E time series, Eloverblik metering-point records); each source converts to tables
  where the data is a table.
- **Chunker:** timezone-aware `start` and `end` and a maximum span (`timedelta`) → the
  list of windows. The chunked fetch (sync and async) calls a caller's function once per
  window and returns the results in window order; joining them is source-specific.

### How it connects to the rest of the repo

Nothing in the package calls it yet; the future `energidataservice`, `eloverblik` and
`entsoe` subpackages are its callers. It does not touch `hello_world`, which stays as the
placeholder for now. `httpx` becomes the first runtime dependency in `pyproject.toml`.

### Explicitly out of scope

- Authentication — Eloverblik's refresh/access-token exchange, ENTSO-E's security token.
- Endpoint URLs and any source-specific parameters.
- Interpreting ENTSO-E's acknowledgement (error) documents.
- DataFrames and any tabular conversion.
- Proactive rate limiting (throttling before the server objects).
- Caching.
- ZIP-bomb or decompression-size limits.
- Running chunk windows concurrently: the async chunked fetch runs windows one at a time,
  so splitting a request does not multiply the load on the server.

Assumptions: a holdoff longer than the cap raises immediately rather than sleeping for it;
XML is parsed with the standard library.

### Acceptance criteria

| # | The finished feature... |
|---|---|
| A1 | Retries network errors (connection, read, write and close failures, and a dropped connection mid-response), timeouts, HTTP 429 and HTTP 500/502/503/504 up to the maximum number of attempts and returns the first successful response; any other 4xx or other error status, and any other transport error (including `httpx.ProxyError`), is raised on the first attempt without retrying. |
| A2 | Without a server-stated holdoff, waits before retry *n* a delay that grows exponentially from the base delay, with random jitter, and never exceeds the maximum delay; all three are configurable. |
| A3 | When a response carries `Retry-After` (seconds or an HTTP date), or a supplied holdoff function returns a value, waits that long instead of the computed delay; a holdoff longer than the cap raises immediately with an error stating the requested wait. |
| A4 | When attempts are exhausted, raises one specific error type carrying the attempt count and the last response or exception. |
| A5 | The sync and async wrappers behave identically for A1 to A4, and the async one waits without blocking the event loop. |
| A6 | The readers parse JSON, XML, CSV and ZIP as described in Inputs and outputs; reading a response picks the format from its `Content-Type`, and the format can also be named explicitly; a malformed body raises an error naming the format. |
| A7 | The chunker returns contiguous half-open windows, each at most the span long (the last may be shorter), together covering exactly `[start, end)`; `start >= end`, a naive datetime, or a non-positive span raises `ValueError`. |
| A8 | The sync and async chunked fetch call the function once per window, in order, and return the results in window order; an error in any window propagates. |
| A9 | The test suite makes no real network calls, and `httpx` is the only new runtime dependency. |

### Open questions

None.

---

## 2. Plan

### Approach

A new subpackage `src/energydata/utils/` with one module per concern: `retry.py` (the
wrapper), `readers.py` (body parsing) and `chunking.py` (date windows). The sync and async
wrappers are thin loops around one shared private decision function — given the attempt
number and the outcome (a response or a transport exception), it returns "done", "raise
now" or "wait this long" — so A5's "identical behaviour" holds by construction rather than
by two copies kept in step. Settings live in a frozen, validated `RetryPolicy` dataclass
rather than five keyword arguments repeated on two functions. Sleep is injectable
(`sleep=` parameter defaulting to `time.sleep` / `asyncio.sleep`) so tests observe the
delays without waiting, and `httpx.MockTransport` serves canned responses, so no test
touches the network. Async tests run through `asyncio.run` inside ordinary test functions,
so no `pytest-asyncio` dev dependency is needed.

Rejected: **a client class** (`RetryingClient` wrapping `httpx.Client` and overriding
`send`) — the cleanest call site, but it must mirror httpx's request surface, doubles for
async, and couples retry state to client lifetime; functions taking an optional client give
the same reuse with far less surface. **An httpx transport wrapper** (retrying inside
`BaseTransport.handle_request`) — invisible to callers, but a transport sees requests, not
the caller's policy per call, and holdoff errors would surface from deep inside httpx.
**`tenacity` for the retry loop** — a second runtime dependency, which A9 forbids, for a
loop of thirty lines.

### Modules

| Path | New or changed | Purpose |
|---|---|---|
| `src/energydata/utils/__init__.py` | new | Subpackage entry; re-exports every public name below. |
| `src/energydata/utils/retry.py` | new | `RetryPolicy`, the error types, `Retry-After` parsing, backoff computation, sync and async request wrappers. |
| `src/energydata/utils/readers.py` | new | JSON/XML/CSV/ZIP readers, `Content-Type` detection, dispatch by format. |
| `src/energydata/utils/chunking.py` | new | Date-range windows and sync/async chunked fetch. |
| `tests/test_retry.py` | new | Suite for `retry.py`. |
| `tests/test_readers.py` | new | Suite for `readers.py`. |
| `tests/test_chunking.py` | new | Suite for `chunking.py`. |
| `pyproject.toml` | changed | `dependencies = ["httpx>=0.28,<0.29"]`; dev extra gains `"tzdata"` — the DST tests and the chunking showcase use `zoneinfo.ZoneInfo("Europe/Copenhagen")`, and Windows has no system time-zone database. Dev only, so A9 holds. |
| `STRUCTURE.md` | changed | Tree gains `utils/`; one line per new module and test file (the stop gate needs every path named here). |
| `.claude/rules/structure-utils.md` | new | Per-subpackage detail with the signature tables, `paths: ["src/energydata/utils/**", "tests/test_retry.py", "tests/test_readers.py", "tests/test_chunking.py"]` — the growth rule in `STRUCTURE.md` for a package that now has subpackages. |

`src/energydata/__init__.py` is not changed: callers import `energydata.utils`.

### Public API

`retry.py` (imports: `asyncio`, `random`, `time`, `collections.abc.Awaitable, Callable, Mapping`, `dataclasses`, `datetime`, `email.utils`, `httpx`):

| Signature | Purpose | Covers |
|---|---|---|
| `RETRY_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})` | Statuses that are retried. | A1 |
| `RETRY_EXCEPTIONS: tuple[type[Exception], ...] = (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)` | Transport errors that are retried; any other exception propagates on the first attempt. | A1 |
| `DEFAULT_TIMEOUT: float = 30.0` | Timeout, in seconds, of a client the wrappers create themselves. | A1 |
| `type HoldoffFn = Callable[[httpx.Response], float \| None]` | Reads a holdoff in seconds from a response, or `None` when it has none. | A3 |
| `@dataclass(frozen=True) class RetryPolicy` with fields `max_attempts: int = 5`, `base_delay: float = 1.0`, `max_delay: float = 60.0`, `max_holdoff: float = 300.0`, `holdoff_reader: HoldoffFn \| None = None` | The settings. `__post_init__` raises `ValueError` naming the value when `max_attempts < 1`, `base_delay < 0`, `max_delay < base_delay` or `max_holdoff < 0`. | A2, A3 |
| `class RetryError(Exception)` | Base of the two errors below, so a caller can catch both. Attributes `attempts: int`, `response: httpx.Response \| None`. | A3, A4 |
| `class RetriesExhaustedError(RetryError)` — `__init__(self, attempts: int, response: httpx.Response \| None, exception: Exception \| None) -> None` | Attempts ran out. Exactly one of `response` (last retryable status) and `exception` (last transport error) is set; the exception is also chained as `__cause__`. Message names the attempt count and the status or exception. | A4 |
| `class HoldoffTooLongError(RetryError)` — `__init__(self, attempts: int, response: httpx.Response, holdoff: float, max_holdoff: float) -> None` | The server asked for a wait longer than the cap. Attribute `holdoff: float`; message states both the requested wait and the cap in seconds. | A3 |
| `retry_after_seconds(response: httpx.Response, now: datetime \| None = None) -> float \| None` | Parse `Retry-After`: seconds matching `^\d+(\.\d+)?$` after stripping (so `nan`, `inf`, `1e3` and `-5` are not seconds), or an HTTP date (`email.utils.parsedate_to_datetime`) measured from `now` (default: current UTC time), a past date giving `0.0`. A parsed date without a timezone (a `-0000` zone) is taken as UTC. Absent, empty, negative or unparseable → `None`. A naive `now` → `ValueError`. | A3 |
| `backoff_delay(retry: int, policy: RetryPolicy, rng: random.Random \| None = None) -> float` | Delay before retry number `retry` (1-based): `cap = min(policy.max_delay, policy.base_delay * 2 ** (retry - 1))`, returned as `cap / 2 + rng.uniform(0, cap / 2)` ("equal jitter": the floor grows exponentially, the value never exceeds the cap). `rng` defaults to the `random` module. `retry < 1` → `ValueError`. Overflow of `2 ** (retry - 1)` for huge `retry` must not raise: compute the cap so it saturates at `max_delay`. | A2 |
| `request_with_retry(method: str, url: str, *, client: httpx.Client \| None = None, policy: RetryPolicy \| None = None, params: Mapping[str, str \| int \| float] \| None = None, headers: Mapping[str, str] \| None = None, content: bytes \| None = None, json: object = None, timeout: float \| None = None, sleep: Callable[[float], None] = time.sleep) -> httpx.Response` | Send, retrying per policy (default `RetryPolicy()`). Returns the first 2xx response. A non-2xx status outside `RETRY_STATUSES` raises `httpx.HTTPStatusError` (via `raise_for_status`) on that attempt. A client it creates (`httpx.Client(timeout=DEFAULT_TIMEOUT, follow_redirects=True)`; the async wrapper likewise with `httpx.AsyncClient`) is closed before returning or raising; a passed client is left open. `timeout`, when not `None`, is passed per request. | A1–A4 |
| `async def async_request_with_retry(method: str, url: str, *, client: httpx.AsyncClient \| None = None, policy: RetryPolicy \| None = None, params: Mapping[str, str \| int \| float] \| None = None, headers: Mapping[str, str] \| None = None, content: bytes \| None = None, json: object = None, timeout: float \| None = None, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> httpx.Response` | The same, async; awaits `sleep`. | A5 |
| `main() -> None` | Showcase: a `MockTransport` that answers 503 with `Retry-After: 1`, then 200; prints the attempts and the delays slept (sleep replaced by a recorder so it runs instantly); then a 404 showing no retry. | — |

Decision rules shared by both wrappers (one private function):

1. A transport exception in `RETRY_EXCEPTIONS` or a status in `RETRY_STATUSES` is retryable;
   anything else ends the call (2xx returned, other status raised, other exception
   propagated unchanged).
2. If the attempt was the last (`attempt == policy.max_attempts`), raise
   `RetriesExhaustedError` — no wait, no holdoff check.
3. Otherwise, for a retryable **response**, the holdoff is `policy.holdoff_reader(response)` when
   that is set and returns a value, else `retry_after_seconds(response)`. The caller's
   function wins because it encodes source-specific knowledge the generic header does not.
   A holdoff `> policy.max_holdoff` raises `HoldoffTooLongError`; a holdoff `<= max_holdoff`
   is slept as is (it is **not** capped by `max_delay` — the server's word beats the
   policy's guess). Negative values from the caller's function are treated as `0.0`; a non-finite value (NaN or infinity) is treated as no value, falling back to `retry_after_seconds`.
4. With no holdoff (transport errors always), sleep `backoff_delay(attempt, policy)`.

`readers.py` (imports: `csv`, `io`, `json`, `zipfile`, `xml.etree.ElementTree as ET`,
`typing.Literal`, `httpx`):

| Signature | Purpose | Covers |
|---|---|---|
| `type Format = Literal["json", "xml", "csv", "zip"]` | The formats. | A6 |
| `FORMATS: tuple[Format, ...] = ("json", "xml", "csv", "zip")` | For validation and showcase comments. | A6 |
| `type JsonValue = dict[str, JsonValue] \| list[JsonValue] \| str \| int \| float \| bool \| None` | What `read_json` returns. | A6 |
| `type Parsed = JsonValue \| ET.Element \| list[dict[str, str]] \| dict[str, Parsed] \| bytes` | What dispatching readers return. | A6 |
| `class ParseError(ValueError)` (not `ReadError`, which would shadow `httpx.ReadError`) — `__init__(self, fmt: Format \| None, message: str) -> None` | Any parse failure. Attribute `format: Format \| None` (`None` when the format could not be determined); message begins with the format name. The underlying error is chained. | A6 |
| `read_json(data: bytes) -> JsonValue` | `json.loads`. Empty or malformed → `ParseError("json", ...)`. | A6 |
| `read_xml(data: bytes) -> ET.Element` | `ET.fromstring`; returns the root. Malformed or empty → `ParseError("xml", ...)`. | A6 |
| `read_csv(data: bytes, *, delimiter: str \| None = None, encoding: str = "utf-8-sig") -> list[dict[str, str]]` | Rows as dicts keyed by the header row. `delimiter=None` sniffs among `,`, `;` and tab with `csv.Sniffer`, falling back to `,` when sniffing fails (a one-column file). Empty input or header only → `[]`. A row with more or fewer fields than the header, or bytes that do not decode → `ParseError("csv", ...)` naming the row number; a duplicate header name → `ParseError("csv", ...)` naming it; an encoding Python does not know → `ParseError("csv", ...)` naming it. | A6 |
| `read_zip(data: bytes) -> dict[str, Parsed]` | `{member name: parsed content}` in archive order, directory entries skipped. Each member goes through `format_from_filename`; a known format is parsed with `read_bytes` (so a nested `.zip` recurses), an unknown one is returned as `bytes`. Not a zip → `ParseError("zip", ...)`; a malformed member → `ParseError` with that member's format and its name in the message. | A6 |
| `format_from_content_type(content_type: str) -> Format \| None` | Media type, case-insensitive, parameters ignored: `application/json`, `text/json`, `*/*+json` → json; `application/xml`, `text/xml`, `*/*+xml` → xml; `text/csv`, `application/csv` → csv; `application/zip`, `application/x-zip-compressed`, `application/x-zip` → zip; anything else → `None`. | A6 |
| `format_from_filename(name: str) -> Format \| None` | By extension, case-insensitive: `.json`, `.xml`, `.csv`, `.zip`; else `None`. | A6 |
| `read_bytes(data: bytes, fmt: Format) -> Parsed` | Dispatch to the reader for `fmt`. An `fmt` not in `FORMATS` → `ValueError` naming it. | A6 |
| `read_response(response: httpx.Response, fmt: Format \| None = None) -> Parsed` | `fmt` given → use it; else `format_from_content_type` of the `Content-Type` header, and `ParseError(None, ...)` naming the content type when it gives `None`. For csv, a `charset` parameter in `Content-Type` is passed as `encoding`. | A6 |
| `main() -> None` | Showcase: one JSON, one CSV with `;`, one in-memory ZIP holding an XML and a CSV, and a response read by its `Content-Type`. | — |

`chunking.py` (imports: `collections.abc.Awaitable, Callable`, `datetime`):

| Signature | Purpose | Covers |
|---|---|---|
| `date_windows(start: datetime, end: datetime, span: timedelta) -> list[tuple[datetime, datetime]]` | Contiguous half-open windows covering `[start, end)`. Stepping is done in UTC so each window is at most `span` of elapsed time even across a DST change; window boundaries are returned in `start`'s timezone, except the final end, which is `end` itself. Naive `start` or `end` (no `tzinfo` or `utcoffset()` is `None`), `start >= end`, or `span <= timedelta(0)` → `ValueError` naming the value. | A7 |
| `fetch_chunked[T](fetch: Callable[[datetime, datetime], T], start: datetime, end: datetime, span: timedelta) -> list[T]` | Call `fetch(window_start, window_end)` per window, in order; results in window order. Validation errors from `date_windows` are raised before any call; an exception from `fetch` propagates and no later window is fetched. | A8 |
| `async def async_fetch_chunked[T](fetch: Callable[[datetime, datetime], Awaitable[T]], start: datetime, end: datetime, span: timedelta) -> list[T]` | The same, awaiting each window before starting the next (sequential by design — see Out of scope). | A8 |
| `main() -> None` | Showcase: a 2.5-year Copenhagen-time period split by 365 days, a short range crossing the October DST change split by one day, and a fetch function that returns a string per window. | — |

`utils/__init__.py` re-exports every public name from the three tables above (not `main`).

### Implementation guide

1. `pyproject.toml`: `dependencies = ["httpx>=0.28,<0.29"]`, dev extra `"tzdata"`; `pip install -e ".[dev]"`.
2. `src/energydata/utils/__init__.py` (empty docstring module first, re-exports last).
3. `retry.py`: constants, `RetryPolicy` with validation, the error classes, `retry_after_seconds`,
   `backoff_delay`, the private decision function, then `request_with_retry` and
   `async_request_with_retry` as loops over it, then `main`.
4. `readers.py`: `Format`, `JsonValue`, `Parsed`, `ParseError`, the four readers,
   the two `format_from_*` functions, `read_bytes`, `read_response`, then `main`.
5. `chunking.py`: `date_windows`, the two fetchers, `main`.
6. Re-exports in `utils/__init__.py`.
7. `STRUCTURE.md` (tree line for `utils/`, one line per new module and test path) and
   `.claude/rules/structure-utils.md` (the signature tables).
8. Run each `python -m energydata.utils.<module>`.

### Test intents

| # | Must prove | Covers |
|---|---|---|
| T1 | Each status in `RETRY_STATUSES` and each exception class in `RETRY_EXCEPTIONS` is retried and a later 2xx is returned; the number of requests made equals failures + 1. | A1 |
| T2 | 400, 401, 403, 404, and a 3xx from a passed client that does not follow redirects, raise `httpx.HTTPStatusError` after exactly one request; an exception outside `RETRY_EXCEPTIONS` propagates after one request. | A1 |
| T3 | `backoff_delay` stays within `[cap/2, cap]` for each retry, its floor doubles until it saturates at `max_delay`, huge `retry` values do not overflow, and a seeded `rng` is deterministic; the delays the wrapper sleeps follow it. | A2 |
| T4 | `RetryPolicy` rejects each invalid field with a `ValueError` naming the value, and accepts the boundaries (`max_attempts=1`, `base_delay=0`, `max_delay == base_delay`). | A2, A3 |
| T5 | `Retry-After` in seconds, as a future HTTP date, as a past date (→ 0), and absent/garbage/negative (→ backoff used) — the slept value matches in each case; a caller holdoff function overrides the header, and returning `None`, NaN or infinity falls back to it; `nan`, `inf`, `1e3`, `-5` headers fall back to backoff; a `-0000`-zone date is read as UTC; a naive `now` raises `ValueError`. | A3 |
| T6 | A holdoff above `max_holdoff` raises `HoldoffTooLongError` stating the wait, without sleeping and without another request; a holdoff equal to the cap is slept. | A3 |
| T7 | Exhaustion raises `RetriesExhaustedError` with the right `attempts`, with `response` set for a status and `exception` set (and chained) for a transport error; no sleep after the final attempt; `max_attempts=1` makes one request. | A4 |
| T8 | Every T1–T7 scenario run through `async_request_with_retry` produces the same requests, sleeps and outcome as the sync wrapper; with the default `sleep` and a `Retry-After: 0.05`, a concurrently scheduled task makes progress while `async_request_with_retry` waits (real `asyncio.sleep`, proving the loop is not blocked). | A5 |
| T9 | A created client is closed and a passed client is left open — the created-client path is driven by monkeypatching `httpx.HTTPTransport.handle_request` / `httpx.AsyncHTTPTransport.handle_async_request` to return canned responses, closure asserted by spying on `close` / `aclose`; the created client follows redirects; `params`, `headers`, `content`, `json` and `timeout` reach the request. | A1 |
| T10 | Each reader parses valid input (including non-ASCII, BOM-prefixed CSV, `;` and tab CSV, one-column CSV, nested zip, unknown-extension member as bytes, directory entries skipped) and raises `ParseError` with the right `format` on empty, truncated or malformed input, including ragged CSV rows, duplicate CSV headers and an unknown `charset`. | A6 |
| T11 | `format_from_content_type` and `format_from_filename` map each listed type and extension, ignore case and parameters, and return `None` otherwise; `read_response` uses the header, honours an explicit `fmt` over it, raises `ParseError(None, ...)` when it cannot tell, and `read_bytes` rejects an unknown format. | A6 |
| T12 | `date_windows` covers `[start, end)` exactly with contiguous windows of at most `span`, including exact multiples, a span longer than the range, a DST-crossing range in `Europe/Copenhagen`, and mixed timezones; each invalid input raises `ValueError`. | A7 |
| T13 | Both chunked fetchers call once per window in order, return results in order, stop at the first exception, and make no call when validation fails. | A8 |
| T14 | No test opens a socket: an autouse fixture in `tests/test_retry.py` (the only suite that builds clients) makes the real `httpx.HTTPTransport.handle_request` and `httpx.AsyncHTTPTransport.handle_async_request` fail unless a test overrides them; `readers` and `chunking` tests build `httpx.Response` objects directly; `pyproject.toml` lists exactly one runtime dependency, `httpx`. | A9 |

Coverage check: every criterion A1–A9 has at least one Public API entry (A9 is covered by
`pyproject.toml` and the mock-only design rather than an API entry) and at least one test
intent; every Public API entry maps to a criterion except the three `main` showcases,
which the conventions require.

### Risks

- **`type` aliases and mypy.** PEP 695 recursive aliases (`JsonValue`, `Parsed`) need a
  recent mypy; the pinned `mypy>=2.3` supports them. If mypy still rejects the recursion,
  fall back to `TypeAlias` with string forward references — a signature-preserving change,
  record it as a deviation, do not halt.
- **`csv.Sniffer` is unreliable** on short or one-column input. The fallback to `,` is the
  plan; if sniffing picks a wrong delimiter on a realistic two-column sample in testing,
  restrict `Sniffer.sniff` to the three candidates (already planned) before anything else.
  Not a halt.
- **DST and elapsed-time stepping.** `date_windows` steps in elapsed time, as A7 requires, so
  a Copenhagen-midnight start with a whole-day span lands boundaries at 23:00 local after
  the autumn change. A date-based caller (Eloverblik takes dates) must normalise boundaries
  itself or pick a span with margin. The showcase prints a DST-crossing case so this is
  visible. Local-midnight stepping would break A7 — a section 1 change, so halt rather
  than switch.
- **Test-file names collide later** when source subpackages add modules of the same name
  (e.g. two `client.py`). Out of this round; noted for `DEVELOPMENT.md` at step 8.
- **Anything that would change whether a status is retried, what a holdoff above the cap
  does, or make the async chunked fetch concurrent is a section 1 change — halt.**

## 3. Implementation notes

> Written in step 3. Only deviations from the plan above, each with its reason. "Built as
> planned" is a complete and good entry. On a fix round, also the reproduction test's red
> run, pasted here before the fix was written — step 6 cites it.

Built as planned, with these small departures (signatures in the Public API table are unchanged):

- `RetryError` takes a leading `message: str` (`RetryError(message, attempts, response)`), so
  the two subclasses share one constructor path; the subclass signatures are as planned.
  `ParseError` also carries a `detail` attribute (message without the format prefix) so
  `read_zip` can re-wrap a member's error without doubling the prefix.
- `RetryPolicy` validation uses negated comparisons, so NaN settings are rejected too.
- A `Retry-After` of hundreds of digits parses to `inf` and so raises `HoldoffTooLongError`.
- `read_response` passes `response.charset_encoding` to `read_csv` also when `fmt="csv"` is
  given explicitly.
- Step 5 fixes (signatures unchanged; see section 5 for the findings): `RetryPolicy` also
  rejects non-finite `base_delay`, `max_delay` and `max_holdoff`; `date_windows` compares
  `start`/`end` as UTC instants; `read_response` maps a `utf-8` charset to `utf-8-sig` for
  CSV; `read_xml` also catches `LookupError`; `read_csv` parses with `strict=True` and
  rejects a delimiter that is not one character with `ParseError`; `read_zip` catches
  `ValueError`, `struct.error` and the other errors a damaged archive can raise.
- `STRUCTURE.md` names the utils modules and tests in prose; the signature tables are in
  `.claude/rules/structure-utils.md`.

---

## 4. Verification log

> Written in step 4: the static half. Command output, not a summary of it.

| Check | Result |
|---|---|
| `ruff check .` | `All checks passed!` (exit 0) |
| `ruff format --check .` | `44 files already formatted` (exit 0) |
| `mypy` | `Success: no issues found in 15 source files` (exit 0) |
| `pytest` | `311 passed` — the existing suite only; the three new test files are step 5's |
| Plan completeness | every signature in the Public API table exists as written |
| `STRUCTURE.md` | in sync |
| `python -m energydata.utils.<module>` | retry, readers, chunking each exit 0 with readable output |

**Plan completeness.** Compared with `inspect.signature` and the source: all 3 retry constants, `HoldoffFn`, `RetryPolicy` (fields and defaults), the three error classes, `retry_after_seconds`, `backoff_delay`, `request_with_retry`, `async_request_with_retry`, the readers' `Format`, `FORMATS`, `JsonValue`, `Parsed`, `ParseError`, all eight functions, `date_windows`, `fetch_chunked`, `async_fetch_chunked` and the three `main` match the plan's names, parameter names, defaults, annotations and return types. Missing: none. Unplanned: none (the `RetryError` leading `message` and `ParseError.detail` are recorded in section 3). Deviations: only those in section 3, already recorded. `utils/__init__.py` re-exports exactly the public names, not `main`.

**structure-auditor (before step 4).** Reported `STRUCTURE.md` and `.claude/rules/structure-utils.md` match the code; no edits needed. Its three optional completeness notes (zip directory skipping and nested recursion, `read_bytes` `ValueError`, CSV charset from `Content-Type`) are behaviour detail the tables already cover in purpose; none applied. The three absent test paths are deliberate until step 5.

**Showcases.** Each shows the `RuntimeWarning` the rules call expected. One edit: `chunking.main` bound the zone name inline and printed its second case unlabelled; it now binds `zone_name` with a comment and prints a `windows across the DST change` heading. `retry.main` and `readers.main` were already in the required form.

---

## 5. Test log

> Written in step 5: the dynamic half.

Designer reports: `input-space` and `contract`, both merged. Full suite after this step:
803 passed (311 existing + 492 new: 297 retry, 152 readers, 43 chunking); `ruff check`,
`ruff format --check`, `mypy` clean; the three `python -m energydata.utils.<module>`
showcases exit 0.

| Intent | Test names (in `tests/test_retry.py` unless noted) | Result |
|---|---|---|
| T1 | `test_each_retried_status_is_retried_then_the_success_is_returned`, `test_each_retried_exception_is_retried_then_the_success_is_returned`, `test_a_2xx_status_is_returned_on_the_first_attempt`, `test_retry_constants_name_the_documented_sets` | pass |
| T2 | `test_other_error_statuses_raise_after_one_request` (incl. 408, 425, 499, 501, 505), `test_a_redirect_not_followed_by_the_client_raises_after_one_request`, `test_an_exception_outside_the_retry_set_propagates_after_one_request` | pass |
| T3 | `test_backoff_delay_*` (floor doubling and saturation, bounds, huge retry, zero base, retry < 1, seeded rng, default rng), `test_the_wrappers_sleep_the_backoff_delays`, `test_transport_errors_are_slept_with_backoff_not_a_holdoff_reader` | pass |
| T4 | `test_retry_policy_rejects_invalid_settings_and_names_the_value`, `test_retry_policy_accepts_the_boundary_values`, `test_retry_policy_defaults_match_the_documented_ones`, `test_retry_policy_is_frozen` | pass |
| T5 | `test_retry_after_seconds_*` (numbers, unusable values, three HTTP date forms, `-0000`, past date, other-zone `now`, naive `now`, huge digits, duplicate header), `test_the_retry_after_header_replaces_the_computed_delay`, `test_a_holdoff_is_not_capped_by_max_delay`, `test_a_retry_after_date_is_waited_for_with_the_current_clock`, `test_a_past_retry_after_date_sleeps_zero_not_the_backoff`, `test_an_unusable_retry_after_falls_back_to_the_backoff`, `test_a_holdoff_reader_overrides_the_header_and_bad_values_fall_back`, `test_a_holdoff_reader_can_read_the_body` | pass |
| T6 | `test_a_holdoff_over_the_cap_raises_without_sleeping_or_retrying`, `test_a_holdoff_equal_to_the_cap_is_slept`, `test_a_holdoff_just_over_the_cap_raises`, `test_a_holdoff_of_hundreds_of_digits_raises_as_too_long`, `test_a_reader_holdoff_over_the_cap_raises`, `test_a_zero_cap_still_allows_a_zero_holdoff`, `test_the_last_attempt_is_exhaustion_not_a_holdoff_error`, `test_a_too_long_holdoff_before_the_last_attempt_raises_on_that_attempt` | pass |
| T7 | `test_exhausted_by_status_sets_the_response_and_not_the_exception`, `test_exhausted_by_a_transport_error_sets_and_chains_the_exception`, `test_exhaustion_reports_the_last_outcome_when_the_kinds_alternate`, `test_a_single_attempt_policy_*`, `test_success_on_the_last_allowed_attempt_is_returned`, `test_both_errors_share_a_base_and_are_not_httpx_errors` | pass |
| T8 | every scenario above is parametrised over `sync`/`async` (`KINDS`); `test_sync_and_async_wrappers_behave_identically` (10 scenarios compared on result, error, attempts, requests and sleeps), `test_the_async_wrapper_does_not_block_the_event_loop_while_waiting`, `test_the_async_wrapper_awaits_the_sleep_it_is_given` | pass |
| T9 | `test_a_created_client_is_closed_*` (success, every way out, reader raising, sleep interrupted, async cancellation), `test_a_created_client_follows_redirects`, `..._uses_the_default_timeout`, `..._receives_a_per_request_timeout`, `test_a_passed_client_is_left_open_on_every_exit` (sync and async), `test_every_attempt_resends_the_same_request`, `test_raw_content_reaches_the_request`, `test_a_per_request_timeout_is_forwarded_even_when_zero`, `test_the_showcase_runs` | pass |
| T10 | `tests/test_readers.py`: `test_read_json_*`, `test_read_xml_*`, `test_read_csv_*` (BOM, `;`/tab, one column, ragged rows, duplicate headers, charset, truncated quote, delimiter, size limit), `test_read_zip_*` (nested, directories, unknown extension, corrupt member, damaged-archive sweep) | pass |
| T11 | `tests/test_readers.py`: `test_format_from_content_type_*`, `test_format_from_filename_*`, `test_read_bytes_*`, `test_read_response_*` | pass |
| T12 | `tests/test_chunking.py`: `test_date_windows_*` (coverage and contiguity, exact multiple, span longer, DST autumn and spring, mixed zones, repeated hour, invalid input) | pass |
| T13 | `tests/test_chunking.py`: `test_fetch_chunked_*` (sync and async via `RUNNERS`), `test_async_fetch_chunked_never_overlaps_windows`, `test_async_fetch_chunked_validation_error_surfaces_on_await` | pass |
| T14 | autouse `_no_network` in `tests/test_retry.py` plus `test_the_no_network_guard_refuses_a_real_transport`; `tests/test_readers.py::test_httpx_is_the_only_runtime_dependency` | pass |

### Findings the designers called contradictions, and what was decided

Each was run before anything was changed. All fixes stay inside section 1's criteria; no case needed a halt.

| Finding | Verified by running | Decision |
|---|---|---|
| `date_windows` compared `start >= end` by wall clock, so a range inside the repeated autumn hour was refused when forward and returned `[]` when backward | yes: `[]` for backward, `ValueError` for forward | **Bug, fixed** (A7: `start >= end` raises, windows cover exactly `[start, end)`): compared as UTC instants. Tests: `test_date_windows_accept_a_forward_range_inside_the_repeated_hour`, `..._reject_a_backward_range_...`, and the fetchers' no-call case |
| `read_response` kept a BOM with `charset=utf-8` | yes: key `'\ufeffa'` | **Bug, fixed** (A6: the same bytes parse the same through `read_csv` and `read_response`): `utf-8`/`utf8` charsets are read as `utf-8-sig` |
| `read_xml` let `LookupError` escape for an unknown declared encoding | yes | **Bug, fixed** (A6: a malformed body raises an error naming the format): `LookupError` caught as `ParseError("xml")` |
| `read_csv` accepted a truncated quoted field | yes: `[{"a": "1", "b": "2"}]` | **Bug, fixed** (A6; T10 lists truncated input): `csv.reader(strict=True)` |
| `read_csv` raised `TypeError` for a delimiter that is not one character | yes | **Bug, fixed**: `ParseError("csv")` naming the delimiter. A6 covers malformed input; the delimiter was never a decided public case, but a raw `TypeError` contradicts the function's `Raises:` and the fix adds no behaviour |
| `read_zip` let `ValueError` and `NotImplementedError` escape (damaged archives; found by a byte-flip sweep) | yes: `ValueError: negative seek value`, `NotImplementedError: zip file version 23.5` | **Bug, fixed** (A6): one `_ZIP_ERRORS` tuple (adds `ValueError`, `struct.error`) used for opening and reading. The sweep over every truncation and byte flip is a test |
| `RetryPolicy` accepted infinite delays and holdoffs, so `time.sleep(inf)` raised `OverflowError` and `asyncio.sleep(inf)` hung | yes: `backoff_delay` returned `inf` | **Bug, fixed**: non-finite `base_delay`, `max_delay` and `max_holdoff` rejected with `ValueError` naming the value. Does not touch A1-A5: the settings stay configurable over every finite value. A user wanting "no cap" passes a large number |
| `ProxyError` is not retried although A1 says "connection errors" | n/a (reading) | **Recorded, not changed.** `RETRY_EXCEPTIONS` is the plan's explicit tuple and adding to it changes what is retried, which section 2's Risks name as a halt trigger; pinned by `test_an_exception_outside_the_retry_set_propagates_after_one_request` (includes `ProxyError`). Candidate for `DEVELOPMENT.md` at step 8 |
| `read_json` accepts `NaN`/`Infinity` | yes | **Recorded, not changed**: stdlib leniency, not covered by section 1; pinned by `test_read_json_accepts_nan_as_a_float` |
| `read_csv` "row N" counts records (header and blank lines) | yes | **Recorded, not changed**; pinned by the ragged-row case with a blank line |
| Plan text says seconds regex `\d`, code uses `[0-9]` | yes | **Code kept** (rejects fullwidth digits, which are not seconds); pinned by `test_retry_after_seconds_returns_none_for_anything_unusable` with `５` |
| Duplicate `Retry-After` headers fall back to backoff (httpx joins as `"5, 5"`) | yes | **Recorded, not changed**; pinned by `test_retry_after_seconds_gives_none_for_a_duplicated_header` |
| A holdoff over the cap on the last attempt is exhaustion, a reader returning `inf` is "no value" but a header of hundreds of digits is `inf` | by reading | **Consistent with plan rules 2 and 3**; pinned by `test_the_last_attempt_is_exhaustion_not_a_holdoff_error` and the reader-parameter cases |

Wrong expectations of mine, corrected on the record: the autumn DST range of 24 to 28 October
is five windows, not four (97 elapsed hours because the 25th has 25); a `Retry-After` of `５`
cannot be put in an httpx header as ASCII, so that case builds its headers with `utf-8`; a
ragged `a;b / 1;2;` row cannot be sniffed as `;` from so small a sample, so that case passes
`delimiter=";"` explicitly (`test_read_csv_trailing_delimiter_makes_a_ragged_row`).

Edge cases considered and deliberately skipped, with reasons:

- JSON declared in a non-UTF-8 `charset`: `test_read_response_json_ignores_the_charset_and_requires_utf8` pins today's behaviour (RFC 8259); no change.
- Duplicate ZIP member names: pinned as last-wins; section 1 says nothing.
- ZIP bombs and huge decompression: out of scope in section 1.
- `max_attempts` given a float or NaN: outside the `int` type; not guarded at run time.
- Real concurrency of `async_fetch_chunked`: only that no two windows overlap is tested; throughput is not.
- Real sockets, DNS and TLS: forbidden by T14; the transports are mocked.

---

## 6. Concept check

> Written in step 6, against section 1 — not against section 2. The question is whether
> the thing built is the thing agreed, not whether it matches the plan.

| # | Criterion | Met | Evidence |
|---|---|---|---|
| A1 | `partially` | Statuses and `RETRY_STATUSES`, timeouts (`TimeoutException`) and `NetworkError` (connect, read, write, close) are retried, other statuses raised on the first attempt: `retry.py` `RETRY_*`, `request_with_retry`; tests `test_each_retried_status_is_retried_then_the_success_is_returned`, `test_each_retried_exception_is_retried_then_the_success_is_returned`, `test_other_error_statuses_raise_after_one_request`. Whether `httpx.ProxyError` (a `TransportError` outside `NetworkError`, "an error occurred while establishing a proxy connection") is a "connection error" is not decided by A1 — see Halted. |
| A2 | yes | `backoff_delay` (`retry.py`): cap `min(max_delay, base*2**(n-1))`, value in `[cap/2, cap]`; `RetryPolicy` fields configurable; `test_backoff_delay_*`, `test_the_wrappers_sleep_the_backoff_delays`. |
| A3 | yes | `retry_after_seconds` (seconds, HTTP date), `holdoff_reader` wins, a holdoff is slept uncapped by `max_delay`, over `max_holdoff` raises `HoldoffTooLongError` naming wait and cap: `test_the_retry_after_header_replaces_the_computed_delay`, `test_a_holdoff_over_the_cap_raises_without_sleeping_or_retrying`, `test_a_holdoff_equal_to_the_cap_is_slept`; showcase prints `slept: [1.0]`. |
| A4 | yes | `RetriesExhaustedError(attempts, response, exception)`, one specific type with attempts and last response/exception: `test_exhausted_by_status_sets_the_response_and_not_the_exception`, `test_exhausted_by_a_transport_error_sets_and_chains_the_exception`. |
| A5 | yes | Both wrappers loop over the one `_retry_delay`; `test_sync_and_async_wrappers_behave_identically`, `test_the_async_wrapper_does_not_block_the_event_loop_while_waiting` (real `asyncio.sleep`). |
| A6 | yes | `readers.py`; `test_read_*`, `test_format_from_*`, `test_read_response_*` (152 tests); `ParseError` message begins with the format name; showcase output run. |
| A7 | yes | `date_windows`; `test_date_windows_*` incl. DST, exact multiple, invalid input; showcase output shows contiguous windows. |
| A8 | yes | `fetch_chunked`, `async_fetch_chunked` sequential; `test_fetch_chunked_*`, `test_async_fetch_chunked_never_overlaps_windows`. |
| A9 | yes | autouse `_no_network` guard plus `test_the_no_network_guard_refuses_a_real_transport`; `test_httpx_is_the_only_runtime_dependency`; full suite run here: 803 passed, `ruff check` and `mypy` clean. |

Drift found, and what was done about it:

- Out of scope, surface, connections: nothing from the exclusion list was built (no auth, URLs, DataFrames, throttling, caching, size limits; async chunking is sequential). No caller uses the package yet, as the concept says. No public API beyond the three parts.
- Showcases: all three run, exit 0, read as inputs/call/output examples.
- Structure: the structure-auditor's two wording fixes applied to `.claude/rules/structure-utils.md` (async wrapper's real signature; the Tests section naming what each suite checks). `STRUCTURE.md` needed none.
- **`httpx.ProxyError` against A1: unresolved, halted.** A1 lists "connection errors". A `ProxyError` is raised while establishing the connection to a proxy, which reads as a connection error, yet it is also what a misconfigured proxy or a 407 produces, which a retry cannot cure; the concept names neither. `RETRY_EXCEPTIONS` (section 2) excludes it and step 5 pinned that. Deciding either way changes what is retried, which is section 1's call.

### Earlier rounds still hold

> Later rounds only. Re-check every acceptance criterion from every earlier round in this
> folder: this round changed code they depend on, and their tests passing is necessary but
> not sufficient — a criterion can be satisfied by tests that no longer describe what the
> feature does.

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

> Written in step 8. Only follow-ups that are critical and belong to this work, which most
> rounds do not have: replace the table with `None.` when there are none. Lesser ideas are
> one-line notes in `DEVELOPMENT.md`, not rows here. Not bugs in what this round built —
> those go back through `/build` before the pull request. A critical defect outside what
> section 1 promised, such as a class member it put out of scope, is a recommendation here,
> and its round opens through `/fix`.

| # | Recommendation | Why it is critical | Effort | Decision |
|---|---|---|---|---|
| R1 | | | | |

Decisions: `deferred`, `rejected`, or `next round` — a new numbered file in this folder,
taken back through steps 1 to 7 on the same branch.

---

## 9. Pull request

> Written in step 9, in the commit that opens the pull request — so the URL is not known
> yet and the pull request is found from the branch. The review itself is recorded on the
> pull request thread, not here: this file is `done` from step 9 on.

| Field | Value |
|---|---|
| URL | opened by step 9 — see the branch's pull request |
| Opened as | ready for review |

---

## Halted

> Only if the build stopped. Written by `/build`: the step, the reason verbatim from the
> step that halted, the question for the user — and, once answered, the answer and what
> changed because of it. Never deleted; it is the record of where the plan was thinner
> than the code needed.

### Step 6, concept check: is `httpx.ProxyError` a "connection error" under A1?

Reason, as found: A1 says the wrapper "retries connection errors, timeouts, HTTP 429 ...". The built `RETRY_EXCEPTIONS` is `TimeoutException`, `NetworkError`, `RemoteProtocolError`; `httpx.ProxyError` is a `TransportError` that is none of these, so it propagates on the first attempt. Every other criterion is met.

Question for the user: should a `ProxyError` be retried?
- **Yes**: the failure to open a connection to the proxy is a connection error, and proxies do drop connections transiently. Cost: a permanently wrong proxy setting or proxy credentials is retried up to `max_attempts` times with backoff before failing.
- **No** (as built): A1 stands as is; a proxy error is treated as configuration and fails at once. A1 could then say "network errors" or name the exception classes.
Either answer is a one-line change to A1's wording or to `RETRY_EXCEPTIONS` plus a test; nothing else needs rework. `/build` resumes from step 6 once answered.

**Answer (user, 2026-10-06):** No — do not retry proxy errors. A `ProxyError` is the proxy
refusing the tunnel (typically 407 or 403), which is configuration; an unreachable proxy
already surfaces as `ConnectError` and is retried. Section 1 changed: A1 now reads "network
errors (connection, read, write and close failures, and a dropped connection mid-response)"
and says any other transport error, including `httpx.ProxyError`, is raised on the first
attempt. No code change; the behaviour step 5 pinned is now the agreed behaviour.
