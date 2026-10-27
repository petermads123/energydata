"""Min Strøm Consumer API client: bearer key, error mapping, JSON fetch."""

import os
import re
from collections.abc import Mapping
from typing import cast

import httpx

from energydata.utils.api_client import ApiClient
from energydata.utils.readers import JsonValue, ParseError, read_json
from energydata.utils.retry import (
    DEFAULT_TIMEOUT,
    HoldoffTooLongError,
    RetriesExhaustedError,
    RetryPolicy,
)

BASE_URL: str = "https://api.minstroem.app/consumer/v1"
API_KEY_ENV: str = "MINSTROEM_API_KEY"

_KEY_PATTERN = re.compile(r"[\x21-\x7e]+")
_CAPABILITIES = {
    "consumption": "consumption",
    "solar-export": "solarExport",
    "consumption-costs": "consumptionCosts",
    "solar-export-revenue": "solarExportRevenue",
    "prices": "prices",
    "forecast-prices": "forecastPrices",
}


class MinStroemError(Exception):
    """An error from the Min Strøm API, or a payload that is not what it promises.

    Attributes:
        status: The HTTP status, or `None` when no response is involved.
        error_code: The API's `errorCode`, or `None`.
        reason: The API's `reason`, or `None`.
        request_id: The `X-Request-ID` header of the response, or `None`.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        error_code: str | None = None,
        reason: str | None = None,
        request_id: str | None = None,
    ) -> None:
        """Build the error; `str()` of it is `message` only.

        Args:
            message: The human-readable message. Never holds a header.
            status: The HTTP status.
            error_code: The API's `errorCode`.
            reason: The API's `reason`.
            request_id: The response's `X-Request-ID`.
        """
        super().__init__(message)
        self.status = status
        self.error_code = error_code
        self.reason = reason
        self.request_id = request_id


class MissingApiKeyError(MinStroemError):
    """A request was made by a client that has no API key."""


class MinStroemClient(ApiClient):
    """Client for the Min Strøm Consumer API.

    The key is read from `api_key` or the `MINSTROEM_API_KEY` environment
    variable. Constructing a client never fails for a missing or unusable key;
    the first request does. The key never appears in an exception message or a
    `repr`.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        policy: RetryPolicy | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_concurrency: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Create a client for `BASE_URL`.

        Args:
            api_key: The Consumer API key. `None` reads `API_KEY_ENV`. Leading
                and trailing whitespace is stripped; an empty result counts as
                no key.
            policy: Retry settings. Defaults to `RetryPolicy()`.
            timeout: Per-request timeout in seconds.
            max_concurrency: The most requests in flight at once.
            transport: An `httpx` transport, for tests.

        Raises:
            ValueError: If `max_concurrency` is below 1.
        """
        raw = os.environ.get(API_KEY_ENV) if api_key is None else api_key
        key = (raw or "").strip()
        headers = {"Accept": "application/json"}
        self._key_state = "missing"
        if key:
            if _KEY_PATTERN.fullmatch(key):
                headers["Authorization"] = f"Bearer {key}"
                self._key_state = "ok"
            else:
                self._key_state = "unusable"
        super().__init__(
            BASE_URL,
            policy=policy,
            timeout=timeout,
            max_concurrency=max_concurrency,
            headers=headers,
            transport=transport,
        )

    def __repr__(self) -> str:
        """Describe the client without the key."""
        return f"MinStroemClient(key={'set' if self._key_state == 'ok' else 'missing'})"

    @property
    def has_api_key(self) -> bool:
        """Whether the client holds a key it can send."""
        return self._key_state == "ok"

    async def fetch_json(
        self, path: str, params: Mapping[str, str] | None = None
    ) -> dict[str, JsonValue]:
        """`GET` one path and return the JSON object.

        Like `request`, this may only be awaited on the client's loop; use
        `get_json` from synchronous code.

        Args:
            path: Appended to `BASE_URL`.
            params: Query parameters.

        Returns:
            The JSON object of the response.

        Raises:
            MissingApiKeyError: If the client has no key, before any I/O.
            MinStroemError: For an error response, an exhausted retry or a
                holdoff on a response, a key with characters a header cannot
                carry, or a 2xx body that is not a JSON object.
            RetriesExhaustedError: If a transport failure outlasted the retries.
            RuntimeError: If awaited outside the client's own loop.
        """
        self._check_key()
        try:
            payload = await self.request("GET", path, params=params, fmt="json")
        except httpx.HTTPStatusError as exc:
            raise _api_error(path, exc.response) from exc
        except (RetriesExhaustedError, HoldoffTooLongError) as exc:
            if exc.response is None:
                raise
            raise _api_error(path, exc.response) from exc
        except ParseError as exc:
            raise MinStroemError(
                f"Min Strøm API: the response to {path} is not valid JSON: "
                f"{exc.detail}",
                status=200,
            ) from exc
        if not isinstance(payload, dict):
            raise MinStroemError(
                f"Min Strøm API: the response to {path} is not a JSON object, "
                f"got {type(payload).__name__}",
                status=200,
            )
        return cast("dict[str, JsonValue]", payload)

    def get_json(
        self, path: str, params: Mapping[str, str] | None = None
    ) -> dict[str, JsonValue]:
        """`GET` one path and return the JSON object, synchronously.

        Args:
            path: Appended to `BASE_URL`.
            params: Query parameters.

        Returns:
            The JSON object of the response.

        Raises:
            MissingApiKeyError: If the client has no key.
            MinStroemError: As for `fetch_json`.
            RetriesExhaustedError: If a transport failure outlasted the retries.
            RuntimeError: If the client is closed.
        """
        return self.run(lambda: self.fetch_json(path, params))

    def _check_key(self) -> None:
        """Raise unless the client holds a key it can send."""
        if self._key_state == "missing":
            raise MissingApiKeyError(
                f"no Min Strøm API key: pass api_key= or set {API_KEY_ENV}"
            )
        if self._key_state == "unusable":
            raise MinStroemError(
                "the Min Strøm API key has characters an HTTP header cannot "
                "carry; it must be printable ASCII without spaces"
            )


def _api_error(path: str, response: httpx.Response) -> MinStroemError:
    """Build the error for an error response, reading the documented body."""
    error_code: str | None = None
    reason: str | None = None
    try:
        body = read_json(response.content)
    except ParseError:
        body = None
    if isinstance(body, dict) and body.get("error") is True:
        code, text = body.get("errorCode"), body.get("reason")
        error_code = code if isinstance(code, str) else None
        reason = text if isinstance(text, str) else None
    request_id = response.headers.get("X-Request-ID")
    parts = [f"Min Strøm API: HTTP {response.status_code}"]
    if error_code:
        parts.append(error_code)
    message = " ".join(parts) + f" on {path}"
    if reason:
        message += f": {reason}"
    if request_id:
        message += f" (request id {request_id})"
    if error_code == "feature_unavailable":
        capability = _CAPABILITIES.get(path.rstrip("/").rsplit("/", 1)[-1])
        if capability:
            message += f"; the address lacks the {capability!r} capability"
    return MinStroemError(
        message,
        status=response.status_code,
        error_code=error_code,
        reason=reason,
        request_id=request_id,
    )


def _showcase_has_key() -> bool:
    """Whether the showcases may call the live API; says so when they may not."""
    if os.environ.get(API_KEY_ENV, "").strip():
        return True
    print(f"Set {API_KEY_ENV} to run this showcase against the live Min Strøm API.")
    return False


def main() -> None:
    """Showcase this module's functionality (calls the live API)."""
    if not _showcase_has_key():
        return
    path = "/me/addresses"

    with MinStroemClient() as client:
        payload = client.get_json(path)

    data = payload.get("data")
    addresses = data if isinstance(data, list) else []
    print(f"{path}: {len(addresses)} address(es)")
    for address in addresses:
        if isinstance(address, dict):
            print(f"  region {address.get('region')}: {address.get('capabilities')}")


if __name__ == "__main__":
    main()
