import json
import re
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
import pytest

from energydata.energidataservice import EnergiDataServiceClient
from energydata.minstroem import MinStroemClient
from energydata.utils import RetryPolicy

if TYPE_CHECKING:
    from energydata.eloverblik import EloverblikClient


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


# --- Eloverblik ------------------------------------------------------------

ELOVERBLIK_REFRESH_TOKEN = "test-refresh-token"
ELOVERBLIK_POINT_A = "571313100000000001"
ELOVERBLIK_POINT_B = "571313100000000002"
_ELO_TIME = "%Y-%m-%dT%H:%M:%SZ"
_ELO_RESOLUTION = {
    "Quarter": "PT15M",
    "Hour": "PT1H",
    "Day": "PT1D",  # what the real API labels daily periods
    "Month": "P1M",
    "Year": "P1Y",
}
_ELO_DETAIL_FIELDS = [
    "meteringPointId",
    "parentMeteringPointId",
    "typeOfMP",
    "energyTimeSeriesMeasureUnit",
    "estimatedAnnualVolume",
    "settlementMethod",
    "meterNumber",
    "gridOperatorName",
    "meteringGridAreaIdentification",
    "netSettlementGroup",
    "physicalStatusOfMP",
    "consumerCategory",
    "powerLimitKW",
    "powerLimitKWDecimal",
    "powerLimitA",
    "subTypeOfMP",
    "productionObligation",
    "mpCapacity",
    "mpConnectionType",
    "disconnectionType",
    "product",
    "consumerCVR",
    "dataAccessCVR",
    "consumerStartDate",
    "meterReadingOccurrence",
    "mpReadingCharacteristics",
    "meterCounterDigits",
    "meterCounterMultiplyFactor",
    "meterCounterUnit",
    "meterCounterType",
    "taxReduction",
    "taxSettlementDate",
    "mpRelationType",
    "firstConsumerPartyName",
    "secondConsumerPartyName",
    "occurrence",
    "meteringPointAlias",
    "gridOperatorID",
    "gridOperatorID_SchemeAgencyIdentifier",
    "assetType",
    "mpAddressWashInstructions",
    "darReference",
    "streetCode",
    "streetName",
    "buildingNumber",
    "floorId",
    "roomId",
    "postcode",
    "cityName",
    "citySubDivisionName",
    "municipalityCode",
    "locationDescription",
    "balanceSupplierName",
    "balanceSupplierId",
    "balanceSupplierStartDate",
    "balanceSupplierId_SchemeAgencyIdentifier",
    "protectedName",
]


def eloverblik_point_id(number: int) -> str:
    """The 18-digit metering point id `571313100000000000 + number`."""
    return str(571313100000000000 + number)


def eloverblik_point(
    point_id: str = ELOVERBLIK_POINT_A, **fields: object
) -> dict[str, Any]:
    """One `MeteringPointApiDto` as the listing serves it; `fields` override."""
    point: dict[str, Any] = {
        "meteringPointId": point_id,
        "typeOfMP": "E17",
        "balanceSupplierName": "Test Supplier",
        "postcode": "1000",
        "cityName": "Testby",
        "hasRelation": True,
        "consumerCVR": None,
        "dataAccessCVR": None,
        "childMeteringPoints": [],
        "streetCode": "0001",
        "streetName": "Testvej",
        "buildingNumber": "1",
        "floorId": "st",
        "roomId": "tv",
        "citySubDivisionName": None,
        "municipalityCode": "101",
        "locationDescription": None,
        "settlementMethod": "D01",
        "meterReadingOccurrence": "PT1H",
        "firstConsumerPartyName": "Test Person",
        "secondConsumerPartyName": None,
        "meterNumber": "12345678",
        "consumerStartDate": "2020-01-01T00:00:00Z",
        "isMovedOut": False,
    }
    return point | fields


def eloverblik_details(
    point_id: str = ELOVERBLIK_POINT_A, **fields: object
) -> dict[str, Any]:
    """One `MeteringPointDetailsCustomerDto`: every spec field, `fields` override."""
    details: dict[str, Any] = {name: f"{name}-value" for name in _ELO_DETAIL_FIELDS}
    details |= {
        "meteringPointId": point_id,
        "powerLimitKWDecimal": 6.5,
        "contactAddresses": [{"contactName1": "Test Person", "postcode": "1000"}],
        "childMeteringPoints": [],
        "parentMeteringPointId": None,
        "balanceSupplierStartDate": None,
    }
    return details | fields


def eloverblik_charge(price_id: str = "SUB1", **fields: object) -> dict[str, Any]:
    """One subscription or fee as `getcharges` serves it."""
    charge: dict[str, Any] = {
        "priceId": price_id,
        "name": f"Charge {price_id}",
        "description": f"Description {price_id}",
        "owner": "5790000000001",
        "validFromDate": "2025-01-01T00:00:00",
        "validToDate": None,
        "periodType": "P1M",
        "price": 49.5,
        "quantity": 1,
    }
    return charge | fields


def eloverblik_tariff(
    price_id: str = "TAR1", prices: int = 24, **fields: object
) -> dict[str, Any]:
    """One tariff, with `prices` positions priced `0.1 * position`."""
    tariff: dict[str, Any] = {
        "priceId": price_id,
        "name": f"Tariff {price_id}",
        "description": f"Description {price_id}",
        "owner": "5790000000001",
        "validFromDate": "2025-01-01T00:00:00",
        "validToDate": None,
        "periodType": "PT1H",
        "prices": [
            {"position": str(n), "price": round(0.1 * n, 4)}
            for n in range(1, prices + 1)
        ],
    }
    return tariff | fields


def _elo_local_midnight(day: date) -> datetime:
    return datetime.combine(day, time(0), tzinfo=_COPENHAGEN).astimezone(UTC)


def _elo_quantity(instant: datetime) -> str:
    return f"{(instant.timestamp() / 3600) % 1000:.3f}"


class EloverblikService:
    """A mock Eloverblik Customer API, written from the published spec.

    `/token` trades the refresh token for an access token (`access-1`, `access-2`,
    ...; `expire_access()` makes every issued one invalid, so the next call is a 401);
    every other path needs a valid access token (401 ProblemDetails). `/isalive`,
    the listing, `getdetails`, `getcharges` and `gettimeseries` are served.
    `gettimeseries` validates as documented (HTTP 400 ProblemDetails carrying
    `errorCode`: 20013, 30000-30004, 30008, 30014, 30017) and then answers one
    result per requested id with generated CIM data: `dateTo` exclusive, Danish
    dates, quality `A04`, DST-correct. Quarter and hour data come as one Period per
    Danish day; day, month and year data as one Period over the whole range.

    Scriptable: `codes[point_id][(date_from, date_to) | None]` answers a point with
    an error code in that window (`None`: every window); `detail_codes` and
    `charge_codes` do the same by point; `a02` holds period-start texts with quality
    `A02` and no quantity; `gaps[point_id]` period-start texts left out;
    `resolution_override` and `unit_override` change what a point reports;
    `out_of_order`, `drop_ids`, `repeat_ids` and `extra_ids` bend the result list;
    `max_days` is the 730-day cap (`None` removes it); `overrides[last path segment]`
    answers a request in place of the mock. Every response carries an
    `X-Correlation-ID`; every request is kept in `requests`.
    """

    def __init__(
        self,
        points: list[dict[str, Any]] | None = None,
        *,
        refresh_token: str = ELOVERBLIK_REFRESH_TOKEN,
    ) -> None:
        """Serve `points` (one default point if omitted) behind `refresh_token`."""
        self.points = [eloverblik_point()] if points is None else points
        self.unlinked: list[dict[str, Any]] = []
        self.refresh_token = refresh_token
        self.details: dict[str, dict[str, Any]] = {}
        self.charges: dict[str, dict[str, list[dict[str, Any]]]] = {}
        self.codes: dict[str, dict[tuple[str, str] | None, int]] = {}
        self.detail_codes: dict[str, int] = {}
        self.charge_codes: dict[str, int] = {}
        self.a02: set[str] = set()
        self.gaps: dict[str, set[str]] = {}
        self.resolution_override: dict[str, str] = {}
        self.unit_override: dict[str, str] = {}
        self.out_of_order = False
        self.drop_ids: set[str] = set()
        self.repeat_ids: set[str] = set()
        self.extra_ids: list[str] = []
        self.max_days: int | None = 730
        self.overrides: dict[str, Callable[[httpx.Request], httpx.Response]] = {}
        self.requests: list[httpx.Request] = []
        self.token_calls = 0
        self._valid: set[str] = set()

    @property
    def point_ids(self) -> list[str]:
        """The linked point ids, in listing order."""
        return [p["meteringPointId"] for p in self.points]

    def calls(self, suffix: str) -> list[httpx.Request]:
        """Every request whose path ends in `suffix`."""
        return [r for r in self.requests if r.url.path.endswith(suffix)]

    def timeseries_calls(self) -> list[tuple[str, str, str, list[str]]]:
        """`(dateFrom, dateTo, aggregation, ids)` of every `gettimeseries` request."""
        found = []
        for request in self.requests:
            parts = request.url.path.split("/")
            if "gettimeseries" in parts:
                at = parts.index("gettimeseries")
                ids = json.loads(request.content)["meteringPoints"]["meteringPoint"]
                found.append((parts[at + 1], parts[at + 2], parts[at + 3], ids))
        return found

    def expire_access(self) -> None:
        """Invalidate every access token issued so far."""
        self._valid.clear()

    def client(
        self,
        refresh_token: str | None = ELOVERBLIK_REFRESH_TOKEN,
        *,
        policy: RetryPolicy | None = None,
        max_concurrency: int = 2,
    ) -> "EloverblikClient":
        """A real client wired to this mock, with instant retries by default."""
        from energydata.eloverblik import EloverblikClient

        return EloverblikClient(
            refresh_token,
            policy=policy or RetryPolicy(base_delay=0.0, max_delay=0.0),
            max_concurrency=max_concurrency,
            transport=httpx.MockTransport(self),
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Answer one request."""
        self.requests.append(request)
        response = self._answer(request)
        response.headers["X-Correlation-ID"] = str(
            uuid.UUID(int=len(self.requests), version=4)
        )
        return response

    @staticmethod
    def _problem(status: int, title: str, **extra: object) -> httpx.Response:
        body = {"type": "about:blank", "title": title, "status": status} | extra
        return httpx.Response(status, json=body)

    def _answer(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.rstrip("/")
        tail = path.split("/customerapi/api", 1)[-1]
        override = self.overrides.get(path.rsplit("/", 1)[-1])
        if override is not None:
            return override(request)
        auth = request.headers.get("Authorization", "")
        if tail == "/token":
            if request.method != "GET":
                return self._problem(405, "Method Not Allowed")
            if auth != f"Bearer {self.refresh_token}":
                return self._problem(401, "Unauthorized", detail="Bad refresh token.")
            self.token_calls += 1
            access = f"access-{self.token_calls}"
            self._valid.add(access)
            return httpx.Response(200, json={"result": access})
        if tail == "/isalive":
            return httpx.Response(200, json=True)
        if not auth.startswith("Bearer ") or auth[7:] not in self._valid:
            return self._problem(401, "Unauthorized")
        if tail == "/meteringpoints/meteringpoints" and request.method == "GET":
            include = request.url.params.get("includeAll", "false").lower() == "true"
            rows = self.points + (self.unlinked if include else [])
            return httpx.Response(200, json={"result": rows})
        if request.method != "POST":
            return self._problem(405, "Method Not Allowed")
        try:
            ids = json.loads(request.content)["meteringPoints"]["meteringPoint"]
            assert isinstance(ids, list)
        except (ValueError, KeyError, TypeError, AssertionError):
            return self._problem(400, "Bad Request", errorCode=20013)
        if not ids:
            return self._problem(400, "Bad Request", errorCode=20013)
        if tail == "/meteringpoints/meteringpoint/getdetails":
            return self._details(ids)
        if tail == "/meteringpoints/meteringpoint/getcharges":
            return self._charges(ids)
        if tail.startswith("/meterdata/gettimeseries/"):
            return self._timeseries(tail.split("/")[3:], ids)
        return self._problem(404, "Not Found")

    @staticmethod
    def _failure(point_id: str, code: int, key: str, empty: object) -> dict[str, Any]:
        text = {20008: "Metering point not found", 20003: "Invalid metering point id"}
        return {
            "success": False,
            "errorCode": code,
            "errorText": text.get(code, f"Error {code}"),
            "id": point_id,
            "stackTrace": None,
            key: empty,
        }

    def _known(self, point_id: str) -> int | None:
        if not re.fullmatch(r"\d{18}", point_id):
            return 20003
        return None if point_id in self.point_ids else 20008

    def _details(self, ids: list[str]) -> httpx.Response:
        results: list[dict[str, Any]] = []
        for point_id in ids:
            code = self.detail_codes.get(point_id) or self._known(point_id)
            if code:
                results.append(self._failure(point_id, code, "result", None))
                continue
            body = self.details.get(point_id, eloverblik_details(point_id))
            results.append(
                {
                    "success": True,
                    "errorCode": 10000,
                    "errorText": "OK",
                    "id": point_id,
                    "stackTrace": None,
                    "result": body,
                }
            )
        return httpx.Response(200, json={"result": results})

    def _charges(self, ids: list[str]) -> httpx.Response:
        results: list[dict[str, Any]] = []
        for point_id in ids:
            code = self.charge_codes.get(point_id) or self._known(point_id)
            if code:
                results.append(self._failure(point_id, code, "result", None))
                continue
            found = self.charges.get(point_id) or {
                "subscriptions": [eloverblik_charge("SUB1")],
                "tariffs": [eloverblik_tariff("TAR1")],
                "fees": [eloverblik_charge("FEE1", periodType="P1D", price=0.25)],
            }
            results.append(
                {
                    "success": True,
                    "errorCode": 10000,
                    "errorText": "OK",
                    "id": point_id,
                    "stackTrace": None,
                    "result": {"meteringPointId": point_id, **found},
                }
            )
        return httpx.Response(200, json={"result": results})

    def _timeseries(self, parts: list[str], ids: list[str]) -> httpx.Response:
        date_from, date_to, aggregation = parts
        problem = self._problem
        try:
            for text in (date_from, date_to):
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
                    raise ValueError(text)
            first, last = date.fromisoformat(date_from), date.fromisoformat(date_to)
        except ValueError:
            return problem(400, "Bad Request", errorCode=30004)
        if aggregation not in _ELO_RESOLUTION:
            return problem(400, "Bad Request", errorCode=30008)
        today = pd.Timestamp.now("Europe/Copenhagen").date()
        horizon = date(today.year - 5, 1, 1)
        too_long = self.max_days is not None and (last - first).days > self.max_days
        for failed, code in (
            (first > today, 30000),
            (first > last, 30001),
            (first == last, 30002),
            (last > today + timedelta(days=1), 30003),
            (too_long, 30014),
            (last <= horizon, 30017),
        ):
            if failed:
                return problem(400, "Bad Request", errorCode=code)
        results = [self._series(i, first, last, aggregation) for i in ids]
        if self.out_of_order:
            results.reverse()
        results = [r for r in results if r["id"] not in self.drop_ids]
        results += [r for r in results if r["id"] in self.repeat_ids]
        for extra in self.extra_ids:
            results.append(self._series(extra, first, last, aggregation))
        return httpx.Response(200, json={"result": results})

    def _series(
        self, point_id: str, first: date, last: date, aggregation: str
    ) -> dict[str, Any]:
        scripted = self.codes.get(point_id, {})
        code = scripted.get((first.isoformat(), last.isoformat())) or scripted.get(None)
        code = code or self._known(point_id)
        if code:
            return self._failure(
                point_id, code, "MyEnergyData_MarketDocument", {"TimeSeries": None}
            )
        resolution = _ELO_RESOLUTION[aggregation]
        periods = []
        for begin, end, count in self._spans(first, last, aggregation):
            points = []
            for position in range(1, count + 1):
                instant = self._at(begin, aggregation, position - 1)
                text = instant.strftime(_ELO_TIME)
                if text in self.gaps.get(point_id, set()):
                    continue
                point: dict[str, Any] = {"position": str(position)}
                if text in self.a02:
                    point["out_Quantity.quality"] = "A02"
                else:
                    point["out_Quantity.quantity"] = _elo_quantity(instant)
                    point["out_Quantity.quality"] = "A04"
                points.append(point)
            periods.append(
                {
                    "resolution": self.resolution_override.get(point_id, resolution),
                    "timeInterval": {
                        "start": begin.strftime(_ELO_TIME),
                        "end": end.strftime(_ELO_TIME),
                    },
                    "Point": points,
                }
            )
        series = {
            "mRID": point_id,
            "businessType": "A04",
            "curveType": "A01",
            "measurement_Unit.name": self.unit_override.get(point_id, "KWH"),
            "MarketEvaluationPoint": {
                "mRID": {"codingScheme": "A10", "name": point_id}
            },
            "Period": periods,
        }
        document = {
            "mRID": "doc",
            "createdDateTime": "2026-11-02T00:00:00Z",
            "sender_MarketParticipant.name": "Energinet",
            "sender_MarketParticipant.mRID": {
                "codingScheme": "A10",
                "name": "5790001330583",
            },
            "period.timeInterval": {
                "start": _elo_local_midnight(first).strftime(_ELO_TIME),
                "end": _elo_local_midnight(last).strftime(_ELO_TIME),
            },
            "TimeSeries": [series],
        }
        return {
            "success": True,
            "errorCode": 10000,
            "errorText": "OK",
            "id": point_id,
            "stackTrace": None,
            "MyEnergyData_MarketDocument": document,
        }

    @staticmethod
    def _spans(
        first: date, last: date, aggregation: str
    ) -> list[tuple[datetime, datetime, int]]:
        """`(period start, period end, point count)` in UTC for `[first, last)`."""
        if aggregation in ("Quarter", "Hour"):
            per_hour = 4 if aggregation == "Quarter" else 1
            spans = []
            day = first
            while day < last:
                begin = _elo_local_midnight(day)
                end = _elo_local_midnight(day + timedelta(days=1))
                hours = round((end - begin).total_seconds() / 3600)
                spans.append((begin, end, hours * per_hour))
                day += timedelta(days=1)
            return spans
        if aggregation == "Day":
            return [
                (
                    _elo_local_midnight(first),
                    _elo_local_midnight(last),
                    (last - first).days,
                )
            ]
        if aggregation == "Month":
            months = (last.year - first.year) * 12 + last.month - first.month
            count = months + (1 if last.day > 1 else 0)
            start = first.replace(day=1)
            index = start.month - 1 + count
            stop = date(start.year + index // 12, index % 12 + 1, 1)
            return [(_elo_local_midnight(start), _elo_local_midnight(stop), count)]
        count = last.year - first.year + (1 if (last.month, last.day) > (1, 1) else 0)
        year_start = date(first.year, 1, 1)
        year_stop = date(year_start.year + count, 1, 1)
        return [
            (_elo_local_midnight(year_start), _elo_local_midnight(year_stop), count)
        ]

    @staticmethod
    def _at(begin: datetime, aggregation: str, offset: int) -> datetime:
        """The UTC start of the `offset`th interval of a period starting at `begin`."""
        if aggregation == "Quarter":
            return begin + timedelta(minutes=15 * offset)
        if aggregation == "Hour":
            return begin + timedelta(hours=offset)
        day = begin.astimezone(_COPENHAGEN).date()
        if aggregation == "Day":
            day += timedelta(days=offset)
        elif aggregation == "Month":
            index = day.month - 1 + offset
            day = date(day.year + index // 12, index % 12 + 1, 1)
        else:
            day = date(day.year + offset, 1, 1)
        return _elo_local_midnight(day)


@pytest.fixture
def eloverblik_service() -> EloverblikService:
    """A mock Eloverblik API with one linked point and generated data."""
    return EloverblikService()
