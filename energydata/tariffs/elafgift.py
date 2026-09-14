"""Danish electricity tax ("elafgift").

Fetches the elafgift charge from the ``DatahubPricelist`` dataset and
expands it onto a uniform UTC 15-minute grid.
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
from energydata.tariffs.registry import ELAFGIFT_CHARGE_CODE, ELAFGIFT_GLN


def get_elafgift(start: TimeInput, end: TimeInput) -> pd.DataFrame:
    """Get the Danish electricity tax (elafgift) on a UTC 15-minute grid.

    Deliberately does not pass ``start``/``end`` to :func:`fetch_dataset`:
    the API's date filter applies to ``ValidFrom``, which would silently
    drop the currently-active (open-ended ``ValidTo``) record. Matching
    is also done on ``GLN_Number``/``ChargeTypeCode`` rather than the
    ``ChargeOwner`` string (e.g. ``"Energinet Systemansvar A/S (SYO)"``),
    which is brittle.

    Args:
        start: Start of the requested period (inclusive), in
            :data:`~energydata.common.timeutils.TimeInput` form.
        end: End of the requested period (exclusive).

    Returns:
        A DataFrame with columns ``UTC`` (tz-aware UTC 15-minute grid,
        half-open ``[start, end)``) and ``Elafgift`` (``float64``,
        DKK/kWh excl. VAT). Slots with no active validity window are
        ``NaN``.

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
        filters={"GLN_Number": [ELAFGIFT_GLN], "ChargeTypeCode": [ELAFGIFT_CHARGE_CODE]},
    )
    if not records:
        raise NoDataError(
            "no DatahubPricelist records found for elafgift "
            f"(GLN {ELAFGIFT_GLN}, ChargeTypeCode {ELAFGIFT_CHARGE_CODE!r})"
        )

    result = pd.DataFrame({"UTC": index})
    # .to_numpy() assigns by position, avoiding index-label alignment
    # between `result`'s default RangeIndex and the UTC-grid-indexed Series.
    result["Elafgift"] = expand_pricelist_to_15min(records, index).to_numpy()
    return result


def main() -> None:
    """Demonstrate :func:`get_elafgift`."""
    # Use "yesterday -> today" (Danish local dates) as a small live example.
    now_local = pd.Timestamp.now(tz=DK_TZ)
    yesterday = (now_local - pd.Timedelta(days=1)).date().isoformat()
    today = now_local.date().isoformat()

    df = get_elafgift(yesterday, today)
    print(f"get_elafgift({yesterday!r}, {today!r}) -> {len(df)} rows")
    print(df.head())


if __name__ == "__main__":
    main()
