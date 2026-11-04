"""Eloverblik Customer API client: refresh and access tokens, error mapping, JSON."""

import asyncio
import os
import re
from collections.abc import Mapping
from typing import Literal, cast

import httpx

from energydata.utils.api_client import ApiClient
from energydata.utils.readers import JsonValue, ParseError, read_json, read_response
from energydata.utils.retry import (
    DEFAULT_TIMEOUT,
    HoldoffTooLongError,
    RetriesExhaustedError,
    RetryPolicy,
)

BASE_URL: str = "https://api.eloverblik.dk/customerapi/api"
REFRESH_TOKEN_ENV: str = "ELOVERBLIK_REFRESH_TOKEN"
DEFAULT_POLICY: RetryPolicy = RetryPolicy(
    max_attempts=4, base_delay=15.0, max_delay=60.0
)

_TOKEN_PATTERN = re.compile(r"[\x21-\x7e]+")
_MIN_SECRET = 6
_STRING_BODY = re.compile(r"#(\d+):\s*(.*)", re.DOTALL)


class EloverblikError(Exception):
    """An error from the Eloverblik API, or a payload that is not what it promises.

    Attributes:
        status: The HTTP status, or `None` when no response is involved. A failure
            reported inside a 200 response has status 200.
        error_code: Eloverblik's numeric error code, or `None`.
        error_text: Eloverblik's error text, or `None`.
        metering_point_id: The metering point concerned, or `None`.
        correlation_id: The response's `X-Correlation-ID`, or `None`.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        error_code: int | None = None,
        error_text: str | None = None,
        metering_point_id: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        """Build the error; `str()` of it is `message` only.

        Args:
            message: The human-readable message. Never holds a token.
            status: The HTTP status.
            error_code: Eloverblik's error code.
            error_text: Eloverblik's error text.
            metering_point_id: The metering point concerned.
            correlation_id: The response's `X-Correlation-ID`.
        """
        super().__init__(message)
        self.status = status
        self.error_code = error_code
        self.error_text = error_text
        self.metering_point_id = metering_point_id
        self.correlation_id = correlation_id


class MissingTokenError(EloverblikError):
    """A request was made by a client that has no refresh token."""


class EloverblikClient(ApiClient):
    """Client for the Eloverblik Customer API.

    The refresh token (from the portal) is read from `refresh_token` or the
    `ELOVERBLIK_REFRESH_TOKEN` environment variable. On its first request the client
    trades it for a 24-hour access token and reuses that for its lifetime; a 401
    triggers one refresh and one retry. `/token` allows two calls a minute, so pass
    one client to several calls. Constructing a client never fails for a missing or
    unusable token; the first request does. Neither token appears in an exception
    message or a `repr`.
    """

    def __init__(
        self,
        refresh_token: str | None = None,
        *,
        policy: RetryPolicy | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_concurrency: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Create a client for `BASE_URL`.

        Args:
            refresh_token: The refresh token. `None` reads `REFRESH_TOKEN_ENV`.
                Surrounding whitespace is stripped; an empty result counts as none.
            policy: Retry settings. Defaults to `DEFAULT_POLICY`.
            timeout: Per-request timeout in seconds.
            max_concurrency: The most requests in flight at once.
            transport: An `httpx` transport, for tests.

        Raises:
            ValueError: If `max_concurrency` is below 1.
        """
        raw = (
            os.environ.get(REFRESH_TOKEN_ENV)
            if refresh_token is None
            else refresh_token
        )
        token = (raw or "").strip()
        self._refresh = ""
        self._state = "missing"
        if token:
            if _TOKEN_PATTERN.fullmatch(token):
                self._refresh = token
                self._state = "ok"
            else:
                self._state = "unusable"
        self._access: str | None = None
        self._token_lock: asyncio.Lock | None = None
        super().__init__(
            BASE_URL,
            policy=DEFAULT_POLICY if policy is None else policy,
            timeout=timeout,
            max_concurrency=max_concurrency,
            headers={"Accept": "application/json"},
            transport=transport,
        )

    def __repr__(self) -> str:
        """Describe the client without the token."""
        return f"EloverblikClient(token={'set' if self._state == 'ok' else 'missing'})"

    @property
    def has_refresh_token(self) -> bool:
        """Whether the client holds a refresh token it can send."""
        return self._state == "ok"

    async def fetch_json(
        self,
        method: Literal["GET", "POST"],
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        body: object = None,
    ) -> dict[str, JsonValue]:
        """Send one authorised request and return the JSON object.

        The access token is fetched on first use (concurrent calls share one
        `/token` call) and reused. A 401 refreshes it once and retries once. Like
        `request`, this may only be awaited on the client's loop.

        Args:
            method: `"GET"` or `"POST"`.
            path: Appended to `BASE_URL`.
            params: Query parameters.
            body: A JSON-serialisable request body.

        Returns:
            The JSON object of the response.

        Raises:
            MissingTokenError: If the client has no refresh token, before any I/O.
            EloverblikError: For an error response, an exhausted retry or holdoff
                on a response, an unusable token, a rejected token or a 2xx body
                that is not a JSON object.
            RetriesExhaustedError: If a transport failure outlasted the retries.
            RuntimeError: If awaited outside the client's own loop.
        """
        payload, _ = await self._fetch(method, path, params, body)
        return payload

    async def _fetch(
        self,
        method: str,
        path: str,
        params: Mapping[str, str] | None,
        body: object,
    ) -> tuple[dict[str, JsonValue], str | None]:
        """`fetch_json`, also returning the response's `X-Correlation-ID`."""
        self._check_token()
        access = await self._access_token(None)
        try:
            return await self._json_with_id(method, path, params, body, _bearer(access))
        except EloverblikError as exc:
            if exc.status != 401:
                raise
        access = await self._access_token(access)
        return await self._json_with_id(method, path, params, body, _bearer(access))

    def get_json(
        self,
        method: Literal["GET", "POST"],
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        body: object = None,
    ) -> dict[str, JsonValue]:
        """Send one authorised request and return the JSON object, synchronously.

        Args:
            method: `"GET"` or `"POST"`.
            path: Appended to `BASE_URL`.
            params: Query parameters.
            body: A JSON-serialisable request body.

        Returns:
            The JSON object of the response.

        Raises:
            MissingTokenError: If the client has no refresh token.
            EloverblikError: As for `fetch_json`.
            RetriesExhaustedError: If a transport failure outlasted the retries.
            RuntimeError: If the client is closed.
        """
        return self.run(lambda: self.fetch_json(method, path, params=params, body=body))

    def _check_token(self) -> None:
        """Raise unless the client holds a refresh token it can send."""
        if self._state == "missing":
            raise MissingTokenError(
                "no Eloverblik refresh token: pass refresh_token= or set "
                f"{REFRESH_TOKEN_ENV}"
            )
        if self._state == "unusable":
            raise EloverblikError(
                "the Eloverblik refresh token has characters an HTTP header cannot "
                "carry; it must be printable ASCII without spaces"
            )

    async def _access_token(self, rejected: str | None) -> str:
        """The access token, fetched if there is none or `rejected` was refused."""
        if self._token_lock is None:
            self._token_lock = asyncio.Lock()
        async with self._token_lock:
            if self._access is not None and self._access != rejected:
                return self._access
            payload = await self._json(
                "GET", "/token", None, None, _bearer(self._refresh)
            )
            token = payload.get("result")
            if not isinstance(token, str) or not token.strip():
                raise EloverblikError(
                    "Eloverblik API: /token returned no access token", status=200
                )
            self._access = token.strip()
            return self._access

    def _secrets(self) -> tuple[str, ...]:
        """The tokens held, to be removed from anything the server says back."""
        return tuple(t for t in (self._refresh, self._access or "") if t)

    async def _json(
        self,
        method: str,
        path: str,
        params: Mapping[str, str] | None,
        body: object,
        headers: Mapping[str, str],
    ) -> dict[str, JsonValue]:
        """One request, with errors mapped to `EloverblikError`."""
        payload, _ = await self._json_with_id(method, path, params, body, headers)
        return payload

    async def _json_with_id(
        self,
        method: str,
        path: str,
        params: Mapping[str, str] | None,
        body: object,
        headers: Mapping[str, str],
    ) -> tuple[dict[str, JsonValue], str | None]:
        """`_json`, also returning the response's `X-Correlation-ID`."""
        try:
            response = await self.request_response(
                method, path, params=params, headers=headers, json=body
            )
            payload = read_response(response, "json")
        except httpx.HTTPStatusError as exc:
            raise _api_error(path, exc.response, self._secrets()) from exc
        except (RetriesExhaustedError, HoldoffTooLongError) as exc:
            if exc.response is None:
                raise
            raise _api_error(path, exc.response, self._secrets()) from exc
        except ParseError as exc:
            raise EloverblikError(
                f"Eloverblik API: the response to {path} is not valid JSON: "
                f"{exc.detail}",
                status=200,
            ) from exc
        if not isinstance(payload, dict):
            raise EloverblikError(
                f"Eloverblik API: the response to {path} is not a JSON object, "
                f"got {type(payload).__name__}",
                status=200,
            )
        return (
            cast("dict[str, JsonValue]", payload),
            response.headers.get("X-Correlation-ID"),
        )


def _bearer(token: str) -> dict[str, str]:
    """The `Authorization` header for `token`."""
    return {"Authorization": f"Bearer {token}"}


def _redact(text: str, secrets: tuple[str, ...]) -> str:
    """`text` with every token (of at least six characters) replaced by `<token>`."""
    for secret in secrets:
        if len(secret) >= _MIN_SECRET:
            text = text.replace(secret, "<token>")
    return text


def _api_error(
    path: str, response: httpx.Response, secrets: tuple[str, ...] = ()
) -> EloverblikError:
    """Build the error for an error response, reading Eloverblik's error bodies.

    A server text that echoes one of `secrets` has it replaced by `<token>`.
    """
    error_code: int | None = None
    error_text: str | None = None
    try:
        body = read_json(response.content)
    except ParseError:
        body = None
    if isinstance(body, str):
        # The real API answers some request errors with a bare JSON string,
        # "#30001: Period not allowed, ...".
        match = _STRING_BODY.fullmatch(body.strip())
        if match is not None:
            error_code = int(match.group(1))
            body = match.group(2)
        if body.strip():
            error_text = _redact(body.strip(), secrets)
    elif isinstance(body, dict):
        code = body.get("errorCode")
        if isinstance(code, int) and not isinstance(code, bool):
            error_code = code
        for key in ("errorText", "detail", "title"):
            text = body.get(key)
            if isinstance(text, str) and text:
                error_text = _redact(text, secrets)
                break
    correlation_id = response.headers.get("X-Correlation-ID")
    status = response.status_code
    message = f"Eloverblik API: HTTP {status}"
    if error_code is not None:
        message += f" error {error_code}"
    message += f" on {path}"
    if error_text:
        message += f": {error_text}"
    if path.rstrip("/").endswith("/token") and status in (401, 403):
        message += "; the refresh token was rejected (expired or revoked?)"
    if correlation_id:
        message += f" (correlation id {correlation_id})"
    return EloverblikError(
        message,
        status=status,
        error_code=error_code,
        error_text=error_text,
        correlation_id=correlation_id,
    )


def _showcase_has_token() -> bool:
    """Whether the showcases may call the live API; says so when they may not."""
    if os.environ.get(REFRESH_TOKEN_ENV, "").strip():
        return True
    print(
        f"Set {REFRESH_TOKEN_ENV} to run this showcase against the live Eloverblik API."
    )
    return False


def main() -> None:
    """Showcase this module's functionality (calls the live API)."""
    if not _showcase_has_token():
        return
    method: Literal["GET", "POST"] = "GET"  # "GET", "POST"
    path = "/meteringpoints/meteringpoints"

    with EloverblikClient() as client:
        payload = client.get_json(method, path)

    result = payload.get("result")
    points = result if isinstance(result, list) else []
    print(f"{path}: {len(points)} metering point(s)")


if __name__ == "__main__":
    main()
