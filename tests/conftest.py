import json
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from energydata.energidataservice import EnergiDataServiceClient
from energydata.utils import RetryPolicy


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every real httpx transport refuse, so no test can reach the network."""

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


MARKETS_FIXTURE = Path(__file__).parent / "fixtures" / "energidataservice_markets.json"
_TIME_FORMAT = "%Y-%m-%dT%H:%M"

type MarketRecords = dict[str, list[dict[str, Any]]]


class MarketsService:
    """A mock Energi Data Service for the market datasets.

    Serves stored records the way the API does: a `[start, end)` window on the
    dataset's UTC time field, then the `filter` parameter on every field it
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
        time_field = "TimeUTC" if "TimeUTC" in records[0] else "HourUTC"
        wanted: dict[str, list[str]] = (
            json.loads(params["filter"]) if "filter" in params else {}
        )
        chosen = [
            r for r in records if start <= datetime.fromisoformat(r[time_field]) < end
        ]
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
