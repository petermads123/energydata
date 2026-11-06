"""Metering points and their master data from Eloverblik."""

import re
from collections.abc import Sequence

import pandas as pd

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

_POINT_TEXT = (
    ("metering_point_id", "meteringPointId"),
    ("type_of_mp", "typeOfMP"),
    ("settlement_method", "settlementMethod"),
    ("meter_reading_occurrence", "meterReadingOccurrence"),
    ("balance_supplier_name", "balanceSupplierName"),
    ("street_code", "streetCode"),
    ("street_name", "streetName"),
    ("building_number", "buildingNumber"),
    ("floor_id", "floorId"),
    ("room_id", "roomId"),
    ("city_sub_division_name", "citySubDivisionName"),
    ("postcode", "postcode"),
    ("city_name", "cityName"),
    ("municipality_code", "municipalityCode"),
    ("location_description", "locationDescription"),
    ("meter_number", "meterNumber"),
    ("consumer_start_date", "consumerStartDate"),
)
_POINT_FLAGS = (("has_relation", "hasRelation"), ("is_moved_out", "isMovedOut"))
_POINT_COLUMNS = (
    *(column for column, _ in _POINT_TEXT),
    *(column for column, _ in _POINT_FLAGS),
    "child_metering_point_ids",
)

# Scalar fields of MeteringPointDetailsCustomerDto in spec order; the nested
# contactAddresses and childMeteringPoints are left out.
_DETAIL_FIELDS = [
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
_NUMERIC_DETAIL = "powerLimitKWDecimal"


def _snake(name: str) -> str:
    """An API field name as a snake_case column name."""
    step = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    step = re.sub(r"([a-z\d])([A-Z])", r"\1_\2", step)
    return re.sub(r"_+", "_", step.lower())


_DETAIL_COLUMNS = tuple(_snake(name) for name in _DETAIL_FIELDS)


def _point_frame(result: list[JsonValue]) -> pd.DataFrame:
    """Check a listing and build the metering point frame."""
    columns: dict[str, list[object]] = {name: [] for name in _POINT_COLUMNS}
    for position, item in enumerate(result):
        label = f"/meteringpoints/meteringpoints: point {position}"
        if not isinstance(item, dict):
            raise EloverblikError(f"{label} is not a JSON object")
        for column, source in _POINT_TEXT:
            if column == "metering_point_id":
                columns[column].append(_text(item, source, label))
            else:
                columns[column].append(_nullable_text(item, source, label))
        for column, source in _POINT_FLAGS:
            flag = item.get(source)
            if not isinstance(flag, bool):
                raise EloverblikError(f"{label}: '{source}' is not a boolean")
            columns[column].append(flag)
        children = item.get("childMeteringPoints")
        if children is not None and not isinstance(children, list):
            raise EloverblikError(f"{label}: 'childMeteringPoints' is not a list")
        ids: list[str] = []
        for child in children or []:
            if not isinstance(child, dict):
                raise EloverblikError(f"{label}: a child point is not a JSON object")
            ids.append(_text(child, "meteringPointId", f"{label} child"))
        columns["child_metering_point_ids"].append(tuple(ids))
    flags = {column for column, _ in _POINT_FLAGS}
    return pd.DataFrame(
        {
            name: pd.Series(values, dtype=bool if name in flags else object)
            for name, values in columns.items()
        }
    )


def get_metering_points(
    *,
    include_unlinked: bool = False,
    client: EloverblikClient | None = None,
) -> pd.DataFrame:
    """Get the account's metering points.

    | Property | Value |
    |---|---|
    | Endpoint | `GET /meteringpoints/meteringpoints` |
    | Index | `RangeIndex`, one row per point |
    | Columns | `metering_point_id`, `type_of_mp`, `settlement_method`, `meter_reading_occurrence`, `balance_supplier_name`, `street_code`, `street_name`, `building_number`, `floor_id`, `room_id`, `city_sub_division_name`, `postcode`, `city_name`, `municipality_code`, `location_description`, `meter_number`, `consumer_start_date` (object, `None` where null), `has_relation`, `is_moved_out` (`bool`), `child_metering_point_ids` (a tuple of ids per row) |

    Consumer names and CPR/CVR fields are left out. No points gives an empty
    frame with the same columns.

    Args:
        include_unlinked: Also list points registered to the user's CPR/CVR that
            are not linked yet (`includeAll=true`).
        client: A client to fetch with. `None` creates one and closes it before
            returning; a passed client is left open.

    Returns:
        The frame described above.

    Raises:
        MissingTokenError: If there is no refresh token.
        EloverblikError: If the API refuses the request or returns an unexpected
            payload.
        RetriesExhaustedError: If a network failure outlasted the retries.
        RuntimeError: If a passed `client` is closed.
    """
    params = {"includeAll": "true" if include_unlinked else "false"}

    async def work(active: EloverblikClient) -> pd.DataFrame:
        payload = await active.fetch_json(
            "GET", "/meteringpoints/meteringpoints", params=params
        )
        result = payload.get("result")
        if not isinstance(result, list):
            raise EloverblikError(
                "/meteringpoints/meteringpoints: the response has no 'result' list"
            )
        return _point_frame(result)

    with _client_scope(client) as active:
        return active.run(lambda: work(active))


def get_metering_point_details(
    metering_point_ids: str | Sequence[str] | None = None,
    *,
    client: EloverblikClient | None = None,
) -> pd.DataFrame:
    """Get the master data of metering points.

    | Property | Value |
    |---|---|
    | Endpoint | `POST /meteringpoints/meteringpoint/getdetails` |
    | Index | `RangeIndex`, one row per point in request order |
    | Columns | every scalar field of the API's details object in its order, as snake_case (`typeOfMP` is `type_of_mp`, `powerLimitKWDecimal` is `power_limit_kw_decimal`) |

    `power_limit_kw_decimal` is `float64` (NaN where null); every other column is
    object (`None` where null). The nested contact addresses and child points are
    left out.

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
    path = "/meteringpoints/meteringpoint/getdetails"

    async def work(active: EloverblikClient) -> list[dict[str, JsonValue]]:
        ids = await _resolve_ids(active, metering_point_ids)
        return await _post_points(active, path, ids)

    with _client_scope(client) as active:
        results = active.run(lambda: work(active))
    columns: dict[str, list[object]] = {name: [] for name in _DETAIL_COLUMNS}
    for result in results:
        with _carrying(result):
            _unpack(result, path)
            details = result.get("result")
            label = f"{path}: point {result.get('id')}"
            if not isinstance(details, dict):
                raise EloverblikError(f"{label}: 'result' is not a JSON object")
            for field, column in zip(_DETAIL_FIELDS, _DETAIL_COLUMNS, strict=True):
                if field == _NUMERIC_DETAIL:
                    value = details.get(field)
                    columns[column].append(_number(value, field, label, nullable=True))
                else:
                    columns[column].append(_nullable_text(details, field, label))
    return pd.DataFrame(
        {
            name: pd.Series(
                values, dtype=float if name == "power_limit_kw_decimal" else object
            )
            for name, values in columns.items()
        }
    )


def main() -> None:
    """Showcase this module's functionality (calls the live API)."""
    if not _showcase_has_token():
        return
    include_unlinked = False  # True, False

    with EloverblikClient() as client:
        points = get_metering_points(include_unlinked=include_unlinked, client=client)

        print(f"{len(points)} metering point(s)")
        print(points[["type_of_mp", "settlement_method", "has_relation"]])

        # Master data for every linked point.
        metering_point_ids = None

        details = get_metering_point_details(metering_point_ids, client=client)

        print(f"details: {details.shape}")
        print(details[["type_of_mp", "power_limit_kw_decimal"]])


if __name__ == "__main__":
    main()
