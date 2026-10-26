import json
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from energydata.energidataservice import EnergiDataServiceClient
from energydata.minstroem import MinStroemClient
from energydata.utils import RetryPolicy


def block_real_transports(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every real httpx transport refuse, so nothing can reach the network.

    `httpx.MockTransport` is untouched. Shared by the no-network guard and by the
    plugin that runs the live suite against the mock.
    """

    def refuse_sync(
        _self: httpx.HTTPTransport, request: httpx.Request
    ) -> httpx.Response:
        raise AssertionError(f"test tried to reach the network: {request.url}")

    async def refuse_async(
        _self: httpx.AsyncHTTPTransport, request: httpx.Request
    ) -> httpx.Response:
        raise AssertionError(f"test tried to reach the network: {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse_sync)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse_async)


@pytest.fixture(autouse=True)
def _no_network(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Make every real httpx transport refuse, so no test can reach the network.

    A test marked `live` is the one exception: it exists to call the real API.
    The guard is function-scoped, so a module- or session-scoped fixture is set up
    before it runs and is not covered; no offline fixture here builds a transport.
    """
    if request.node.get_closest_marker("live") is not None:
        return
    block_real_transports(monkeypatch)


MARKETS_FIXTURE = Path(__file__).parent / "fixtures" / "energidataservice_markets.json"
PRICELIST_FIXTURE = (
    Path(__file__).parent / "fixtures" / "energidataservice_pricelist.json"
)
_TIME_FORMAT = "%Y-%m-%dT%H:%M"
# Datasets whose window field is a validity date, which the API compares by date.
_DATE_FIELDS = {"DatahubPricelist": "ValidFrom"}

type MarketRecords = dict[str, list[dict[str, Any]]]


class MarketsService:
    """A mock Energi Data Service for the market datasets.

    Serves stored records the way the API does: a `[start, end)` window on the
    dataset's UTC time field (for `DatahubPricelist`, on the date of `ValidFrom`
    against the date part of `start` and `end`, `end` exclusive), then the `filter` parameter on every field it
    names, then `sort` and the `columns` projection. A filter on a field the
    records lack, or a column they lack, is a 400, as at the real service.
    """

    def __init__(self, data: MarketRecords) -> None:
        """Store the records each dataset serves."""
        self.data = {name: list(records) for name, records in data.items()}
        self.requests: list[httpx.Request] = []
        self.honour_filter = True
        self.respond: Callable[[httpx.Request], httpx.Response | None] | None = None

    def datasets(self) -> list[str]:
        """The dataset of each request, in arrival order."""
        return [request.url.path.rsplit("/", 1)[1] for request in self.requests]

    def params(self, dataset: str | None = None) -> dict[str, str]:
        """The query parameters of the one request made (to `dataset`)."""
        (request,) = [
            r
            for r in self.requests
            if dataset is None or r.url.path == f"/dataset/{dataset}"
        ]
        return dict(request.url.params)

    def filters(self, dataset: str | None = None) -> dict[str, list[str]]:
        """The decoded `filter` parameter of the one request made."""
        params = self.params(dataset)
        decoded: dict[str, list[str]] = json.loads(params["filter"])
        return decoded

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Answer one request."""
        self.requests.append(request)
        if self.respond is not None and (custom := self.respond(request)) is not None:
            return custom
        dataset = request.url.path.rsplit("/", 1)[1]
        params = request.url.params
        start = datetime.strptime(params["start"], _TIME_FORMAT)  # noqa: DTZ007 - UTC text
        end = datetime.strptime(params["end"], _TIME_FORMAT)  # noqa: DTZ007 - UTC text
        records = self.data.get(dataset, [])
        if not records:
            return httpx.Response(200, json={"total": 0, "records": []})
        date_field = _DATE_FIELDS.get(dataset)
        time_field = "TimeUTC" if "TimeUTC" in records[0] else "HourUTC"
        wanted: dict[str, list[str]] = (
            json.loads(params["filter"]) if "filter" in params else {}
        )
        if date_field is None:
            chosen = [
                r
                for r in records
                if start <= datetime.fromisoformat(r[time_field]) < end
            ]
        else:
            first, last = params["start"][:10], params["end"][:10]
            chosen = [r for r in records if first <= r[date_field][:10] < last]
        for name, values in wanted.items():
            if any(name not in r for r in chosen):
                return httpx.Response(400, json={"message": f"no field {name}"})
            if self.honour_filter:
                chosen = [r for r in chosen if r[name] in values]
        if "sort" in params:
            field = params["sort"].split()[0]
            chosen.sort(key=lambda r: r[field])
        if "columns" in params:
            names = params["columns"].split(",")
            if any(name not in r for r in chosen for name in names):
                return httpx.Response(400, json={"message": "no such column"})
            chosen = [{name: r[name] for name in names} for r in chosen]
        return httpx.Response(200, json={"total": len(chosen), "records": chosen})

    def client(
        self, max_span: timedelta = timedelta(days=31)
    ) -> EnergiDataServiceClient:
        """A real client wired to this mock."""
        return EnergiDataServiceClient(
            transport=httpx.MockTransport(self),
            policy=RetryPolicy(base_delay=0.0, max_delay=0.0),
            max_span=max_span,
        )


@pytest.fixture
def markets_records() -> MarketRecords:
    """The probe's records for the market datasets, keyed by dataset name."""
    loaded: MarketRecords = json.loads(MARKETS_FIXTURE.read_text(encoding="utf-8"))
    return loaded


@pytest.fixture
def markets_service(markets_records: MarketRecords) -> MarketsService:
    """A mock service serving `markets_records`; it also notes every request."""
    return MarketsService(markets_records)


@pytest.fixture
def pricelist_fixture() -> MarketRecords:
    """The price-list fixture file: `records` and `catalogue`.

    `records` is the full history of the tested charge codes; `catalogue` lists
    every charge seen since 2025, without prices.
    """
    loaded: MarketRecords = json.loads(PRICELIST_FIXTURE.read_text(encoding="utf-8"))
    return loaded


@pytest.fixture
def pricelist_service(pricelist_fixture: MarketRecords) -> MarketsService:
    """A mock service serving the fixture's price-list records."""
    return MarketsService({"DatahubPricelist": pricelist_fixture["records"]})


# --- Min Strøm -------------------------------------------------------------

ADDRESS_ID = "5b0ac4d2-3c5e-4f55-8a53-0a6f5a1b2c3d"
_COPENHAGEN = ZoneInfo("Europe/Copenhagen")
_API_TIME = "%Y-%m-%dT%H:%M:%SZ"
_ALL_CAPABILITIES = {
    "consumption": True,
    "solarExport": True,
    "consumptionCosts": True,
    "solarExportRevenue": True,
    "prices": True,
    "forecastPrices": True,
}
_INTERVALS = {
    "consumption": ("aggregation", ("hour", "day", "month", "year")),
    "solar-export": ("aggregation", ("hour", "day", "month", "year")),
    "consumption-costs": ("aggregation", ("hour", "day", "month", "year")),
    "solar-export-revenue": ("aggregation", ("hour", "day", "month", "year")),
    "prices": ("resolution", ("hour", "fifteenMinutes")),
}
_CAPABILITY_OF = {
    "consumption": "consumption",
    "solar-export": "solarExport",
    "consumption-costs": "consumptionCosts",
    "solar-export-revenue": "solarExportRevenue",
    "prices": "prices",
    "forecast-prices": "forecastPrices",
}


def minstroem_address(
    address_id: str = ADDRESS_ID, label: str = "Home", **capabilities: bool
) -> dict[str, Any]:
    """One address object as `/me/addresses` serves it; capabilities default true."""
    return {
        "id": address_id,
        "label": label,
        "formattedAddress": "Testvej 1, 1000 Testby",
        "region": "DK1",
        "timeZone": "Europe/Copenhagen",
        "gridOperatorName": "Test Net",
        "supplierName": None,
        "capabilities": {**_ALL_CAPABILITIES, **capabilities},
    }


def minstroem_price_row(
    start: datetime, total: float | None, variable: float | None = 0.25
) -> dict[str, Any]:
    """One hourly price or forecast row starting at the aware instant `start`.

    The hour is added in UTC: adding it to a Copenhagen-time `datetime` would be
    wall-clock arithmetic and give a two-hour row across the autumn change.
    """
    begin = start.astimezone(UTC)
    return {
        "start": begin.strftime(_API_TIME),
        "end": (begin + timedelta(hours=1)).strftime(_API_TIME),
        "totalPricePerKWh": total,
        "variableChargesPerKWh": variable,
    }


def minstroem_request_id(number: int) -> str:
    """The `X-Request-ID` the mock gives its `number`th request.

    A UUID, as the spec types the header (`format: uuid`): a test that handles
    request ids must meet the shape the real API sends, not a friendlier one.
    """
    return str(uuid.UUID(int=number, version=4))


def _floor(instant: datetime, interval: str) -> datetime:
    """Round a UTC instant down to the start of its Copenhagen-calendar interval."""
    local = instant.astimezone(_COPENHAGEN)
    if interval == "hour":
        local = local.replace(minute=0, second=0, microsecond=0)
    elif interval == "fifteenMinutes":
        local = local.replace(minute=local.minute // 15 * 15, second=0, microsecond=0)
    else:
        day = local.date()
        if interval == "month":
            day = day.replace(day=1)
        elif interval == "year":
            day = day.replace(month=1, day=1)
        local = datetime.combine(day, time(0), tzinfo=_COPENHAGEN)
    return local.astimezone(UTC)


def _next(instant: datetime, interval: str) -> datetime:
    """The start of the interval after the one starting at the UTC `instant`."""
    if interval == "hour":
        return instant + timedelta(hours=1)
    if interval == "fifteenMinutes":
        return instant + timedelta(minutes=15)
    day = instant.astimezone(_COPENHAGEN).date()
    if interval == "day":
        day += timedelta(days=1)
    elif interval == "month":
        day = date(day.year + day.month // 12, day.month % 12 + 1, 1)
    else:
        day = date(day.year + 1, 1, 1)
    return datetime.combine(day, time(0), tzinfo=_COPENHAGEN).astimezone(UTC)


def _ceil(instant: datetime, interval: str) -> datetime:
    """Round a UTC instant up to an interval start."""
    floor = _floor(instant, interval)
    return floor if floor == instant else _next(floor, interval)


def _touched(lo: datetime, hi: datetime) -> tuple[int, int]:
    """Copenhagen calendar days and years touched by `[lo, hi)`, partials counting."""
    first = lo.astimezone(_COPENHAGEN).date()
    last = (hi - timedelta(microseconds=1)).astimezone(_COPENHAGEN).date()
    return (last - first).days + 1, last.year - first.year + 1


class MinStroemService:
    """A mock Min Strøm Consumer API.

    Behaves as the documented API does: the bearer key is checked (401
    `invalid_token`); `from`, `to` and the interval parameter are validated (400
    `invalid_parameter`, `invalid_range`); the period is expanded outward to
    whole Copenhagen-calendar intervals; the cap is checked on the expanded
    period (400 `range_too_large`) under the strictest reading: at most 366
    local calendar days touched (a partial day counts) for hourly usage, costs,
    revenue and hourly prices, 90 for `fifteenMinutes`, at most 5 calendar years
    for usage at `day` and above; an unknown address is a 404, a capability the
    address lacks a 422 `feature_unavailable`. Every response carries an
    `X-Request-ID`, a UUID that counts the requests (`minstroem_request_id`).
    Written independently of the package so the tests mean something.

    Rows are served sparse: either `rows[suffix]` (a list of API-shaped row
    dicts, filtered to the expanded period), or, when `generate` is true and
    `rows` has nothing for the endpoint, one deterministic row per interval,
    minus any whose start text is in `gaps`. `forecast` holds the forecast rows.
    `overrides[last path segment]` answers a request in place of the mock, and
    every request is noted in `requests`.
    """

    def __init__(
        self,
        addresses: list[dict[str, Any]] | None = None,
        *,
        api_key: str = "test-key",
        currency: str = "DKK",
    ) -> None:
        """Serve `addresses` (one default address if omitted) behind `api_key`."""
        self.addresses = [minstroem_address()] if addresses is None else addresses
        self.api_key = api_key
        self.currency = currency
        self.rows: dict[str, list[dict[str, Any]]] = {}
        self.forecast: list[dict[str, Any]] = []
        self.generate = True
        self.gaps: set[str] = set()
        self.overrides: dict[str, Callable[[httpx.Request], httpx.Response]] = {}
        self.requests: list[httpx.Request] = []

    def calls(self, suffix: str) -> list[dict[str, str]]:
        """The query parameters of every request whose path ends in `suffix`."""
        return [
            dict(r.url.params) for r in self.requests if r.url.path.endswith(suffix)
        ]

    def client(
        self,
        api_key: str | None = "test-key",
        *,
        policy: RetryPolicy | None = None,
        max_concurrency: int = 2,
    ) -> MinStroemClient:
        """A real client wired to this mock, with instant retries by default."""
        return MinStroemClient(
            api_key,
            policy=policy or RetryPolicy(base_delay=0.0, max_delay=0.0),
            max_concurrency=max_concurrency,
            transport=httpx.MockTransport(self),
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Answer one request."""
        self.requests.append(request)
        response = self._answer(request)
        response.headers["X-Request-ID"] = minstroem_request_id(len(self.requests))
        return response

    @staticmethod
    def _error(status: int, code: str, reason: str) -> httpx.Response:
        return httpx.Response(
            status, json={"error": True, "reason": reason, "errorCode": code}
        )

    def _answer(self, request: httpx.Request) -> httpx.Response:
        segments = request.url.path.rstrip("/").split("/")
        override = self.overrides.get(segments[-1])
        if override is not None:
            return override(request)
        if request.headers.get("Authorization") != f"Bearer {self.api_key}":
            return self._error(401, "invalid_token", "The key is not valid.")
        if request.url.path.endswith("/me/addresses"):
            return httpx.Response(200, json={"data": self.addresses})
        # /consumer/v1/me/addresses/{id}/{suffix}
        address_id, suffix = segments[-2], segments[-1]
        known = {a["id"]: a for a in self.addresses}
        if "addresses" not in segments or address_id not in known:
            return self._error(404, "resource_not_found", "No such address.")
        if suffix not in _CAPABILITY_OF:
            return self._error(404, "resource_not_found", "No such endpoint.")
        if not known[address_id]["capabilities"][_CAPABILITY_OF[suffix]]:
            return self._error(422, "feature_unavailable", "Not available.")
        if suffix == "forecast-prices":
            return httpx.Response(
                200,
                json={
                    "addressId": address_id,
                    "resolution": "hour",
                    "isEstimate": True,
                    "timeZone": "Europe/Copenhagen",
                    "currency": self.currency,
                    "vatIncluded": True,
                    "data": self.forecast,
                },
            )
        return self._series(request, address_id, suffix)

    def _series(
        self, request: httpx.Request, address_id: str, suffix: str
    ) -> httpx.Response:
        params = request.url.params
        name, allowed = _INTERVALS[suffix]
        interval = params.get(name, "")
        if interval not in allowed:
            return self._error(400, "invalid_parameter", f"Bad {name}.")
        try:
            lo = datetime.strptime(params["from"], _API_TIME).replace(tzinfo=UTC)
            hi = datetime.strptime(params["to"], _API_TIME).replace(tzinfo=UTC)
        except (KeyError, ValueError):
            return self._error(400, "invalid_parameter", "Bad from or to.")
        if hi <= lo:
            return self._error(400, "invalid_range", "to must be after from.")
        lo, hi = _floor(lo, interval), _ceil(hi, interval)
        days, years = _touched(lo, hi)
        if suffix in ("consumption", "solar-export") and interval != "hour":
            too_large = years > 5
        else:
            too_large = days > (90 if interval == "fifteenMinutes" else 366)
        if too_large:
            return self._error(400, "range_too_large", "The range is too large.")
        data = self._rows(suffix, interval, lo, hi)
        body: dict[str, Any] = {
            "addressId": address_id,
            "from": lo.strftime(_API_TIME),
            "to": hi.strftime(_API_TIME),
            "timeZone": "Europe/Copenhagen",
            "data": data,
        }
        body["resolution" if suffix == "prices" else "aggregation"] = interval
        if suffix not in ("consumption", "solar-export"):
            body["currency"] = self.currency
            body["vatIncluded"] = suffix != "solar-export-revenue"
        return httpx.Response(200, json=body)

    def _rows(
        self, suffix: str, interval: str, lo: datetime, hi: datetime
    ) -> list[dict[str, Any]]:
        if suffix in self.rows:
            wanted = lo.strftime(_API_TIME), hi.strftime(_API_TIME)
            return [r for r in self.rows[suffix] if wanted[0] <= r["start"] < wanted[1]]
        if not self.generate:
            return []
        rows: list[dict[str, Any]] = []
        start = lo
        while start < hi:
            stop = _next(start, interval)
            text = start.strftime(_API_TIME)
            if text not in self.gaps:
                rows.append(self._row(suffix, start, stop))
            start = stop
        return rows

    @staticmethod
    def _row(suffix: str, start: datetime, stop: datetime) -> dict[str, Any]:
        energy = (start.timestamp() / 3600) % 1000
        row: dict[str, Any] = {
            "start": start.strftime(_API_TIME),
            "end": stop.strftime(_API_TIME),
        }
        if suffix in ("consumption", "solar-export"):
            row |= {"energyKWh": energy, "isEstimate": False}
        elif suffix == "prices":
            row |= {"totalPricePerKWh": energy / 1000, "variableChargesPerKWh": 0.25}
        else:
            total_key = "totalCost" if suffix == "consumption-costs" else "netRevenue"
            row |= {
                "energyKWh": energy,
                "totalPricePerKWh": 2.0,
                "fixedFeesForInterval": 0.5,
                total_key: energy * 2.0 + 0.5,
                "isEstimate": False,
            }
        return row


@pytest.fixture
def minstroem_service() -> MinStroemService:
    """A mock Min Strøm API with one address, every capability, generated rows."""
    return MinStroemService()


_LIVE_LINES: list[str] = []


@pytest.fixture
def live_report() -> Callable[[str], None]:
    """Add a line to the `MIN STRØM LIVE SUMMARY` printed at the end of the run."""
    return _LIVE_LINES.append


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    """Print the live summary block, when any live test reported a line."""
    if _LIVE_LINES:
        terminalreporter.section("MIN STRØM LIVE SUMMARY")
        for line in _LIVE_LINES:
            terminalreporter.write_line(line)
