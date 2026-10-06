import httpx
import pytest


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
