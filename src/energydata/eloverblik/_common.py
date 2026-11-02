"""Private shared path of the point-scoped Eloverblik functions.

Holds metering point id normalisation, the linked-point default, batching by 10,
per-point result unpacking, payload-shape helpers and the client scope that
`meteringpoints.py`, `meterdata.py` and `charges.py` are thin layers over.
"""

import math
import re
from collections.abc import Awaitable, Callable, Collection, Iterator, Sequence
from contextlib import contextmanager

from energydata.utils.chunking import gather_ordered
from energydata.utils.readers import JsonValue

from .client import EloverblikClient, EloverblikError, _showcase_has_token

BATCH_SIZE = 10
_ID_PATTERN = re.compile(r"\d{18}")


def _normalise_ids(metering_point_ids: str | Sequence[str]) -> list[str]:
    """Strip and check metering point ids, keeping their order.

    Raises:
        ValueError: For an empty sequence, an id that is not 18 digits, or a
            duplicate; the message names the offender.
    """
    raw = (
        [metering_point_ids]
        if isinstance(metering_point_ids, str)
        else list(metering_point_ids)
    )
    if not raw:
        raise ValueError("metering_point_ids must not be empty")
    ids: list[str] = []
    for item in raw:
        text = item.strip() if isinstance(item, str) else item
        if not isinstance(text, str) or not _ID_PATTERN.fullmatch(text):
            raise ValueError(
                f"a metering point id must be exactly 18 digits, got {item!r}"
            )
        if text in ids:
            raise ValueError(f"metering point id {text!r} is given twice")
        ids.append(text)
    return ids


@contextmanager
def _client_scope(client: EloverblikClient | None) -> Iterator[EloverblikClient]:
    """Yield the passed client, or a new one that is closed afterwards."""
    if client is not None:
        yield client
        return
    created = EloverblikClient()
    try:
        yield created
    finally:
        created.close()


def _batches(ids: Sequence[str]) -> list[list[str]]:
    """Split `ids` into batches of at most `BATCH_SIZE`, keeping their order."""
    return [list(ids[i : i + BATCH_SIZE]) for i in range(0, len(ids), BATCH_SIZE)]


async def _linked_ids(client: EloverblikClient) -> list[str]:
    """The ids of the linked metering points, in listing order."""
    payload = await client.fetch_json(
        "GET", "/meteringpoints/meteringpoints", params={"includeAll": "false"}
    )
    result = payload.get("result")
    if not isinstance(result, list):
        raise EloverblikError(
            "/meteringpoints/meteringpoints: the response has no 'result' list"
        )
    ids: list[str] = []
    for position, item in enumerate(result):
        if not isinstance(item, dict):
            raise EloverblikError(
                f"/meteringpoints/meteringpoints: point {position} is not a JSON object"
            )
        ids.append(_text(item, "meteringPointId", f"point {position}"))
    return ids


async def _resolve_ids(
    client: EloverblikClient, metering_point_ids: str | Sequence[str] | None
) -> list[str]:
    """The ids to work on: those given (checked), else every linked point.

    Raises:
        ValueError: As `_normalise_ids`, or when none is given and none is linked.
    """
    if metering_point_ids is not None:
        return _normalise_ids(metering_point_ids)
    linked = await _linked_ids(client)
    if not linked:
        raise ValueError(
            "no metering points are linked to this account: link one in the "
            "Eloverblik portal or pass metering_point_ids="
        )
    return linked


async def _post_points(
    client: EloverblikClient, path: str, ids: Sequence[str]
) -> list[dict[str, JsonValue]]:
    """`POST` `ids` to `path` in batches and return the per-point results.

    Results are matched to the ids by their `id`, not by position. The list is
    in request order; a repeated id keeps all its results.

    Raises:
        EloverblikError: For a result for an id that was not requested, a
            requested id with no result, or a response without a `result` list.
    """

    async def one(batch: list[str]) -> list[dict[str, JsonValue]]:
        body = {"meteringPoints": {"meteringPoint": batch}}
        payload = await client.fetch_json("POST", path, body=body)
        result = payload.get("result")
        if not isinstance(result, list):
            raise EloverblikError(f"{path}: the response has no 'result' list")
        found: list[dict[str, JsonValue]] = []
        for item in result:
            if not isinstance(item, dict):
                raise EloverblikError(f"{path}: a result is not a JSON object")
            if item.get("id") not in batch:
                raise EloverblikError(
                    f"{path}: a result for metering point {item.get('id')!r}, "
                    "which was not requested",
                    metering_point_id=_id_of(item),
                )
            found.append(item)
        for point_id in batch:
            if not any(item["id"] == point_id for item in found):
                raise EloverblikError(
                    f"{path}: no result for metering point {point_id}",
                    metering_point_id=point_id,
                )
        return found

    def bind(
        batch: list[str],
    ) -> Callable[[], Awaitable[list[dict[str, JsonValue]]]]:
        return lambda: one(batch)

    parts = await gather_ordered([bind(batch) for batch in _batches(ids)])
    flat = [item for part in parts for item in part]
    return [item for point_id in ids for item in flat if item["id"] == point_id]


def _id_of(item: dict[str, JsonValue]) -> str | None:
    """The result's `id` if it is text."""
    value = item.get("id")
    return value if isinstance(value, str) else None


def _unpack(
    result: dict[str, JsonValue],
    path: str,
    tolerated: Collection[int] = (),
) -> bool:
    """Whether a per-point `result` succeeded; raise for a failure not tolerated.

    Returns:
        True for a success, False for a failure whose code is in `tolerated`.

    Raises:
        EloverblikError: For any other failure, carrying status 200, the code, the
            text and the metering point.
    """
    point_id = _id_of(result)
    if result.get("success") is True:
        return True
    code = result.get("errorCode")
    error_code = code if isinstance(code, int) and not isinstance(code, bool) else None
    text = result.get("errorText")
    error_text = text if isinstance(text, str) else None
    if error_code is not None and error_code in tolerated:
        return False
    message = f"Eloverblik API: {path} failed for metering point {point_id}"
    if error_code is not None:
        message += f" with error {error_code}"
    if error_text:
        message += f": {error_text}"
    raise EloverblikError(
        message,
        status=200,
        error_code=error_code,
        error_text=error_text,
        metering_point_id=point_id,
    )


def _text(item: dict[str, JsonValue], key: str, label: str) -> str:
    """A required string field."""
    value = item.get(key)
    if not isinstance(value, str):
        raise EloverblikError(f"{label}: '{key}' is missing or not text")
    return value


def _nullable_text(item: dict[str, JsonValue], key: str, label: str) -> str | None:
    """A string-or-null field; absent counts as null."""
    value = item.get(key)
    if value is not None and not isinstance(value, str):
        raise EloverblikError(f"{label}: '{key}' is not text or null")
    return value


def _number(value: object, key: str, label: str, *, nullable: bool = False) -> float:
    """A finite number; `None` is NaN where `nullable`."""
    if value is None and nullable:
        return math.nan
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise EloverblikError(f"{label}: '{key}' is not a number, got {value!r}")
    try:
        number = float(value)
    except OverflowError as exc:
        raise EloverblikError(f"{label}: '{key}' is too large") from exc
    if not math.isfinite(number):
        raise EloverblikError(f"{label}: '{key}' is not finite, got {value!r}")
    return number


def main() -> None:
    """Showcase id normalisation and batching (offline)."""
    metering_point_ids = [f"57131310000000{n:04d}" for n in range(1, 24)]
    padded = "  571313100000000001  "

    batches = _batches(_normalise_ids(metering_point_ids))
    single = _normalise_ids(padded)

    print(f"{len(metering_point_ids)} ids -> batches of {[len(b) for b in batches]}")
    print(f"a padded string is one id: {single}")

    # A malformed id is refused before any request.
    metering_point_ids = ["57131310000000001"]

    try:
        _normalise_ids(metering_point_ids)
    except ValueError as exc:
        print(f"refused: {exc}")

    if _showcase_has_token():
        with EloverblikClient() as client:
            linked = client.run(lambda: _linked_ids(client))
        print(f"{len(linked)} linked metering point(s)")


if __name__ == "__main__":
    main()
