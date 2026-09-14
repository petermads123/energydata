"""Expand ``DatahubPricelist`` records onto a uniform UTC 15-minute grid.

The ``DatahubPricelist`` dataset represents a tariff as a sequence of
*validity windows* (``ValidFrom``/``ValidTo``, Danish local wall-clock,
naive), each carrying either 24 hourly prices (``Price1``..``Price24`` for
``ResolutionDuration == "PT1H"``) or a single flat price (``Price1`` for
``"P1D"``/``"P1M"`` and anything else we treat as flat). This module turns
that representation into a plain ``float64`` :class:`pandas.Series` aligned
to a UTC 15-minute grid, which is what the public ``tariffs`` functions
return as DataFrame columns.
"""

from typing import Any

import numpy as np
import pandas as pd

from energydata.common.timeutils import DK_TZ

# Records use ResolutionDuration == "PT1H" for hourly-varying tariffs; every
# other value (e.g. "P1D", "P1M") is treated as a single flat daily/monthly
# price living in Price1.
_HOURLY_RESOLUTION = "PT1H"


def _price_or_nan(record: dict[str, Any], hour_number: int) -> float:
    """Read ``Price{hour_number}`` from a record, defaulting missing/null to NaN.

    Args:
        record: A single ``DatahubPricelist`` record.
        hour_number: 1-based price slot, i.e. ``1`` for ``Price1`` up to
            ``24`` for ``Price24``.

    Returns:
        The price as a float, or ``float("nan")`` if the field is absent or
        null in the record.
    """
    value = record.get(f"Price{hour_number}")
    if value is None:
        return float("nan")
    return float(value)


def _parse_valid_from(record: dict[str, Any]) -> pd.Timestamp:
    """Parse a record's required ``ValidFrom`` field.

    Args:
        record: A single ``DatahubPricelist`` record.

    Returns:
        A naive :class:`pandas.Timestamp` representing the Danish local
        wall-clock start of the record's validity window.
    """
    return pd.Timestamp(record["ValidFrom"])


def _parse_valid_to(record: dict[str, Any]) -> pd.Timestamp | None:
    """Parse a record's optional ``ValidTo`` field.

    Args:
        record: A single ``DatahubPricelist`` record.

    Returns:
        A naive :class:`pandas.Timestamp` representing the Danish local
        wall-clock end of the record's validity window (exclusive), or
        ``None`` if the record is open-ended (``ValidTo`` is ``null``).
    """
    valid_to = record.get("ValidTo")
    if valid_to is None:
        return None
    return pd.Timestamp(valid_to)


def select_active_record(
    records: list[dict[str, Any]], at_local: pd.Timestamp
) -> dict[str, Any] | None:
    """Select the record whose validity window covers a local timestamp.

    A record is active at ``at_local`` when ``ValidFrom <= at_local <
    ValidTo`` (an open-ended, ``None``, ``ValidTo`` covers everything from
    ``ValidFrom`` onwards). If several records' windows overlap at
    ``at_local`` the one with the latest ``ValidFrom`` wins, matching how
    the datahub represents corrections/updates to a tariff.

    Args:
        records: Raw ``DatahubPricelist`` records (already filtered to a
            single charge, e.g. one DSO/charge-type-code combination).
        at_local: The point in time to test, interpreted as Danish local
            wall-clock time. May be tz-aware (e.g. in ``Europe/Copenhagen``)
            or a naive local timestamp; tz-aware input has its tzinfo
            stripped before comparing against the naive ``ValidFrom``/
            ``ValidTo`` fields.

    Returns:
        The active record, or ``None`` if no record's validity window
        covers ``at_local``.

    Note:
        This is a deliberately-provided standalone utility for inspecting a
        single point in time. :func:`expand_pricelist_to_15min` implements
        the same selection rules (window containment + latest-``ValidFrom``
        tie-break) in vectorized form for whole grids; keep the two in sync
        if the selection semantics ever change.
    """
    # ValidFrom/ValidTo are naive Danish local wall-clock times in the raw
    # API data, so drop any tzinfo on the query point before comparing.
    at_naive = at_local.tz_localize(None) if at_local.tzinfo is not None else at_local

    best_record: dict[str, Any] | None = None
    best_valid_from: pd.Timestamp | None = None
    for record in records:
        valid_from = _parse_valid_from(record)
        valid_to = _parse_valid_to(record)
        is_active = valid_from <= at_naive and (valid_to is None or at_naive < valid_to)
        if not is_active:
            continue
        # Tie-break overlapping windows by keeping the latest ValidFrom.
        if best_valid_from is None or valid_from > best_valid_from:
            best_record = record
            best_valid_from = valid_from
    return best_record


def expand_pricelist_to_15min(
    records: list[dict[str, Any]], index: pd.DatetimeIndex
) -> pd.Series:
    """Expand ``DatahubPricelist`` records onto a UTC 15-minute grid.

    For each slot in ``index`` the corresponding Danish local wall-clock
    time is computed, the active validity window is selected (latest
    ``ValidFrom`` wins on overlap), and the price is read out:

    - ``ResolutionDuration == "PT1H"``: ``Price{local_hour + 1}`` (local
      hour ``0`` -> ``Price1``, ..., local hour ``23`` -> ``Price24``).
    - Anything else (``"P1D"``, ``"P1M"``, ...): the flat ``Price1``.

    Daylight-saving time is handled naturally by working with local
    wall-clock hours: the duplicated ``02:xx`` hour on the autumn
    fall-back both map to the same local hour (``2``) and therefore the
    same price, and the skipped spring-forward hour never appears in a
    grid built from real UTC instants.

    Args:
        records: Raw ``DatahubPricelist`` records for a single charge.
        index: A UTC, tz-aware 15-minute grid (as produced by
            :func:`energydata.common.timeutils.build_utc_15min_index`).

    Returns:
        A ``float64`` :class:`pandas.Series` indexed by ``index``, with
        ``NaN`` for slots that have no active record or a null price.
    """
    if len(index) == 0:
        # Nothing to expand; return an empty, correctly-typed series.
        return pd.Series(dtype="float64", index=index)

    # Danish local wall-clock time for each UTC slot: `local_hour` drives
    # PT1H price selection, `local_naive` (tzinfo stripped) is compared
    # against the naive ValidFrom/ValidTo bounds in the raw records.
    local_index = index.tz_convert(DK_TZ)
    local_naive = local_index.tz_localize(None)
    local_hour = local_index.hour

    values = np.full(len(index), np.nan, dtype="float64")

    # Process validity windows in ascending ValidFrom order so that, for
    # any grid slot covered by more than one window (an overlap/
    # correction), the later assignment (== latest ValidFrom) wins —
    # mirroring the tie-break rule in select_active_record.
    sorted_records = sorted(records, key=_parse_valid_from)

    for record in sorted_records:
        valid_from = _parse_valid_from(record)
        valid_to = _parse_valid_to(record)

        mask = local_naive >= valid_from
        if valid_to is not None:
            mask &= local_naive < valid_to
        if not mask.any():
            continue  # this window doesn't touch the requested grid at all

        if record.get("ResolutionDuration") == _HOURLY_RESOLUTION:
            # Build the 24 hourly prices once, then gather per-slot via
            # fancy indexing on the local hour (0-23) instead of a
            # per-row Python loop.
            hourly_prices = np.array(
                [_price_or_nan(record, hour_number) for hour_number in range(1, 25)],
                dtype="float64",
            )
            values[mask] = hourly_prices[local_hour[mask]]
        else:
            # Flat daily/monthly resolution: the same price applies to
            # every slot in the window.
            values[mask] = _price_or_nan(record, 1)

    return pd.Series(values, index=index, dtype="float64")


def main() -> None:
    """Demonstrate :func:`select_active_record` and :func:`expand_pricelist_to_15min`."""
    # A tiny synthetic two-window PT1H tariff: cheap at night, expensive by day.
    records: list[dict[str, Any]] = [
        {
            "ValidFrom": "2026-01-01T00:00:00",
            "ValidTo": None,
            "ResolutionDuration": "PT1H",
            **{f"Price{h}": (0.1 if h <= 6 else 0.3) for h in range(1, 25)},
        }
    ]

    # select_active_record: a Copenhagen-local timestamp inside the window.
    at_local = pd.Timestamp("2026-01-15 03:00", tz=DK_TZ)
    active = select_active_record(records, at_local)
    print("Active record at", at_local, "->", active is not None)

    # A timestamp before the window starts has no active record.
    before = pd.Timestamp("2025-01-01 00:00", tz=DK_TZ)
    print("Active record before window starts:", select_active_record(records, before))

    # expand_pricelist_to_15min over one UTC day (Danish winter, UTC+1).
    from energydata.common.timeutils import build_utc_15min_index, to_utc_timestamp

    start_utc = to_utc_timestamp("2026-01-15")
    end_utc = to_utc_timestamp("2026-01-16")
    index = build_utc_15min_index(start_utc, end_utc)
    series = expand_pricelist_to_15min(records, index)
    print("Expanded series head:")
    print(series.head(8))


if __name__ == "__main__":
    main()
