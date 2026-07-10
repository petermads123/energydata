"""Distribution system operator (DSO) grid tariffs.

Fetches per-DSO grid tariffs (e.g. Radius' "Nettarif C") from the
``DatahubPricelist`` dataset and expands them onto a uniform UTC
15-minute grid.
"""

from typing import Any

import pandas as pd

from energydata.common.client import fetch_dataset
from energydata.common.errors import NoDataError
from energydata.common.timeutils import (
    DK_TZ,
    TimeInput,
    build_utc_15min_index,
    to_utc_timestamp,
)
from energydata.tariffs.expand import expand_pricelist_to_15min
from energydata.tariffs.registry import DsoConfig, lookup_dso


def _normalize_dso_names(dso: str | list[str]) -> list[str]:
    """Normalize the ``dso`` argument into a de-duplicated list of names.

    A single string is treated as a one-element list. Duplicates are
    dropped case-insensitively (e.g. ``["Radius", "radius"]`` collapses to
    a single request), preserving the order of first appearance.

    Args:
        dso: A single DSO name, or a list of DSO names.

    Returns:
        The de-duplicated list of requested DSO names, in input order.
    """
    raw_names = [dso] if isinstance(dso, str) else list(dso)

    seen: set[str] = set()
    unique_names: list[str] = []
    for name in raw_names:
        key = name.lower()
        if key not in seen:
            seen.add(key)
            unique_names.append(name)
    return unique_names


def _fetch_dso_records(config: DsoConfig) -> list[dict[str, Any]]:
    """Fetch every ``DatahubPricelist`` record for one DSO's grid tariff.

    Deliberately does not pass ``start``/``end`` to :func:`fetch_dataset`:
    the API's date filter applies to ``ValidFrom``, which would silently
    drop the currently-active (typically open-ended ``ValidTo``) record.
    Instead, every record for the DSO's charge is fetched and the correct
    validity window is selected client-side in
    :func:`energydata.tariffs.expand.expand_pricelist_to_15min`.

    Args:
        config: The DSO's identifiers (GLN + charge type code).

    Returns:
        The raw records for this DSO's charge (all validity windows).

    Raises:
        NoDataError: If the query returns zero records at all, which
            indicates a wrong/unregistered GLN or charge type code.
    """
    records = fetch_dataset(
        "DatahubPricelist",
        filters={"GLN_Number": [config.gln], "ChargeTypeCode": [config.charge_type_code]},
    )
    if not records:
        raise NoDataError(
            f"no DatahubPricelist records found for DSO {config.name!r} "
            f"(GLN {config.gln}, ChargeTypeCode {config.charge_type_code!r})"
        )
    return records


def get_dso_tariffs(
    start: TimeInput, end: TimeInput, dso: str | list[str] = "Radius"
) -> pd.DataFrame:
    """Get one or more DSOs' grid tariffs on a UTC 15-minute grid.

    Args:
        start: Start of the requested period (inclusive), in
            :data:`~energydata.common.timeutils.TimeInput` form.
        end: End of the requested period (exclusive).
        dso: A single DSO name or a list of DSO names, looked up
            case-insensitively in :data:`~energydata.tariffs.registry.DSO_REGISTRY`.
            Defaults to ``"Radius"``.

    Returns:
        A DataFrame with a ``UTC`` column (tz-aware UTC 15-minute grid,
        half-open ``[start, end)``) plus one ``float64`` column per
        requested DSO, named with the DSO's canonical registry name.
        Values are DKK/kWh excl. VAT; slots with no active validity
        window are ``NaN``.

    Raises:
        InvalidPeriodError: If ``start``/``end`` cannot be parsed, or
            ``start >= end``.
        UnknownAreaError: If a requested DSO name is not registered.
        NoDataError: If a requested DSO has zero records at all.
    """
    start_utc = to_utc_timestamp(start)
    end_utc = to_utc_timestamp(end)
    index = build_utc_15min_index(start_utc, end_utc)

    names = _normalize_dso_names(dso)

    result = pd.DataFrame({"UTC": index})
    for name in names:
        # Resolves case-insensitively and raises UnknownAreaError (listing
        # the registry keys) for unrecognized names.
        config = lookup_dso(name)
        records = _fetch_dso_records(config)
        # Use .to_numpy() to assign by position: `result` has a default
        # RangeIndex while the expanded Series is indexed by the UTC grid,
        # so a plain Series assignment would try to align on index labels.
        result[config.name] = expand_pricelist_to_15min(records, index).to_numpy()
    return result


def main() -> None:
    """Demonstrate :func:`get_dso_tariffs` for the default (Radius) DSO."""
    # Use "yesterday -> today" (Danish local dates) as a small live example.
    now_local = pd.Timestamp.now(tz=DK_TZ)
    yesterday = (now_local - pd.Timedelta(days=1)).date().isoformat()
    today = now_local.date().isoformat()

    df = get_dso_tariffs(yesterday, today)
    print(f"get_dso_tariffs({yesterday!r}, {today!r}) -> {len(df)} rows")
    print(df.head())

    # A single DSO given as a list, and duplicate/differently-cased names
    # collapsing to one column.
    df_list = get_dso_tariffs(yesterday, today, dso=["Radius", "radius"])
    print("Columns for dso=['Radius', 'radius']:", list(df_list.columns))


if __name__ == "__main__":
    main()
