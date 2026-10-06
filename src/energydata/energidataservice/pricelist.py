"""Grid tariffs, subscriptions and the electricity tax, from *DatahubPricelist*."""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta

import numpy as np
import pandas as pd

from energydata.utils.frames import period_index
from energydata.utils.periods import DANISH_TZ, TimeLike, resolve_period

from .client import EnergiDataServiceClient, EnergiDataServiceError, Record
from .dsos import DSOS, Dso

DATASET = "DatahubPricelist"
ENERGINET_GLN = "5790000432752"
TARIFF = "D03"
SUBSCRIPTION = "D01"

_ORIGIN = pd.Timestamp("2014-01-01", tz=DANISH_TZ)
_HOUR = timedelta(hours=1)
_PRICES = [f"Price{n}" for n in range(1, 25)]
_COLUMNS = [
    "ChargeTypeCode",
    "ValidFrom",
    "ValidTo",
    "ResolutionDuration",
    *_PRICES,
]
_HOURLY_RESOLUTIONS = ("PT1H", "P1D")
_MONTHLY_RESOLUTIONS = ("P1M",)


@dataclass(frozen=True)
class _Row:
    """One price-list row, parsed.

    Attributes:
        code: The `ChargeTypeCode`.
        rank: The code's position in the requested list; lower wins a tie.
        valid_from: Local midnight the row starts, inclusive.
        valid_to: Local midnight the row ends, exclusive; `None` is open-ended.
        resolution: `ResolutionDuration`: `PT1H`, `P1D` or `P1M`.
        prices: 24 hourly prices for `PT1H`, the single price otherwise; NaN
            where the record has none.
    """

    code: str
    rank: int
    valid_from: pd.Timestamp
    valid_to: pd.Timestamp | None
    resolution: str
    prices: tuple[float, ...]

    def covers(self, moment: pd.Timestamp) -> bool:
        """Whether the row is valid at `moment`."""
        return self.valid_from <= moment and (
            self.valid_to is None or moment < self.valid_to
        )


def get_dso_tariffs(
    dso: str,
    start: TimeLike,
    end: TimeLike | None = None,
    *,
    client: EnergiDataServiceClient | None = None,
) -> pd.DataFrame:
    """Get a DSO's standard C-customer consumption tariff, one row per hour.

    | Property | Value |
    |---|---|
    | Currency and unit | DKK/kWh, excl. VAT |
    | Resolution | 1 hour |
    | Format | long: `start`, `end` and a float `tariff` column |
    | Source dataset | *DatahubPricelist*, `ChargeType` `D03`, the DSO's GLN and C tariff codes (see `DSOS`) |
    | Supported DSOs | the keys of `DSOS`, matched case-insensitively |

    `start` and `end` are tz-aware `Europe/Copenhagen`, one half-open row per
    hour of `[start, end)` (23 or 25 rows on a DST day). Each hour takes the
    price-list row valid on its local date: a `PT1H` row contributes
    `Price{n}` to local hour n-1 (the repeated autumn hour is priced twice, as
    its wall-clock hour) and a `P1D` row contributes its price to every hour.
    Where rows overlap the latest `ValidFrom` wins. An hour with no valid row
    is NaN. The price list is read from its first date (2014-01-01) in one
    request, because a row that began before the period is still valid in it.

    Args:
        dso: A friendly DSO name from `DSOS`, such as `"radius"`.
        start: First moment of the period. A date means local midnight; a
            naive value is read as Danish local time.
        end: First moment after the period (exclusive). `None` means the whole
            local day when `start` is a date, or one hour when it is a
            timestamp.
        client: A client to fetch with. `None` creates one and closes it before
            returning; a passed client is left open.

    Returns:
        A frame with columns `start`, `end` and `tariff`, on a `RangeIndex`.

    Raises:
        ValueError: If `dso` is not a key of `DSOS`, `start >= end`, a
            timestamp is not on an hour boundary, a time is nonexistent or
            ambiguous, or a string does not parse.
        EnergiDataServiceError: If the service returns an unexpected payload
            or a row with an unknown `ResolutionDuration`.
        httpx.HTTPStatusError: If the service refuses a request.
        RuntimeError: If a passed `client` is closed.
    """
    known = _dso(dso)
    return _hourly_frame(
        start,
        end,
        client,
        known.gln,
        TARIFF,
        {"tariff": known.tariff_codes},
    )


def get_energinet_tariffs(
    start: TimeLike,
    end: TimeLike | None = None,
    *,
    client: EnergiDataServiceClient | None = None,
) -> pd.DataFrame:
    """Get Energinet's system and transmission tariffs, one row per hour.

    | Property | Value |
    |---|---|
    | Currency and unit | DKK/kWh, excl. VAT |
    | Resolution | 1 hour |
    | Format | long: `start`, `end`, `system_tariff` and `transmission_tariff` |
    | Source dataset | *DatahubPricelist*, `ChargeType` `D03`, GLN `5790000432752`, codes `41000` (Systemtarif) and `40000` (Transmissions nettarif) |

    The hourly shape and the row-picking rules are those of `get_dso_tariffs`.
    Both codes come from one request.

    Args:
        start: First moment of the period. A date means local midnight; a
            naive value is read as Danish local time.
        end: First moment after the period (exclusive). `None` means the whole
            local day when `start` is a date, or one hour when it is a
            timestamp.
        client: A client to fetch with. `None` creates one and closes it before
            returning; a passed client is left open.

    Returns:
        A frame with columns `start`, `end`, `system_tariff` and
        `transmission_tariff`, on a `RangeIndex`.

    Raises:
        ValueError: If `start >= end`, a timestamp is not on an hour boundary,
            a time is nonexistent or ambiguous, or a string does not parse.
        EnergiDataServiceError: If the service returns an unexpected payload
            or a row with an unknown `ResolutionDuration`.
        httpx.HTTPStatusError: If the service refuses a request.
        RuntimeError: If a passed `client` is closed.
    """
    return _hourly_frame(
        start,
        end,
        client,
        ENERGINET_GLN,
        TARIFF,
        {"system_tariff": ("41000",), "transmission_tariff": ("40000",)},
    )


def get_dso_subscriptions(
    dso: str,
    start: TimeLike,
    end: TimeLike | None = None,
    *,
    client: EnergiDataServiceClient | None = None,
) -> pd.DataFrame:
    """Get a DSO's standard C-customer consumption subscription, per validity period.

    | Property | Value |
    |---|---|
    | Currency and unit | DKK/month, excl. VAT |
    | Resolution | one row per validity period |
    | Format | long: `start`, `end` and a float `subscription` column |
    | Source dataset | *DatahubPricelist*, `ChargeType` `D01`, the DSO's GLN and C subscription codes (see `DSOS`) |
    | Supported DSOs | the keys of `DSOS`, matched case-insensitively |

    One row per stretch of `[start, end)` over which the same price-list row
    is valid, `start` and `end` clipped to the request. A stretch with no row
    is a row with NaN. A DSO that publishes no C subscription (`sunds`) gives
    one NaN row for the whole period and no request is made.

    Args:
        dso: A friendly DSO name from `DSOS`, such as `"radius"`.
        start: First moment of the period. A date means local midnight; a
            naive value is read as Danish local time.
        end: First moment after the period (exclusive). `None` means the whole
            local day when `start` is a date, or one hour when it is a
            timestamp.
        client: A client to fetch with. `None` creates one and closes it before
            returning; a passed client is left open.

    Returns:
        A frame with columns `start`, `end` and `subscription`, on a
        `RangeIndex`.

    Raises:
        ValueError: If `dso` is not a key of `DSOS`, `start >= end`, a
            timestamp is not on an hour boundary, a time is nonexistent or
            ambiguous, or a string does not parse.
        EnergiDataServiceError: If the service returns an unexpected payload
            or a row with an unknown `ResolutionDuration`.
        httpx.HTTPStatusError: If the service refuses a request.
        RuntimeError: If a passed `client` is closed.
    """
    known = _dso(dso)
    return _period_frame(start, end, client, known.gln, known.subscription_codes)


def get_energinet_subscriptions(
    start: TimeLike,
    end: TimeLike | None = None,
    *,
    client: EnergiDataServiceClient | None = None,
) -> pd.DataFrame:
    """Get Energinet's TSO system subscription, per validity period.

    | Property | Value |
    |---|---|
    | Currency and unit | DKK/month, excl. VAT |
    | Resolution | one row per validity period |
    | Format | long: `start`, `end` and a float `subscription` column |
    | Source dataset | *DatahubPricelist*, `ChargeType` `D01`, GLN `5790000432752`, code `41004` |

    The shape and the row-picking rules are those of `get_dso_subscriptions`.

    Args:
        start: First moment of the period. A date means local midnight; a
            naive value is read as Danish local time.
        end: First moment after the period (exclusive). `None` means the whole
            local day when `start` is a date, or one hour when it is a
            timestamp.
        client: A client to fetch with. `None` creates one and closes it before
            returning; a passed client is left open.

    Returns:
        A frame with columns `start`, `end` and `subscription`, on a
        `RangeIndex`.

    Raises:
        ValueError: If `start >= end`, a timestamp is not on an hour boundary,
            a time is nonexistent or ambiguous, or a string does not parse.
        EnergiDataServiceError: If the service returns an unexpected payload
            or a row with an unknown `ResolutionDuration`.
        httpx.HTTPStatusError: If the service refuses a request.
        RuntimeError: If a passed `client` is closed.
    """
    return _period_frame(start, end, client, ENERGINET_GLN, ("41004",))


def get_electricity_tax(
    start: TimeLike,
    end: TimeLike | None = None,
    *,
    client: EnergiDataServiceClient | None = None,
) -> pd.DataFrame:
    """Get the Danish electricity tax (elafgift), one row per hour.

    | Property | Value |
    |---|---|
    | Currency and unit | DKK/kWh, excl. VAT |
    | Resolution | 1 hour |
    | Format | long: `start`, `end` and a float `electricity_tax` column |
    | Source dataset | *DatahubPricelist*, `ChargeType` `D03`, GLN `5790000432752`, code `EA-001` (the normal rate) |

    The hourly shape and the row-picking rules are those of `get_dso_tariffs`.
    The reduced rate for electric heating is not published in
    *DatahubPricelist*, so it is not available here.

    Args:
        start: First moment of the period. A date means local midnight; a
            naive value is read as Danish local time.
        end: First moment after the period (exclusive). `None` means the whole
            local day when `start` is a date, or one hour when it is a
            timestamp.
        client: A client to fetch with. `None` creates one and closes it before
            returning; a passed client is left open.

    Returns:
        A frame with columns `start`, `end` and `electricity_tax`, on a
        `RangeIndex`.

    Raises:
        ValueError: If `start >= end`, a timestamp is not on an hour boundary,
            a time is nonexistent or ambiguous, or a string does not parse.
        EnergiDataServiceError: If the service returns an unexpected payload
            or a row with an unknown `ResolutionDuration`.
        httpx.HTTPStatusError: If the service refuses a request.
        RuntimeError: If a passed `client` is closed.
    """
    return _hourly_frame(
        start,
        end,
        client,
        ENERGINET_GLN,
        TARIFF,
        {"electricity_tax": ("EA-001",)},
    )


def _dso(name: str) -> Dso:
    """Look a DSO up by friendly name, case-insensitively."""
    found = DSOS.get(name.lower())
    if found is None:
        known = ", ".join(sorted(DSOS))
        raise ValueError(f"unknown DSO {name!r}; known DSOs: {known}")
    return found


def _hourly_frame(
    start: TimeLike,
    end: TimeLike | None,
    client: EnergiDataServiceClient | None,
    gln: str,
    charge_type: str,
    columns: Mapping[str, Sequence[str]],
) -> pd.DataFrame:
    """Fetch the codes and shape them into one value column each, per hour."""
    first, last = resolve_period(start, end, resolution=_HOUR)
    index = period_index(first, last, _HOUR)
    codes = list(dict.fromkeys(code for group in columns.values() for code in group))
    rows = _load(client, gln, codes, charge_type, last, _HOURLY_RESOLUTIONS)
    values = {
        name: _hourly([row for row in rows if row.code in group], index)
        for name, group in columns.items()
    }
    frame = pd.DataFrame(
        {"start": index, "end": index + pd.Timedelta(_HOUR), **values},
        index=pd.RangeIndex(len(index)),
    )
    return frame


def _period_frame(
    start: TimeLike,
    end: TimeLike | None,
    client: EnergiDataServiceClient | None,
    gln: str,
    codes: Sequence[str],
) -> pd.DataFrame:
    """Fetch the subscription codes and shape them into one row per period."""
    first, last = resolve_period(start, end, resolution=_HOUR)
    rows = (
        _load(client, gln, codes, SUBSCRIPTION, last, _MONTHLY_RESOLUTIONS)
        if codes
        else []
    )
    pieces = _periods(rows, first, last)
    starts = pd.DatetimeIndex([piece[0] for piece in pieces]).as_unit("ns")
    ends = pd.DatetimeIndex([piece[1] for piece in pieces]).as_unit("ns")
    return pd.DataFrame(
        {
            "start": starts,
            "end": ends,
            "subscription": np.array([piece[2] for piece in pieces], dtype=float),
        },
        index=pd.RangeIndex(len(pieces)),
    )


def _load(
    client: EnergiDataServiceClient | None,
    gln: str,
    codes: Sequence[str],
    charge_type: str,
    last: pd.Timestamp,
    resolutions: Sequence[str],
) -> list[_Row]:
    """Fetch the rows of `codes` that can matter for a period ending at `last`.

    Makes one request from `_ORIGIN` and none when `last` is not after it. The
    API cuts `start` and `end` to their dates, `end` exclusive, and the client
    sends UTC; the end is therefore local midnight two days after the period's
    last local date, so a row starting on that date is never cut off.

    Args:
        client: A client to fetch with; `None` creates one and closes it.
        gln: The charge owner's GLN.
        codes: The `ChargeTypeCode`s, in order of precedence.
        charge_type: `D03` (tariff) or `D01` (subscription).
        last: End of the period, exclusive.
        resolutions: The `ResolutionDuration`s the caller can read.

    Returns:
        The parsed rows, in the order the service returned them.

    Raises:
        EnergiDataServiceError: If a row has another `ResolutionDuration`.
    """
    if last <= _ORIGIN:
        return []
    last_date = (last - pd.Timedelta(nanoseconds=1)).tz_localize(None).normalize()
    fetch_end = (last_date + pd.Timedelta(days=2)).tz_localize(DANISH_TZ)
    begin = _ORIGIN.to_pydatetime()
    stop = fetch_end.to_pydatetime()
    filters = {
        "GLN_Number": [gln],
        "ChargeType": [charge_type],
        "ChargeTypeCode": list(codes),
    }
    owned = client is None
    active = EnergiDataServiceClient() if client is None else client
    try:
        records = active.get_dataset(
            DATASET,
            begin,
            stop,
            filters=filters,
            columns=_COLUMNS,
            sort_by="ValidFrom",
            max_span=stop - begin,
        )
    finally:
        if owned:
            active.close()
    return _rows(records, codes, resolutions)


def _rows(
    records: Sequence[Record], codes: Sequence[str], resolutions: Sequence[str]
) -> list[_Row]:
    """Parse records into rows, refusing a resolution the caller cannot read."""
    rows: list[_Row] = []
    for record in records:
        code = record.get("ChargeTypeCode")
        if not isinstance(code, str) or code not in codes:
            raise EnergiDataServiceError(
                f"{DATASET}: unexpected ChargeTypeCode {code!r}"
            )
        resolution = record.get("ResolutionDuration")
        if resolution not in resolutions:
            raise EnergiDataServiceError(
                f"{DATASET}: code {code!r} has unsupported ResolutionDuration "
                f"{resolution!r}, expected one of {', '.join(resolutions)}"
            )
        assert isinstance(resolution, str)  # narrowed by the membership test
        valid_from = _local(record.get("ValidFrom"), code, "ValidFrom")
        if valid_from is None:
            raise EnergiDataServiceError(f"{DATASET}: code {code!r} has no ValidFrom")
        count = 24 if resolution == "PT1H" else 1
        prices = tuple(_price(record.get(name), code, name) for name in _PRICES[:count])
        rows.append(
            _Row(
                code=code,
                rank=list(codes).index(code),
                valid_from=valid_from,
                valid_to=_local(record.get("ValidTo"), code, "ValidTo"),
                resolution=resolution,
                prices=prices,
            )
        )
    return rows


def _local(value: object, code: str, name: str) -> pd.Timestamp | None:
    """Parse a validity date as local Danish midnight; `None` stays `None`."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise EnergiDataServiceError(
            f"{DATASET}: code {code!r} has a non-text {name} {value!r}"
        )
    try:
        stamp = pd.Timestamp(value)
    except ValueError as error:
        raise EnergiDataServiceError(
            f"{DATASET}: code {code!r} has an unparseable {name} {value!r}"
        ) from error
    if stamp.tzinfo is None:
        return stamp.normalize().tz_localize(DANISH_TZ)
    return stamp.tz_convert(DANISH_TZ)


def _price(value: object, code: str, name: str) -> float:
    """Read one price field as a float, NaN when the record has none."""
    if value is None:
        return math.nan
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise EnergiDataServiceError(
            f"{DATASET}: code {code!r} has a non-numeric {name} {value!r}"
        )
    return float(value)


def _precedence(rows: Sequence[_Row]) -> list[_Row]:
    """Order rows so that applying them in turn leaves the winner last.

    The latest `ValidFrom` wins; on an equal one the earlier code wins.
    """
    return sorted(rows, key=lambda row: (row.valid_from, -row.rank))


def _hourly(rows: Sequence[_Row], index: pd.DatetimeIndex) -> np.ndarray:
    """The price for every slot of `index` from the row valid then, NaN if none."""
    values = np.full(len(index), np.nan)
    hours = np.asarray(index.hour)
    for row in _precedence(rows):
        mask = np.asarray(index >= row.valid_from)
        if row.valid_to is not None:
            mask &= np.asarray(index < row.valid_to)
        if row.resolution == "PT1H":
            values[mask] = np.asarray(row.prices)[hours[mask]]
        else:
            values[mask] = row.prices[0]
    return values


def _periods(
    rows: Sequence[_Row], first: pd.Timestamp, last: pd.Timestamp
) -> list[tuple[pd.Timestamp, pd.Timestamp, float]]:
    """Cut `[first, last)` at every row boundary into stretches of one active row.

    Adjacent stretches with the same active row (or none) are merged. Each
    comes with its price, NaN where no row is active.
    """
    cuts = {first, last}
    for row in rows:
        for edge in (row.valid_from, row.valid_to):
            if edge is not None and first < edge < last:
                cuts.add(edge)
    ordered = sorted(cuts)
    ranked = _precedence(rows)
    pieces: list[tuple[pd.Timestamp, pd.Timestamp, _Row | None]] = []
    for lo, hi in zip(ordered, ordered[1:], strict=False):
        active = next((row for row in reversed(ranked) if row.covers(lo)), None)
        if pieces and pieces[-1][2] is active:
            pieces[-1] = (pieces[-1][0], hi, active)
        else:
            pieces.append((lo, hi, active))
    return [
        (lo, hi, math.nan if active is None else active.prices[0])
        for lo, hi, active in pieces
    ]


def main() -> None:
    """Showcase this module's functionality (calls the live API)."""
    dso = "radius"  # any key of DSOS, for example "radius", "cerius", "n1-131"
    start: TimeLike = "2026-10-15"  # a date, a datetime or an ISO 8601 string
    end: TimeLike | None = None  # exclusive; None means the whole day for a date

    tariffs = get_dso_tariffs(dso, start, end)

    print(f"{dso} tariff, DKK/kWh, {len(tariffs)} hours")
    print(tariffs.iloc[16:20])

    # Energinet's tariffs for three hours: one row per hour, constant per day.
    start = "2026-01-01T00:00"
    end = "2026-01-01T03:00"

    energinet = get_energinet_tariffs(start, end)

    print(energinet)

    # The monthly subscription, split where the price changed.
    start = "2025-12-01"
    end = "2026-02-01"

    subscriptions = get_dso_subscriptions(dso, start, end)

    print(subscriptions)

    # The tax (normal rate) across the 2025-2026 cut.
    start = "2025-12-31T22:00"
    end = "2026-01-01T02:00"

    tax = get_electricity_tax(start, end)

    print(tax)


if __name__ == "__main__":
    main()
