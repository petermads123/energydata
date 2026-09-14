"""Tests for the paginated Energi Data Service HTTP client."""

from typing import Any

import pytest
import requests

from energydata.common import client as client_mod
from energydata.common.client import fetch_dataset
from energydata.common.errors import EnergyDataError


class FakeResponse:
    """Minimal stand-in for ``requests.Response`` used to mock HTTP calls."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._json_body = json_body if json_body is not None else {}
        self.headers = headers if headers is not None else {}
        self.text = text

    def json(self) -> Any:
        return self._json_body


def make_records_page(records: list[dict[str, Any]]) -> FakeResponse:
    """Wrap a list of records in a 200 response shaped like the EDS API."""
    return FakeResponse(status_code=200, json_body={"records": records})


def test_single_short_page_stops_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    # One page shorter than the limit is the final page.
    calls: list[dict[str, Any]] = []

    def fake_get(_url: str, *, params: dict[str, Any], **_kwargs: Any) -> FakeResponse:
        calls.append(params)
        return make_records_page([{"x": 1}, {"x": 2}])

    monkeypatch.setattr(client_mod.requests, "get", fake_get)
    records = fetch_dataset("Elspotprices", page_limit=10)
    assert records == [{"x": 1}, {"x": 2}]
    assert len(calls) == 1


def test_pagination_across_two_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    # First page is exactly `page_limit` long -> keep going; second is short.
    seen_offsets: list[int] = []

    def fake_get(_url: str, *, params: dict[str, Any], **_kwargs: Any) -> FakeResponse:
        seen_offsets.append(params["offset"])
        if params["offset"] == 0:
            return make_records_page([{"i": 0}, {"i": 1}])
        return make_records_page([{"i": 2}])

    monkeypatch.setattr(client_mod.requests, "get", fake_get)
    records = fetch_dataset("DayAheadPrices", page_limit=2)
    assert records == [{"i": 0}, {"i": 1}, {"i": 2}]
    # Offsets advance by page_limit each round.
    assert seen_offsets == [0, 2]


def test_exact_multiple_triggers_extra_empty_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A full final page forces one more fetch that returns an empty page.
    def fake_get(_url: str, *, params: dict[str, Any], **_kwargs: Any) -> FakeResponse:
        if params["offset"] == 0:
            return make_records_page([{"i": 0}, {"i": 1}])
        return make_records_page([])

    monkeypatch.setattr(client_mod.requests, "get", fake_get)
    records = fetch_dataset("DayAheadPrices", page_limit=2)
    assert records == [{"i": 0}, {"i": 1}]


def test_429_retries_once_honoring_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        FakeResponse(status_code=429, headers={"Retry-After": "0.01"}, text="slow"),
        make_records_page([{"ok": True}]),
    ]
    slept: list[float] = []

    def fake_get(*_args: Any, **_kwargs: Any) -> FakeResponse:
        return responses.pop(0)

    monkeypatch.setattr(client_mod.requests, "get", fake_get)
    monkeypatch.setattr(client_mod.time, "sleep", lambda s: slept.append(s))

    records = fetch_dataset("Elspotprices", page_limit=10)
    assert records == [{"ok": True}]
    # Retry-After of 0.01 seconds is honored before the single retry.
    assert slept == [0.01]


def test_429_without_retry_after_uses_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        FakeResponse(status_code=429, headers={}),
        make_records_page([{"ok": 1}]),
    ]
    slept: list[float] = []

    monkeypatch.setattr(
        client_mod.requests, "get", lambda *_a, **_k: responses.pop(0)
    )
    monkeypatch.setattr(client_mod.time, "sleep", lambda s: slept.append(s))

    fetch_dataset("Elspotprices", page_limit=10)
    assert slept == [5.0]


def test_persistent_429_after_retry_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # A second 429 (retry also rate-limited) is a non-200 -> error.
    monkeypatch.setattr(
        client_mod.requests,
        "get",
        lambda *_a, **_k: FakeResponse(
            status_code=429, headers={"Retry-After": "0.01"}, text="still limited"
        ),
    )
    monkeypatch.setattr(client_mod.time, "sleep", lambda *_a, **_k: None)
    with pytest.raises(EnergyDataError):
        fetch_dataset("Elspotprices", page_limit=10)


def test_non_200_raises_energydata_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        client_mod.requests,
        "get",
        lambda *_a, **_k: FakeResponse(status_code=500, text="boom"),
    )
    with pytest.raises(EnergyDataError, match="500"):
        fetch_dataset("Elspotprices", page_limit=10)


def test_network_error_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: Any, **_kwargs: Any) -> FakeResponse:
        raise requests.exceptions.ConnectionError("down")

    monkeypatch.setattr(client_mod.requests, "get", boom)
    with pytest.raises(EnergyDataError, match="network error"):
        fetch_dataset("Elspotprices", page_limit=10)


def test_non_object_json_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        client_mod.requests,
        "get",
        lambda *_a, **_k: FakeResponse(status_code=200, json_body=[1, 2, 3]),
    )
    with pytest.raises(EnergyDataError):
        fetch_dataset("Elspotprices", page_limit=10)


def test_query_params_are_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_get(url: str, *, params: dict[str, Any], **_kwargs: Any) -> FakeResponse:
        captured.update(params)
        captured["_url"] = url
        return make_records_page([])

    monkeypatch.setattr(client_mod.requests, "get", fake_get)
    fetch_dataset(
        "Elspotprices",
        start="2025-01-01T00:00",
        end="2025-01-02T00:00",
        filters={"PriceArea": ["DK2"]},
        columns=["HourUTC", "SpotPriceDKK"],
        sort="HourUTC ASC",
        page_limit=10,
    )
    assert captured["_url"].endswith("/dataset/Elspotprices")
    assert captured["start"] == "2025-01-01T00:00"
    assert captured["end"] == "2025-01-02T00:00"
    assert captured["filter"] == '{"PriceArea": ["DK2"]}'
    assert captured["columns"] == "HourUTC,SpotPriceDKK"
    assert captured["sort"] == "HourUTC ASC"
