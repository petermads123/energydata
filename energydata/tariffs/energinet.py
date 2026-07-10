"""Energinet (Danish TSO) transmission and system tariffs.

Fetches Energinet's transmission grid tariff and system tariff from the
``DatahubPricelist`` dataset in a single request and expands both onto a
uniform UTC 15-minute grid.
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
from energydata.tariffs.registry import (
    ENERGINET_GLN,
    ENERGINET_SYSTEM_CODE,
    ENERGINET_TRANSMISSION_CODE,
)


def get_energinet_tariffs(start: TimeInput, end: TimeInput) -> pd.DataFrame:
    """Get Energinet's transmission and system tariffs on a UTC 15-minute grid.

    Both tariffs share the same owner GLN, so they are fetched in a single
    ``DatahubPricelist`` query and split client-side by
    ``ChargeTypeCode``. Deliberately does not pass ``start``/``end`` to
    :func:`fetch_dataset`: the API's date filter applies to ``ValidFrom``,
    which would silently drop the currently-active (open-ended
    ``ValidTo``) record.

    Args:
        start: Start of the requested period (inclusive), in
            :data:`~energydata.common.timeutils.TimeInput` form.
        end: End of the requested period (exclusive).

    Returns:
        A DataFrame with columns ``UTC`` (tz-aware UTC 15-minute grid,
        half-open ``[start, end)``), ``Transmission``, and ``System``
        (both ``float64``, DKK/kWh excl. VAT). Slots with no active
        validity window are ``NaN``.

    Raises:
        InvalidPeriodError: If ``start``/``end`` cannot be parsed, or
            ``start >= end``.
        NoDataError: If the query returns zero records at all.
    """
    start_utc = to_utc_timestamp(start)
    end_utc = to_utc_timestamp(end)
    index = build_utc_15min_index(start_utc, end_utc)

    records: list[dict[str, Any]] = fetch_dataset(
        "DatahubPricelist",
        filters={
            "GLN_Number": [ENERGINET_GLN],
            "ChargeTypeCode": [ENERGINET_TRANSMISSION_CODE, ENERGINET_SYSTEM_CODE],
        },
    )
    if not records:
        raise NoDataError(
            "no DatahubPricelist records found for Energinet tariffs "
            f"(GLN {ENERGINET_GLN}, ChargeTypeCode "
            f"{ENERGINET_TRANSMISSION_CODE!r}/{ENERGINET_SYSTEM_CODE!r})"
        )

    # Split the combined result client-side into the two individual tariffs.
    transmission_records = [
        record for record in records if record.get("ChargeTypeCode") == ENERGINET_TRANSMISSION_CODE
    ]
    system_records = [
        record for record in records if record.get("ChargeTypeCode") == ENERGINET_SYSTEM_CODE
    ]

    result = pd.DataFrame({"UTC": index})
    # .to_numpy() assigns by position, avoiding index-label alignment
    # between `result`'s default RangeIndex and the UTC-grid-indexed Series.
    result["Transmission"] = expand_pricelist_to_15min(transmission_records, index).to_numpy()
    result["System"] = expand_pricelist_to_15min(system_records, index).to_numpy()
    return result


def main() -> None:
    """Demonstrate :func:`get_energinet_tariffs`."""
    # Use "yesterday -> today" (Danish local dates) as a small live example.
    now_local = pd.Timestamp.now(tz=DK_TZ)
    yesterday = (now_local - pd.Timedelta(days=1)).date().isoformat()
    today = now_local.date().isoformat()

    df = get_energinet_tariffs(yesterday, today)
    print(f"get_energinet_tariffs({yesterday!r}, {today!r}) -> {len(df)} rows")
    print(df.head())


if __name__ == "__main__":
    main()
