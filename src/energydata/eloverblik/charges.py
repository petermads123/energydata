"""Charges (subscriptions, tariffs and fees) linked to metering points."""

import math
from collections.abc import Sequence

import pandas as pd

from energydata.utils.periods import DANISH_TZ
from energydata.utils.readers import JsonValue

from ._common import (
    _carrying,
    _client_scope,
    _nullable_text,
    _number,
    _post_points,
    _resolve_ids,
    _text,
    _unpack,
)
from .client import EloverblikClient, EloverblikError, _showcase_has_token

_PATH = "/meteringpoints/meteringpoint/getcharges"
_KINDS = (("subscriptions", "subscription"), ("tariffs", "tariff"), ("fees", "fee"))


def _stamp(value: str | None, label: str) -> pd.Timestamp | None:
    """A charge date as a Copenhagen timestamp; a naive text is Danish time."""
    if not value:
        return None
    try:
        stamp = pd.Timestamp(value)
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize(DANISH_TZ)
        return stamp.tz_convert(DANISH_TZ).as_unit("ns")
    except (ValueError, OverflowError) as exc:
        raise EloverblikError(f"{label} is not a usable date: {value!r}") from exc


def _integer(value: object, key: str, label: str) -> int:
    """A whole number from a number or a text."""
    if isinstance(value, bool) or value is None:
        raise EloverblikError(f"{label}: '{key}' is not an integer, got {value!r}")
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError as exc:
            raise EloverblikError(
                f"{label}: '{key}' is not an integer, got {value!r}"
            ) from exc
    number = _number(value, key, label)
    if number != math.floor(number):
        raise EloverblikError(f"{label}: '{key}' is not an integer, got {value!r}")
    return int(number)


def _rows(point_id: str, result: dict[str, JsonValue]) -> list[dict[str, object]]:
    """The charge rows of one point's result."""
    body = result.get("result")
    label = f"{_PATH}: point {point_id}"
    if not isinstance(body, dict):
        raise EloverblikError(f"{label}: 'result' is not a JSON object")
    rows: list[dict[str, object]] = []
    for key, kind in _KINDS:
        charges = body.get(key)
        if charges is not None and not isinstance(charges, list):
            raise EloverblikError(f"{label}: '{key}' is not a list")
        for position, charge in enumerate(charges or []):
            what = f"{label}: {kind} {position}"
            if not isinstance(charge, dict):
                raise EloverblikError(f"{what} is not a JSON object")
            common: dict[str, object] = {
                "metering_point_id": point_id,
                "kind": kind,
                "price_id": _nullable_text(charge, "priceId", what),
                "name": _nullable_text(charge, "name", what),
                "description": _nullable_text(charge, "description", what),
                "owner": _nullable_text(charge, "owner", what),
                "period_type": _nullable_text(charge, "periodType", what),
                "valid_from": _stamp(
                    _nullable_text(charge, "validFromDate", what),
                    f"{what} 'validFromDate'",
                ),
                "valid_to": _stamp(
                    _nullable_text(charge, "validToDate", what), f"{what} 'validToDate'"
                ),
            }
            if kind == "tariff":
                prices = charge.get("prices")
                if prices is not None and not isinstance(prices, list):
                    raise EloverblikError(f"{what}: 'prices' is not a list")
                if not prices:
                    rows.append(
                        common | {"price": math.nan, "quantity": None, "position": None}
                    )
                steps: list[dict[str, object]] = []
                for step, entry in enumerate(prices or []):
                    note = f"{what} price {step}"
                    if not isinstance(entry, dict):
                        raise EloverblikError(f"{note} is not a JSON object")
                    raw = entry.get("position")
                    steps.append(
                        {
                            "price": _number(entry.get("price"), "price", note),
                            "quantity": None,
                            "position": (
                                None if raw is None else _integer(raw, "position", note)
                            ),
                        }
                    )
                # By position, a price without one last; the API order breaks ties.
                steps.sort(key=lambda r: (r["position"] is None, r["position"] or 0))
                rows.extend(common | step_row for step_row in steps)
            else:
                quantity = charge.get("quantity")
                rows.append(
                    common
                    | {
                        "price": _number(charge.get("price"), "price", what),
                        "quantity": _integer(quantity, "quantity", what),
                        "position": None,
                    }
                )
    return rows


def get_charges(
    metering_point_ids: str | Sequence[str] | None = None,
    *,
    client: EloverblikClient | None = None,
) -> pd.DataFrame:
    """Get the subscriptions, tariffs and fees linked to metering points.

    | Property | Value |
    |---|---|
    | Endpoint | `POST /meteringpoints/meteringpoint/getcharges` |
    | Index | `RangeIndex`, one row per charge and price position |
    | Columns | `metering_point_id`, `kind` (`"subscription"`, `"tariff"`, `"fee"`), `price_id`, `name`, `description`, `owner`, `period_type` (object); `valid_from`, `valid_to` (`datetime64[ns, Europe/Copenhagen]`, NaT where null); `price` (`float64`); `quantity`, `position` (`Int64`, `<NA>` where the kind has none) |

    Per point in request order: subscriptions, tariffs (each price position its
    own row, by position), then fees. A tariff with no prices is still listed, as
    one row with a NaN price. No charges gives an empty frame with the same
    columns and dtypes. Charges linked at the time of the request or later are
    returned; past and future changes to an existing charge are not.

    Args:
        metering_point_ids: One 18-digit id or a sequence of them. `None` means
            every linked point.
        client: A client to fetch with. `None` creates one and closes it before
            returning; a passed client is left open.

    Returns:
        The frame described above.

    Raises:
        ValueError: If an id is malformed or repeated, the sequence is empty, or
            `metering_point_ids` is `None` and no point is linked.
        MissingTokenError: If there is no refresh token.
        EloverblikError: If the API refuses a request, reports a failure for a
            point, or returns an unexpected payload.
        RetriesExhaustedError: If a network failure outlasted the retries.
        RuntimeError: If a passed `client` is closed.
    """

    async def work(active: EloverblikClient) -> list[dict[str, JsonValue]]:
        ids = await _resolve_ids(active, metering_point_ids)
        return await _post_points(active, _PATH, ids)

    with _client_scope(client) as active:
        results = active.run(lambda: work(active))
    rows: list[dict[str, object]] = []
    for result in results:
        with _carrying(result):
            _unpack(result, _PATH)
            rows.extend(_rows(_text(result, "id", _PATH), result))

    def column(name: str) -> list[object]:
        return [row[name] for row in rows]

    def stamps(name: str) -> pd.Series:
        values = pd.to_datetime(pd.Series(column(name), dtype=object), utc=True)
        return values.dt.tz_convert(DANISH_TZ).dt.as_unit("ns")

    return pd.DataFrame(
        {
            "metering_point_id": pd.Series(column("metering_point_id"), dtype=object),
            "kind": pd.Series(column("kind"), dtype=object),
            "price_id": pd.Series(column("price_id"), dtype=object),
            "name": pd.Series(column("name"), dtype=object),
            "description": pd.Series(column("description"), dtype=object),
            "owner": pd.Series(column("owner"), dtype=object),
            "period_type": pd.Series(column("period_type"), dtype=object),
            "valid_from": stamps("valid_from"),
            "valid_to": stamps("valid_to"),
            "price": pd.Series(column("price"), dtype="float64"),
            "quantity": pd.Series(column("quantity"), dtype="Int64"),
            "position": pd.Series(column("position"), dtype="Int64"),
        }
    )


def main() -> None:
    """Showcase this module's functionality (calls the live API)."""
    if not _showcase_has_token():
        return
    metering_point_ids = None  # None for every linked point, or "571313..." ids

    charges = get_charges(metering_point_ids)

    print(f"{len(charges)} charge row(s)")
    print(charges.groupby("kind").size())


if __name__ == "__main__":
    main()
