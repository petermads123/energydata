---
paths:
  - "src/energydata/eloverblik/**"
  - "tests/test_eloverblik_*.py"
  - "tests/fixtures/eloverblik_openapi.json"
  - "tests/conftest.py"
---

# Structure: `src/energydata/eloverblik/`

The Eloverblik **Customer API** (`https://api.eloverblik.dk/customerapi/api`; read-only; the
Third-Party API is out of scope). Every call goes through `EloverblikClient`. `__init__.py`
re-exports `BASE_URL`, `REFRESH_TOKEN_ENV`, `DEFAULT_POLICY`, `EloverblikClient`,
`EloverblikError`, `MissingTokenError`, `Aggregation`, `AGGREGATIONS` and the four `get_*`
functions. Every module but `__init__.py` has a `main()` showcase; with no
`ELOVERBLIK_REFRESH_TOKEN` the live ones print one line saying so and return, otherwise they call
the live API; `_common.main()` and the alignment/windows part of `meterdata.main()` run offline.

Point-scoped functions take `metering_point_ids: str | Sequence[str] | None = None` and
`client: EloverblikClient | None = None` (created and closed when absent, a passed one left
open). A string is one id; ids are stripped and must be exactly 18 digits; an empty sequence, a
malformed id or a duplicate is a `ValueError` naming it, before any request; `None` lists the
linked points (`includeAll=false`) and uses all of them in listing order, none linked being a
`ValueError`. Requests are batched at most 10 points each, concurrently through
`gather_ordered`, and per-point results are matched to the requested ids by their `id`. Each result remembers the
`X-Correlation-ID` of the response it arrived in (read through `ApiClient.request_response`),
so every per-point `EloverblikError` carries it: a per-point failure reported in the response,
and any payload error raised while a function parses that point's result.

## `src/energydata/eloverblik/client.py`

| Signature | Description |
|---|---|
| `BASE_URL: str` | `"https://api.eloverblik.dk/customerapi/api"`. |
| `REFRESH_TOKEN_ENV: str` | `"ELOVERBLIK_REFRESH_TOKEN"`, read when no token is passed. |
| `DEFAULT_POLICY: RetryPolicy` | `RetryPolicy(max_attempts=4, base_delay=15.0, max_delay=60.0)`: 429/503 waits grow towards the minute Eloverblik asks for. |
| `EloverblikError(message: str, *, status: int \| None = None, error_code: int \| None = None, error_text: str \| None = None, metering_point_id: str \| None = None, correlation_id: str \| None = None)` | Attributes as the keywords; `str()` is the message only. An API error response, or an exhausted retry or holdoff on a response: `status` the HTTP status, `error_code`/`error_text` from a JSON object body (`errorCode`; `errorText`, else `detail`, else `title`) or from a bare JSON string body, which is how the real API reports an invalid period (`"#30001: Period not allowed, ..."` gives code 30001 and the text after it; a string of another form is the text with no code), `correlation_id` from `X-Correlation-ID`. A per-point failure inside a 200 response: `status` 200, with the code, text, point and the response's `correlation_id`. A 2xx body that is not a JSON object: `status` 200. Payload checks: the other attributes `None`. |
| `MissingTokenError(EloverblikError)` | The first request of a client with no refresh token; the message names `REFRESH_TOKEN_ENV`. |
| `EloverblikClient(refresh_token: str \| None = None, *, policy: RetryPolicy \| None = None, timeout: float = DEFAULT_TIMEOUT, max_concurrency: int = 2, transport: httpx.AsyncBaseTransport \| None = None)` | `ApiClient` on `BASE_URL` with `Accept: application/json`; `policy=None` means `DEFAULT_POLICY`. Token stripped, blank is missing, one that is not printable ASCII without spaces is refused at the first request without being echoed. `repr` is `EloverblikClient(token=set \| missing)`. |
| `EloverblikClient.has_refresh_token: bool` (property) | Whether a usable refresh token is held. |
| `async EloverblikClient.fetch_json(method: Literal["GET", "POST"], path: str, *, params: Mapping[str, str] \| None = None, body: object = None) -> dict[str, JsonValue]` | One authorised request. Fetches the access token on first use under a lock (concurrent calls share one `/token` call), reuses it, and on a 401 refreshes once (only if the cached token is still the rejected one) and retries once; a second 401 raises. A 401/403 from `/token` says the refresh token was rejected. A transport failure stays `RetriesExhaustedError`. Only awaitable on the client's loop. |
| `EloverblikClient.get_json(method, path, *, params=None, body=None) -> dict[str, JsonValue]` | The sync form, through `run`. |
| `main() -> None` | Showcase (live API). |

## `src/energydata/eloverblik/_common.py`

Private module: the shared path of the point-scoped functions — id normalisation, the client
scope, batching, the linked-point default, the batched `POST` and per-point result unpacking,
and payload-shape checks. Results are mapped by `id`: an `id` not requested or a requested id
with no result is an `EloverblikError`; a repeated id keeps all its results. A per-point failure
raises `EloverblikError` with status 200 and the response's correlation id unless its code is
tolerated.

| Signature | Description |
|---|---|
| `BATCH_SIZE: int` | `10`, the most points per request. |
| `main() -> None` | Offline showcase of id normalisation and batching. |

## `src/energydata/eloverblik/meteringpoints.py`, `meterdata.py`, `charges.py`

| Signature | Description |
|---|---|
| `get_metering_points(*, include_unlinked: bool = False, client=None) -> pd.DataFrame` | `RangeIndex`; `metering_point_id`, `type_of_mp`, `settlement_method`, `meter_reading_occurrence`, `balance_supplier_name`, `street_code`, `street_name`, `building_number`, `floor_id`, `room_id`, `city_sub_division_name`, `postcode`, `city_name`, `municipality_code`, `location_description`, `meter_number`, `consumer_start_date` (object, `None` where null), `has_relation`, `is_moved_out` (`bool`), `child_metering_point_ids` (a tuple per row). Consumer names and CPR/CVR left out. |
| `get_metering_point_details(metering_point_ids=None, *, client=None) -> pd.DataFrame` | `RangeIndex`, one row per point in request order; every scalar field of the details object in spec order as snake_case; `power_limit_kw_decimal` is `float64`, the rest object. Nested contact addresses and child points left out. |
| `type Aggregation = Literal["quarter", "hour", "day", "month", "year"]`, `AGGREGATIONS: tuple[Aggregation, ...]` | The supported aggregations. `Actual` is not offered. |
| `get_meter_data(start: TimeLike, end: TimeLike \| None = None, *, aggregation: Aggregation = "hour", metering_point_ids=None, client=None) -> pd.DataFrame` | Index `"time"`, tz-aware Copenhagen interval starts; columns `MultiIndex` named `("metering_point_id", "field")`, points in request order, fields `energy_kwh` (`float64`) then `quality` (object, raw code); NaN where missing. `attrs["aggregation"]`. With `end`, exactly `[start, end)` on the Danish calendar; without, to the last returned interval (empty frame when none). Bounds not on the aggregation, `ValueError`. |
| `get_charges(metering_point_ids=None, *, client=None) -> pd.DataFrame` | `RangeIndex`; `metering_point_id`, `kind`, `price_id`, `name`, `description`, `owner`, `period_type` (object), `valid_from`, `valid_to` (`datetime64[ns, Europe/Copenhagen]`, NaT where null), `price` (`float64`), `quantity`, `position` (`Int64`). Per point: subscriptions, tariffs (a row per price position), fees; a tariff with no prices is one row with NaN price. |
| `main() -> None` | Showcase in each module. |

`get_meter_data` rules: request dates are the Danish dates covering the period (`dateTo`
exclusive, plus a day when `end` is not local midnight; `end=None` requests through tomorrow);
`dateTo` is clamped to tomorrow and `dateFrom` to 1 January five years back, a part wholly
outside is not requested and is NaN. Windows on Danish dates: 730 days for quarter/hour/day, 12
months for month, one year for year, abutting. Per point a series must be kWh (unit compared stripped and case-insensitively) at the asked
resolution, whose label must be text matching exactly (`PT15M`, `PT1H`, `PT1D` (`P1D` also accepted, for `day` only), `P1M`, `P1Y`), else `EloverblikError` naming the point; a
null or absent quantity is NaN with the quality kept; per-point codes `30015`, `30018`, `30010`
and `30016` give NaN for that window, any other raises. The clock is read only through
`pd.Timestamp.now`. The interval holding today may be a partial sum.

## Tests

`tests/fixtures/eloverblik_openapi.json` is the OpenAPI 3.0.4 spec as published on 2026-11-02.
`tests/conftest.py` carries the support: `ELOVERBLIK_REFRESH_TOKEN`, `ELOVERBLIK_POINT_A`,
`ELOVERBLIK_POINT_B`; builders `eloverblik_point_id(number)`, `eloverblik_point(point_id=ELOVERBLIK_POINT_A, **fields)`,
`eloverblik_details(point_id=ELOVERBLIK_POINT_A, **fields)`, `eloverblik_charge(price_id="SUB1", **fields)`,
`eloverblik_tariff(price_id="TAR1", prices=24, **fields)`; and `EloverblikService(points=None, *,
refresh_token=...)`, a mock written from the spec: `/token`, bearer check, `/isalive`, the
listing, `getdetails`, `getcharges` and `gettimeseries` with request-level validation (HTTP
400 ProblemDetails with `errorCode`: 20013, 30000, 30002-30004, 30008, 30014, 30017; 30001, an invalid period, is the bare JSON string `"#30001: Period not allowed, ToDate is before FromDate."` the real API sends), generated CIM data
(`dateTo` exclusive, Danish dates, quality `A04`, DST-correct), served data (`points`,
`unlinked` for `includeAll=true`, `details` and `charges` by point id, property `point_ids`),
a `/token` counter `token_calls`, scripting attributes (`codes`,
`detail_codes`, `charge_codes`, `a02`, `gaps`, `resolution_override`, `unit_override`,
`out_of_order`, `drop_ids`, `repeat_ids`, `extra_ids`, `max_days`, `overrides`), an
`X-Correlation-ID` on every response, every request in `requests` (`calls(suffix)`,
`timeseries_calls()`), `expire_access()`, and `client(...)` building a real client on it; plus
the `eloverblik_service` fixture.

`tests/test_eloverblik_live.py` is the live suite: every test marked `live`, skipped without
`ELOVERBLIK_REFRESH_TOKEN`, deselected by plain `pytest`. One module-scoped client (one
`/token` call); each test writes scrubbed lines to the file named by `ELOVERBLIK_LIVE_REPORT`. The aggregation test records a `quarter` resolution mismatch as the designed refusal and fails on any other; its `year` case also reports each linked point's raw `Year` result (`success`, `errorCode`, scrubbed `errorText`, series, periods, resolutions). Server text the package did not shape has every run of six or more digits redacted as well.
`scrub(text, secrets=()) -> str` removes the secrets and any JWT and shortens every 18-digit
number to `...` plus its last four digits. With `ELOVERBLIK_LIVE_MOCK=1` the same tests run
offline against `EloverblikService` with every real transport refused. The offline suites
(`test_eloverblik_client.py`, `test_eloverblik_meteringpoints.py`,
`test_eloverblik_meterdata.py`, `test_eloverblik_charges.py`, `test_eloverblik_spec.py`) and
`test_eloverblik_common.py` (the shared path, driven through the public functions) all run on
`httpx.MockTransport` with the clock pinned through `pd.Timestamp.now` where dates matter.
`client` covers tokens, the 401 refresh, error mapping, redaction of a token a server echoes and
retries; `meteringpoints`/`charges`/`meterdata` each endpoint's columns, dtypes, nulls and
payload errors (meter data also periods, the history horizon, windows and batches, DST days);
`spec` the package against the OpenAPI fixture, the mock's bodies against its schemas (and its invalid-period 400 as the real bare string, read back as code 30001), the live
suite's offline subprocess run (nothing private in its output), `scrub`, the live suite's private report helpers (body shape, refusal line, which mismatch is the designed one: only `quarter`; digit runs of six or more redacted in raw server text), the live tests themselves driven in-process against the mock (a non-`quarter` mismatch fails the aggregation test, a `quarter` one is recorded, the quarter probe's line per point, the year probe's raw line per point) and the docs. Rules the
tests pin: ids are ASCII digits only; a point listed twice is used once; an error text that
echoes a token (of six characters or more) has it replaced by `<token>`; a series must carry
the id of the result it is in; a missing or unparseable `timeInterval.start`, or a position
beyond what pandas can place, is an `EloverblikError` carrying the point; tariff rows are in
position order (a null position last, `<NA>`); a charge date pandas cannot hold is an
`EloverblikError`.
