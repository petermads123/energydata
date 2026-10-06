"""Energi Data Service client: fetch every record of a dataset over a period."""

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta

import httpx

from energydata.utils.api_client import ApiClient
from energydata.utils.chunking import gather_chunked
from energydata.utils.readers import JsonValue
from energydata.utils.retry import DEFAULT_TIMEOUT, RetryPolicy

BASE_URL: str = "https://api.energidataservice.dk"
_TIME_FORMAT = "%Y-%m-%dT%H:%M"

type Record = dict[str, JsonValue]


class EnergiDataServiceError(Exception):
    """The payload is not what the API promises.

    Raised when a response is not a JSON object, has no `records` list, has a
    record that is not a JSON object, or has fewer records than its `total`.
    """


class EnergiDataServiceClient(ApiClient):
    """Client for the Energi Data Service dataset API.

    Every call to the service goes through this class. A period longer than
    `max_span` is split into windows fetched concurrently, so a long request
    is several short ones and never a truncated page.
    """

    def __init__(
        self,
        *,
        policy: RetryPolicy | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_concurrency: int = 4,
        max_span: timedelta = timedelta(days=31),
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Create a client for `BASE_URL`.

        Args:
            policy: Retry settings. Defaults to `RetryPolicy()`.
            timeout: Per-request timeout in seconds.
            max_concurrency: The most requests in flight at once.
            max_span: The longest period a single request may ask for.
            transport: An `httpx` transport, for tests.

        Raises:
            ValueError: If `max_span` is not a positive whole number of minutes
                or `max_concurrency` is below 1.
        """
        _check_span(max_span)
        super().__init__(
            BASE_URL,
            policy=policy,
            timeout=timeout,
            max_concurrency=max_concurrency,
            transport=transport,
        )
        self._max_span = max_span

    async def fetch_dataset(
        self,
        dataset: str,
        start: datetime,
        end: datetime,
        *,
        filters: Mapping[str, Sequence[str]] | None = None,
        columns: Sequence[str] | None = None,
        sort_by: str | None = None,
        max_span: timedelta | None = None,
    ) -> list[Record]:
        """Fetch every record of a dataset over `[start, end)`.

        Like `request`, this may only be awaited on the client's loop; use
        `get_dataset` from synchronous code.

        Args:
            dataset: The dataset name, for example `"Elspotprices"`.
            start: Start of the period, inclusive. Must be timezone-aware.
            end: End of the period, exclusive. Must be timezone-aware.
            filters: Field name to the accepted values, sent as the `filter`
                parameter.
            columns: The fields to return. `None` returns all of them.
            sort_by: A field to sort each window's records by, ascending.
            max_span: The window size for this call, overriding the client's.
                `None` uses the client's.

        Returns:
            The records of every window, concatenated in window order.

        Raises:
            ValueError: If `start` or `end` is naive, has seconds, or
                `start >= end`, or `max_span` is not a positive whole number
                of minutes.
            RuntimeError: If awaited outside the client's own loop.
            EnergiDataServiceError: If a payload is not a JSON object, has no
                `records` list, has a record that is not a JSON object, or has
                fewer records than its `total`.
            httpx.HTTPStatusError: For a non-retryable error status.
        """
        span = self._max_span if max_span is None else max_span
        _check_span(span)
        for name, bound in (("start", start), ("end", end)):
            if bound.second or bound.microsecond:
                raise ValueError(
                    f"{name} must be on a whole minute, the API takes no seconds, "
                    f"got {bound.isoformat()!r}"
                )
        values = (
            {
                name: [v] if isinstance(v, str) else list(v)
                for name, v in filters.items()
            }
            if filters
            else None
        )
        fields = [columns] if isinstance(columns, str) else columns

        async def fetch_window(lo: datetime, hi: datetime) -> list[Record]:
            return await self._fetch_window(dataset, lo, hi, values, fields, sort_by)

        windows = await gather_chunked(fetch_window, start, end, span)
        return [record for window in windows for record in window]

    def get_dataset(
        self,
        dataset: str,
        start: datetime,
        end: datetime,
        *,
        filters: Mapping[str, Sequence[str]] | None = None,
        columns: Sequence[str] | None = None,
        sort_by: str | None = None,
        max_span: timedelta | None = None,
    ) -> list[Record]:
        """Fetch every record of a dataset over `[start, end)`, synchronously.

        Args:
            dataset: The dataset name, for example `"Elspotprices"`.
            start: Start of the period, inclusive. Must be timezone-aware.
            end: End of the period, exclusive. Must be timezone-aware.
            filters: Field name to the accepted values, sent as the `filter`
                parameter.
            columns: The fields to return. `None` returns all of them.
            sort_by: A field to sort each window's records by, ascending.
            max_span: The window size for this call, overriding the client's.
                `None` uses the client's.

        Returns:
            The records of every window, concatenated in window order.

        Raises:
            ValueError: If `start` or `end` is naive, has seconds, or
                `start >= end`, or `max_span` is not a positive whole number
                of minutes.
            RuntimeError: If the client is closed.
            EnergiDataServiceError: If a payload is not a JSON object, has no
                `records` list, has a record that is not a JSON object, or has
                fewer records than its `total`.
            httpx.HTTPStatusError: For a non-retryable error status.
        """
        return self.run(
            lambda: self.fetch_dataset(
                dataset,
                start,
                end,
                filters=filters,
                columns=columns,
                sort_by=sort_by,
                max_span=max_span,
            )
        )

    async def _fetch_window(
        self,
        dataset: str,
        start: datetime,
        end: datetime,
        filters: Mapping[str, Sequence[str]] | None,
        columns: Sequence[str] | None,
        sort_by: str | None,
    ) -> list[Record]:
        """Fetch one window, asking for every record rather than a page."""
        params: dict[str, str | int | float] = {
            "start": start.astimezone(UTC).strftime(_TIME_FORMAT),
            "end": end.astimezone(UTC).strftime(_TIME_FORMAT),
            "timezone": "UTC",
            "limit": 0,
        }
        if filters:
            compact = {name: list(values) for name, values in filters.items()}
            params["filter"] = json.dumps(compact, separators=(",", ":"))
        if columns:
            params["columns"] = ",".join(columns)
        if sort_by:
            params["sort"] = f"{sort_by} asc"
        payload = await self.request(
            "GET", f"/dataset/{dataset}", params=params, fmt="json"
        )
        return _records(payload, dataset)


def _check_span(span: timedelta) -> None:
    """Raise unless `span` is a positive whole number of minutes."""
    if span <= timedelta(0) or span % timedelta(minutes=1):
        raise ValueError(
            f"max_span must be a positive whole number of minutes, got {span!r}"
        )


def _records(payload: object, dataset: str) -> list[Record]:
    """Check a payload against what the API promises and return its records."""
    if not isinstance(payload, dict):
        raise EnergiDataServiceError(
            f"{dataset}: expected a JSON object, got {type(payload).__name__}"
        )
    records = payload.get("records")
    if not isinstance(records, list):
        raise EnergiDataServiceError(f"{dataset}: the response has no 'records' list")
    total = payload.get("total")
    if isinstance(total, int) and not isinstance(total, bool) and len(records) < total:
        raise EnergiDataServiceError(
            f"{dataset}: got {len(records)} records but the response says total={total}"
        )
    checked: list[Record] = []
    for position, record in enumerate(records):
        if not isinstance(record, dict):
            raise EnergiDataServiceError(
                f"{dataset}: record {position} is not a JSON object"
            )
        checked.append(record)
    return checked


def main() -> None:
    """Showcase this module's functionality (calls the live API)."""
    dataset = "Elspotprices"  # e.g. "Elspotprices", "DayAheadPrices"
    start = datetime(2025, 1, 1, tzinfo=UTC)
    end = datetime(2025, 1, 1, 3, tzinfo=UTC)
    filters = {"PriceArea": ["DK1"]}
    columns = ["HourUTC", "PriceArea", "SpotPriceEUR"]
    sort_by = "HourUTC"

    with EnergiDataServiceClient() as client:
        records = client.get_dataset(
            dataset, start, end, filters=filters, columns=columns, sort_by=sort_by
        )

    print(f"{dataset}: {len(records)} records")
    for record in records:
        print(f"  {record}")


if __name__ == "__main__":
    main()
