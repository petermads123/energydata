"""Thin HTTP client for the Energi Data Service dataset API.

Wraps ``GET https://api.energidataservice.dk/dataset/{dataset}`` with
auto-pagination, a single retry on HTTP 429 (rate limiting), and uniform
error handling via :class:`~energydata.common.errors.EnergyDataError`.
"""

import json
import time
from typing import Any

import requests

from energydata.common.errors import EnergyDataError

# Base URL for the Energi Data Service dataset API.
_BASE_URL = "https://api.energidataservice.dk/dataset"

# HTTP status codes we branch on explicitly.
_HTTP_OK = 200
_HTTP_TOO_MANY_REQUESTS = 429

# Fallback wait time (seconds) if a 429 response doesn't include a usable
# ``Retry-After`` header.
_DEFAULT_RETRY_AFTER_SECONDS = 5.0


def fetch_dataset(
    dataset: str,
    *,
    start: str | None = None,
    end: str | None = None,
    filters: dict[str, list[str]] | None = None,
    columns: list[str] | None = None,
    sort: str | None = None,
    timeout: float = 30.0,
    page_limit: int = 10_000,
) -> list[dict[str, Any]]:
    """Fetch all records for a dataset from the Energi Data Service API.

    Args:
        dataset: Dataset name, e.g. ``"Elspotprices"`` or ``"DayAheadPrices"``.
        start: DK-local naive time string (e.g. ``"2025-01-01T00:00"``); the
            API filters ``start``/``end`` in Danish local time.
        end: DK-local naive time string, exclusive upper bound.
        filters: Column -> allowed-values mapping, JSON-encoded into the
            ``filter`` query parameter (e.g. ``{"PriceArea": ["DK1", "DK2"]}``).
        columns: Subset of columns to return, to reduce payload size.
        sort: Sort expression, e.g. ``"HourUTC ASC"``.
        timeout: Per-request timeout in seconds.
        page_limit: Maximum number of records requested per page; also used
            to detect the last page (a page shorter than this is assumed to
            be the final one).

    Returns:
        The concatenation of ``records`` across all fetched pages.

    Raises:
        EnergyDataError: On a network error, a non-200 HTTP response, or an
            unexpected (non-JSON-object) response body.
    """
    url = f"{_BASE_URL}/{dataset}"

    # Static query params shared by every page of this request.
    base_params: dict[str, Any] = {}
    if start is not None:
        base_params["start"] = start
    if end is not None:
        base_params["end"] = end
    if filters is not None:
        base_params["filter"] = json.dumps(filters)
    if columns is not None:
        base_params["columns"] = ",".join(columns)
    if sort is not None:
        base_params["sort"] = sort

    records: list[dict[str, Any]] = []
    offset = 0
    while True:
        page_params = {**base_params, "limit": page_limit, "offset": offset}
        payload = _get_json(url, page_params, timeout)

        page_records: list[dict[str, Any]] = payload.get("records", [])
        records.extend(page_records)

        # A page shorter than the requested limit means we've reached the
        # end of the result set; stop paginating.
        if len(page_records) < page_limit:
            break
        offset += page_limit

    return records


def _get_json(url: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
    """Perform a single GET request, retrying once on HTTP 429.

    Args:
        url: Full request URL (without query string).
        params: Query parameters for this request/page.
        timeout: Request timeout in seconds.

    Returns:
        The parsed JSON response body as a dict.

    Raises:
        EnergyDataError: On a network error, a non-200 HTTP response (after
            the retry attempt, if any), or a non-object JSON body.
    """
    response = _send(url, params, timeout)

    if response.status_code == _HTTP_TOO_MANY_REQUESTS:
        # Rate limited: honor Retry-After (seconds), sleep, and retry once.
        wait_seconds = _parse_retry_after(response.headers.get("Retry-After"))
        time.sleep(wait_seconds)
        response = _send(url, params, timeout)

    if response.status_code != _HTTP_OK:
        snippet = response.text[:500]
        raise EnergyDataError(
            f"request to {url} failed with status {response.status_code}: {snippet}"
        )

    payload: dict[str, Any] = response.json()
    if not isinstance(payload, dict):
        raise EnergyDataError(f"unexpected non-object JSON response from {url}: {payload!r}")
    return payload


def _send(url: str, params: dict[str, Any], timeout: float) -> requests.Response:
    """Send a single GET request, wrapping network errors.

    Args:
        url: Full request URL (without query string).
        params: Query parameters for this request.
        timeout: Request timeout in seconds.

    Returns:
        The raw ``requests.Response``.

    Raises:
        EnergyDataError: If the request fails at the network/transport level.
    """
    try:
        return requests.get(url, params=params, timeout=timeout)
    except requests.exceptions.RequestException as exc:
        raise EnergyDataError(f"network error while requesting {url}: {exc}") from exc


def _parse_retry_after(value: str | None) -> float:
    """Parse a ``Retry-After`` header value expressed in seconds.

    Args:
        value: The raw header value, or ``None`` if absent.

    Returns:
        Number of seconds to wait before retrying. Falls back to a default
        if the header is missing or not a plain integer/float of seconds
        (the HTTP-date form of ``Retry-After`` is not handled, as the
        Energi Data Service API always returns the seconds form).
    """
    if value is None:
        return _DEFAULT_RETRY_AFTER_SECONDS
    try:
        return float(value)
    except ValueError:
        return _DEFAULT_RETRY_AFTER_SECONDS


def main() -> None:
    """Demonstrate fetch_dataset with a small, live, recent query."""
    # A single day of 15-min DK2 records fits comfortably in one page, so
    # this also implicitly exercises the "last page is short" stop condition.
    records = fetch_dataset(
        "DayAheadPrices",
        start="2026-07-08T00:00",
        end="2026-07-09T00:00",
        filters={"PriceArea": ["DK2"]},
        columns=["TimeUTC", "PriceArea", "DayAheadPriceDKK"],
        sort="TimeUTC ASC",
    )
    print(f"fetch_dataset('DayAheadPrices', ...) -> {len(records)} records")
    for record in records[:5]:
        print(record)


if __name__ == "__main__":
    main()
