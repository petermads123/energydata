"""Live tests of the Eloverblik Customer API; they need a real refresh token.

Every test is marked `live`, so plain `pytest` deselects them. Run them with
`ELOVERBLIK_REFRESH_TOKEN` set and `-m live`. Each test writes report lines to the
file named by `ELOVERBLIK_LIVE_REPORT` (when set): metering point ids appear only
as `...` plus their last four digits, and never a name, an address, a CPR/CVR
number or a token. A failed request is reported with its status, error code,
error text and correlation id, scrubbed by `scrub`.

The whole module shares one client, so a run spends one `/token` call (the API
allows two a minute). With `ELOVERBLIK_LIVE_MOCK=1` the same tests run offline
against `EloverblikService`, with every real transport refused; this is how the
suite itself is tested.

The output may be pasted into a chat: do not add `--showlocals` (`-l`), which
prints every local variable, ids included.
"""

import functools
import os
import re
from collections.abc import Callable, Iterator, Sequence
from datetime import date, timedelta
from types import TracebackType
from typing import cast

import pandas as pd
import pytest
from conftest import (
    ELOVERBLIK_POINT_A,
    ELOVERBLIK_POINT_B,
    EloverblikService,
    block_real_transports,
    eloverblik_point,
)

from energydata.eloverblik import (
    AGGREGATIONS,
    REFRESH_TOKEN_ENV,
    Aggregation,
    EloverblikClient,
    EloverblikError,
    get_charges,
    get_meter_data,
    get_metering_point_details,
    get_metering_points,
)
from energydata.utils import RetryError

MOCK_ENV = "ELOVERBLIK_LIVE_MOCK"
REPORT_ENV = "ELOVERBLIK_LIVE_REPORT"
TZ = "Europe/Copenhagen"

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.environ.get(REFRESH_TOKEN_ENV, "").strip(),
        reason="set ELOVERBLIK_REFRESH_TOKEN to run the live Eloverblik tests",
    ),
]

type Report = Callable[[str], None]

_JWT = re.compile(r"eyJ[\w-]*\.[\w-]+\.[\w-]*")
_LONG_NUMBER = re.compile(r"(?<!\d)(\d{18})(?!\d)")


def scrub(text: str, secrets: Sequence[str] = ()) -> str:
    """`text` with every secret removed and every 18-digit id shortened.

    Each of `secrets` (when not empty) becomes `<token>`, every JWT too, and every
    18-digit number (a metering point id) becomes `...` plus its last four digits.
    """
    for secret in secrets:
        if secret:
            text = text.replace(secret, "<token>")
    text = _JWT.sub("<token>", text)
    return _LONG_NUMBER.sub(lambda m: f"...{m.group(1)[-4:]}", text)


_DIGIT_RUN = re.compile(r"\d{6,}")


def _scrub_raw(text: str) -> str:
    """`scrub`, then every run of six or more digits redacted.

    For server text the package did not shape (an error body, an `errorText`): it
    may carry a CVR or the digits of a CPR. Ids are already shortened to `...` plus
    four digits by then, which this leaves alone.
    """
    return _DIGIT_RUN.sub("<digits>", scrub(text, _secrets()))


def _secrets() -> list[str]:
    return [os.environ.get(REFRESH_TOKEN_ENV, "").strip()]


def _short(point_id: str) -> str:
    """A metering point id as `...` plus its last four digits."""
    return f"...{point_id[-4:]}"


def _describe(error: EloverblikError | RetryError) -> str:
    """What a failed request says, scrubbed."""
    if isinstance(error, EloverblikError):
        facts = (
            f"status={error.status} error_code={error.error_code} "
            f"error_text={error.error_text!r} "
            f"correlation_id={error.correlation_id}"
        )
        return scrub(
            f"a request failed: {type(error).__name__}: {error} [{facts}]", _secrets()
        )
    return scrub(f"a request failed: {type(error).__name__}: {error}", _secrets())


def _refusal(error: EloverblikError) -> str:
    """A report line for an error: status, code, text and the message itself.

    The message names the error's class for errors the package raises itself
    (status, code and text are `None` then), e.g. `returned resolution 'PT1H' for
    aggregation 'quarter'`. Scrubbed and cut to 200 characters.
    """
    message = _scrub_raw(str(error))
    if len(message) > 200:
        message = message[:200] + "..."
    text = _scrub_raw(error.error_text) if error.error_text is not None else None
    if text is not None and len(text) > 200:
        text = text[:200] + "..."
    return (
        f"error status={error.status} code={error.error_code} "
        f"text={text!r} message={message!r}"
    )


def _body_shape(error: EloverblikError) -> str:
    """The content type, top-level keys and a scrubbed excerpt of an error body.

    Read from the HTTP response chained on the error. The whole body is scrubbed
    (tokens, ids, any run of six or more digits) before the excerpt of at most 300
    characters is cut from it.
    """
    cause = error.__cause__
    response = getattr(cause, "response", None)
    if response is None:
        return "no HTTP response is attached to the error"
    content_type = response.headers.get("Content-Type")
    text = response.text
    keys: object = "not a JSON object"
    try:
        parsed = response.json()
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        keys = sorted(str(key) for key in parsed)
    excerpt = _scrub_raw(text)[:300]
    keys = _scrub_raw(str(keys))
    return f"content_type={content_type!r} keys={keys} excerpt={excerpt!r}"


_QUARTER_REFUSAL = re.compile(
    r"returned resolution '[^']+' for aggregation 'quarter', expected"
)


def _is_resolution_refusal(error: EloverblikError) -> bool:
    """Whether `error` is the designed refusal of `quarter` on a coarser point.

    Only that one is designed (A3): the package names the point, the label it got
    (a real text, not a missing one) and the aggregation `quarter`. Any other
    resolution mismatch is a defect and must fail the live test.
    """
    return error.status is None and _QUARTER_REFUSAL.search(str(error)) is not None


def _test_frame(
    entry: TracebackType | None, test: Callable[..., object]
) -> TracebackType | None:
    """The traceback entry of `test` itself, with the frames it called cut off."""
    code = getattr(test, "__code__", None)
    while entry is not None and entry.tb_frame.f_code is not code:
        entry = entry.tb_next
    if entry is not None:
        entry.tb_next = None
    return entry


def _safe_failures[**P, R](test: Callable[P, R]) -> Callable[P, R]:
    """Fail a test with the scrubbed facts of a failed request, not its path."""

    @functools.wraps(test)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return test(*args, **kwargs)
        except (EloverblikError, RetryError) as error:
            failure = AssertionError(_describe(error))
            failure.__suppress_context__ = True
            raise failure.with_traceback(
                _test_frame(error.__traceback__, test)
            ) from None

    return wrapper


@pytest.fixture(scope="module")
def client() -> Iterator[EloverblikClient]:
    """One client for the module, so the run makes one `/token` call."""
    if os.environ.get(MOCK_ENV) == "1":
        with pytest.MonkeyPatch.context() as patch:
            block_real_transports(patch)
            child = "571313100000000099"
            service = EloverblikService(
                [
                    eloverblik_point(
                        ELOVERBLIK_POINT_A,
                        childMeteringPoints=[{"meteringPointId": child}],
                    ),
                    eloverblik_point(ELOVERBLIK_POINT_B),
                ]
            )
            service.refresh_token = os.environ[REFRESH_TOKEN_ENV].strip()
            mock_client = service.client(service.refresh_token)
            try:
                yield mock_client
            finally:
                mock_client.close()
        return
    real = EloverblikClient()
    try:
        yield real
    finally:
        real.close()


@pytest.fixture
def report(request: pytest.FixtureRequest) -> Report:
    """Write a scrubbed line, prefixed with the test name, to the report file."""
    name = request.node.name

    def write(line: str) -> None:
        path = os.environ.get(REPORT_ENV)
        if path:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(scrub(f"[{name}] {line}", _secrets()) + "\n")

    return write


def _energy(frame: pd.DataFrame) -> pd.DataFrame:
    """The `energy_kwh` columns of a meter-data frame, one per point."""
    return pd.DataFrame(frame.xs("energy_kwh", axis=1, level="field"))


def _count(mask: pd.DataFrame) -> int:
    """How many cells of a boolean frame are true."""
    return int(mask.to_numpy().sum())


def _today() -> date:
    return pd.Timestamp.now(TZ).date()


def _linked(client: EloverblikClient) -> pd.DataFrame:
    return get_metering_points(client=client)


def _raw_periods(result: object) -> list[dict[str, object]]:
    """The periods of the first result of a raw `gettimeseries` response."""
    found: list[dict[str, object]] = []
    if not (isinstance(result, list) and result and isinstance(result[0], dict)):
        return found
    document = result[0].get("MyEnergyData_MarketDocument")
    series = document.get("TimeSeries") if isinstance(document, dict) else None
    for item in series if isinstance(series, list) else []:
        periods = item.get("Period") if isinstance(item, dict) else None
        found.extend(p for p in periods or [] if isinstance(p, dict))
    return found


def _first_id(client: EloverblikClient) -> str:
    points = _linked(client)
    if points.empty:
        pytest.skip("no linked metering points")
    return str(points["metering_point_id"].iloc[0])


@_safe_failures
def test_token_is_accepted(client: EloverblikClient, report: Report) -> None:
    payload = client.get_json("GET", "/meteringpoints/meteringpoints")

    assert isinstance(payload.get("result"), list)
    report("the refresh token was accepted and an access token fetched")


@_safe_failures
def test_metering_points(client: EloverblikClient, report: Report) -> None:
    points = get_metering_points(client=client)

    assert list(points.columns)[0] == "metering_point_id"
    report(f"{len(points)} linked metering point(s)")
    for row in points.itertuples(index=False):
        report(
            f"{_short(str(row.metering_point_id))} type={row.type_of_mp} "
            f"settlement={row.settlement_method} reading={row.meter_reading_occurrence} "
            f"relation={row.has_relation} moved_out={row.is_moved_out} "
            f"children={[_short(str(c)) for c in cast('tuple[str, ...]', row.child_metering_point_ids)]}"
        )


@_safe_failures
def test_metering_points_include_unlinked(
    client: EloverblikClient, report: Report
) -> None:
    linked = get_metering_points(client=client)

    everything = get_metering_points(include_unlinked=True, client=client)

    assert len(everything) >= len(linked)
    report(f"{len(linked)} linked, {len(everything)} with unlinked")


@_safe_failures
def test_metering_point_details(client: EloverblikClient, report: Report) -> None:
    linked = _linked(client)

    details = get_metering_point_details(client=client)

    assert len(details) == len(linked)
    report(f"{len(details)} detail row(s), {details.shape[1]} columns")
    for row in details.itertuples(index=False):
        report(
            f"{_short(str(row.metering_point_id))} type={row.type_of_mp} "
            f"unit={row.energy_time_series_measure_unit} "
            f"net_settlement_group={row.net_settlement_group} "
            f"status={row.physical_status_of_mp} "
            f"power_limit_kw={row.power_limit_kw_decimal}"
        )


def _recent_period(aggregation: str) -> tuple[date, date]:
    """A complete, aligned period for `aggregation` in the recent past."""
    today = _today()
    if aggregation in ("quarter", "hour"):
        return today - timedelta(days=4), today - timedelta(days=1)
    if aggregation == "day":
        return today - timedelta(days=14), today - timedelta(days=1)
    this_month = today.replace(day=1)
    if aggregation == "month":
        year, month = this_month.year, this_month.month - 3
        if month < 1:
            year, month = year - 1, month + 12
        return date(year, month, 1), this_month
    return date(today.year - 1, 1, 1), date(today.year, 1, 1)


@pytest.mark.parametrize("aggregation", AGGREGATIONS)
@_safe_failures
def test_every_aggregation_on_a_recent_period(
    client: EloverblikClient, report: Report, aggregation: Aggregation
) -> None:
    start, end = _recent_period(aggregation)
    if aggregation == "year":
        _report_year_raw(client, report, start, end)

    try:
        frame = get_meter_data(start, end, aggregation=aggregation, client=client)
    except EloverblikError as error:
        if not _is_resolution_refusal(error):
            raise
        # The designed refusal (A3): a point metered at another resolution.
        report(f"{aggregation} {start} to {end}: refused, {_refusal(error)}")
        return

    assert str(getattr(frame.index, "tz", None)) == TZ
    assert frame.columns.nlevels == 2
    energy = _energy(frame)
    report(
        f"{aggregation} {start} to {end}: {frame.shape[0]} rows, "
        f"{_count(energy.notna())} values, {_count(energy.isna())} missing, "
        f"{float(energy.to_numpy().sum()):.1f} kWh in total"
    )


@_safe_failures
def test_autumn_dst_day_hourly(client: EloverblikClient, report: Report) -> None:
    today = _today()
    year = today.year
    day = date(year, 10, 31)
    day -= timedelta(days=(day.weekday() + 1) % 7)  # the last Sunday of October
    if day >= today:
        day = date(year - 1, 10, 31)
        day -= timedelta(days=(day.weekday() + 1) % 7)

    frame = get_meter_data(day, day + timedelta(days=1), client=client)

    assert len(frame) == 25
    energy = _energy(frame)
    report(f"{day}: {len(frame)} hourly rows, {_count(energy.isna())} missing")


@_safe_failures
def test_date_to_is_exclusive(client: EloverblikClient, report: Report) -> None:
    point = _first_id(client)
    day = _today() - timedelta(days=5)
    body = {"meteringPoints": {"meteringPoint": [point]}}

    frame = get_meter_data(
        day, day + timedelta(days=1), aggregation="day", client=client
    )
    raw = client.get_json(
        "POST",
        f"/meterdata/gettimeseries/{day}/{day + timedelta(days=1)}/Day",
        body=body,
    )

    assert len(frame) == 1
    report(f"a one-day day request gives {len(frame)} row")
    result = raw.get("result")
    for period in _raw_periods(result):
        points = period.get("Point")
        report(
            f"raw period resolution={period.get('resolution')} "
            f"timeInterval={period.get('timeInterval')} "
            f"points={len(points) if isinstance(points, list) else 0}"
        )


@_safe_failures
def test_quality_codes_seen(client: EloverblikClient, report: Report) -> None:
    today = _today()

    frame = get_meter_data(
        today - timedelta(days=8), today - timedelta(days=1), client=client
    )

    quality = frame.xs("quality", axis=1, level="field").stack().dropna()
    codes = sorted(set(quality))
    report(f"quality codes seen: {codes}")


@_safe_failures
def test_quarter_on_each_point(client: EloverblikClient, report: Report) -> None:
    today = _today()
    start, end = today - timedelta(days=3), today - timedelta(days=2)
    for point in _linked(client)["metering_point_id"]:
        try:
            frame = get_meter_data(
                start,
                end,
                aggregation="quarter",
                metering_point_ids=str(point),
                client=client,
            )
        except EloverblikError as error:
            report(f"{_short(str(point))} quarter: {_refusal(error)}")
        else:
            values = _count(_energy(frame).notna())
            report(f"{_short(str(point))} quarter: {len(frame)} rows, {values} values")


@_safe_failures
def test_charges(client: EloverblikClient, report: Report) -> None:
    charges = get_charges(client=client)

    assert "kind" in charges.columns
    report(f"{len(charges)} charge row(s): {charges['kind'].value_counts().to_dict()}")
    for kind, group in charges.groupby("kind"):
        report(
            f"{kind}: {group['price_id'].nunique()} charge(s), "
            f"price range {group['price'].min()} to {group['price'].max()}"
        )


@_safe_failures
def test_error_body_of_an_invalid_request(
    client: EloverblikClient, report: Report
) -> None:
    point = _first_id(client)
    body = {"meteringPoints": {"meteringPoint": [point]}}

    try:
        client.get_json(
            "POST", "/meterdata/gettimeseries/2026-01-02/2026-01-01/Day", body=body
        )
    except EloverblikError as error:
        report(
            f"invalid request: status={error.status} code={error.error_code} "
            f"text={error.error_text!r} correlation_id_present={bool(error.correlation_id)}"
        )
        report(f"invalid request body: {_body_shape(error)}")
    else:
        report("invalid request: no error was raised")


@_safe_failures
def test_child_metering_points(client: EloverblikClient, report: Report) -> None:
    points = _linked(client)
    children = sorted(
        {child for ids in points["child_metering_point_ids"] for child in ids}
    )
    if not children:
        report("no child metering points")
        return
    day = _today() - timedelta(days=5)
    for child in children:
        try:
            frame = get_meter_data(
                day,
                day + timedelta(days=1),
                aggregation="day",
                metering_point_ids=child,
                client=client,
            )
        except EloverblikError as error:
            report(f"child {_short(child)}: {_refusal(error)}")
        else:
            energy = _energy(frame).to_numpy()
            report(f"child {_short(child)}: {energy[0, 0]} kWh on {day}")


def _report_year_raw(
    client: EloverblikClient, report: Report, start: date, end: date
) -> None:
    """Report the raw per-point `success`/`errorCode` of a `Year` request, per point."""
    for point in _linked(client)["metering_point_id"]:
        body = {"meteringPoints": {"meteringPoint": [str(point)]}}
        raw = client.get_json(
            "POST", f"/meterdata/gettimeseries/{start}/{end}/Year", body=body
        )
        results = raw.get("result")
        for item in results if isinstance(results, list) else []:
            if not isinstance(item, dict):
                continue
            document = item.get("MyEnergyData_MarketDocument")
            series = document.get("TimeSeries") if isinstance(document, dict) else None
            periods = _raw_periods([item])
            report(
                f"year {start} to {end} {_short(str(point))}: "
                f"success={item.get('success')} errorCode={item.get('errorCode')} "
                f"errorText={_scrub_raw(str(item.get('errorText')))!r} "
                f"series={len(series) if isinstance(series, list) else 0} "
                f"periods={len(periods)} "
                f"resolutions={sorted({str(p.get('resolution')) for p in periods})}"
            )


@_safe_failures
def test_730_day_request(client: EloverblikClient, report: Report) -> None:
    point = _first_id(client)
    today = _today()
    body = {"meteringPoints": {"meteringPoint": [point]}}
    path = f"/meterdata/gettimeseries/{today - timedelta(days=730)}/{today}/Day"

    try:
        client.get_json("POST", path, body=body)
    except EloverblikError as error:
        report(f"730 days: error status={error.status} code={error.error_code}")
    else:
        report("730 days: accepted")
