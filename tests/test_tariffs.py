"""Tests for get_dso_tariffs, get_energinet_tariffs and get_elafgift."""

from typing import Any

import pandas as pd
import pytest

from energydata.common.errors import NoDataError, UnknownAreaError
from energydata.tariffs import dso as dso_mod
from energydata.tariffs import elafgift as elafgift_mod
from energydata.tariffs import energinet as energinet_mod
from energydata.tariffs.dso import get_dso_tariffs
from energydata.tariffs.elafgift import get_elafgift
from energydata.tariffs.energinet import get_energinet_tariffs


def _flat(
    valid_from: str,
    valid_to: str | None,
    price: float,
    *,
    charge_type_code: str | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "ValidFrom": valid_from,
        "ValidTo": valid_to,
        "ResolutionDuration": "P1D",
        "Price1": price,
    }
    if charge_type_code is not None:
        record["ChargeTypeCode"] = charge_type_code
    return record


# --- get_dso_tariffs ----------------------------------------------------


def test_dso_column_name_and_96_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    records = [_flat("2020-01-01T00:00:00", None, 0.25)]
    monkeypatch.setattr(dso_mod, "fetch_dataset", lambda *_a, **_k: records)
    df = get_dso_tariffs("2026-01-15", "2026-01-16", "Radius")
    assert list(df.columns) == ["UTC", "Radius"]
    assert len(df) == 96
    assert (df["Radius"] == 0.25).all()


def test_dso_utc_column_is_tz_aware_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    records = [_flat("2020-01-01T00:00:00", None, 0.25)]
    monkeypatch.setattr(dso_mod, "fetch_dataset", lambda *_a, **_k: records)
    df = get_dso_tariffs("2026-01-15", "2026-01-16", "Radius")
    assert isinstance(df["UTC"].dtype, pd.DatetimeTZDtype)
    assert str(df["UTC"].dt.tz) == "UTC"


def test_dso_case_insensitive_dedupe(monkeypatch: pytest.MonkeyPatch) -> None:
    records = [_flat("2020-01-01T00:00:00", None, 0.25)]
    monkeypatch.setattr(dso_mod, "fetch_dataset", lambda *_a, **_k: records)
    df = get_dso_tariffs("2026-01-15", "2026-01-16", ["Radius", "radius"])
    # Differently-cased duplicates collapse to a single column.
    assert list(df.columns) == ["UTC", "Radius"]


def test_dso_zero_records_raises_no_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dso_mod, "fetch_dataset", lambda *_a, **_k: [])
    with pytest.raises(NoDataError):
        get_dso_tariffs("2026-01-15", "2026-01-16", "Radius")


def test_dso_unknown_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dso_mod, "fetch_dataset", lambda *_a, **_k: [])
    with pytest.raises(UnknownAreaError):
        get_dso_tariffs("2026-01-15", "2026-01-16", "NotADso")


def test_dso_does_not_pass_start_end(monkeypatch: pytest.MonkeyPatch) -> None:
    # The DatahubPricelist query must NOT include start/end (would drop the
    # open-ended active record).
    captured: dict[str, Any] = {}

    def _fetch(_dataset: str, **kwargs: Any) -> list[dict[str, Any]]:
        captured.update(kwargs)
        return [_flat("2020-01-01T00:00:00", None, 0.25)]

    monkeypatch.setattr(dso_mod, "fetch_dataset", _fetch)
    get_dso_tariffs("2026-01-15", "2026-01-16", "Radius")
    assert "start" not in captured
    assert "end" not in captured


# --- get_energinet_tariffs ---------------------------------------------


def test_energinet_columns_and_split(monkeypatch: pytest.MonkeyPatch) -> None:
    records = [
        _flat("2020-01-01T00:00:00", None, 0.05, charge_type_code="40000"),
        _flat("2020-01-01T00:00:00", None, 0.07, charge_type_code="41000"),
    ]
    monkeypatch.setattr(energinet_mod, "fetch_dataset", lambda *_a, **_k: records)
    df = get_energinet_tariffs("2026-01-15", "2026-01-16")
    assert list(df.columns) == ["UTC", "Transmission", "System"]
    assert len(df) == 96
    assert (df["Transmission"] == 0.05).all()
    assert (df["System"] == 0.07).all()


def test_energinet_zero_records_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(energinet_mod, "fetch_dataset", lambda *_a, **_k: [])
    with pytest.raises(NoDataError):
        get_energinet_tariffs("2026-01-15", "2026-01-16")


def test_energinet_utc_dtype(monkeypatch: pytest.MonkeyPatch) -> None:
    records = [
        _flat("2020-01-01T00:00:00", None, 0.05, charge_type_code="40000"),
        _flat("2020-01-01T00:00:00", None, 0.07, charge_type_code="41000"),
    ]
    monkeypatch.setattr(energinet_mod, "fetch_dataset", lambda *_a, **_k: records)
    df = get_energinet_tariffs("2026-01-15", "2026-01-16")
    assert isinstance(df["UTC"].dtype, pd.DatetimeTZDtype)
    assert str(df["UTC"].dt.tz) == "UTC"


# --- get_elafgift -------------------------------------------------------


def test_elafgift_column_and_96_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    records = [_flat("2020-01-01T00:00:00", None, 0.008)]
    monkeypatch.setattr(elafgift_mod, "fetch_dataset", lambda *_a, **_k: records)
    df = get_elafgift("2026-01-15", "2026-01-16")
    assert list(df.columns) == ["UTC", "Elafgift"]
    assert len(df) == 96
    assert (df["Elafgift"] == 0.008).all()


def test_elafgift_zero_records_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(elafgift_mod, "fetch_dataset", lambda *_a, **_k: [])
    with pytest.raises(NoDataError):
        get_elafgift("2026-01-15", "2026-01-16")


def test_elafgift_rate_change_lands_on_local_midnight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 2025 rate 0.72 -> 2026 rate 0.008, switching at DK-local midnight
    # 2026-01-01 == 2025-12-31T23:00 UTC (winter, UTC+1).
    records = [
        _flat("2025-01-01T00:00:00", "2026-01-01T00:00:00", 0.72),
        _flat("2026-01-01T00:00:00", None, 0.008),
    ]
    monkeypatch.setattr(elafgift_mod, "fetch_dataset", lambda *_a, **_k: records)
    df = get_elafgift("2025-12-31", "2026-01-02").set_index("UTC")["Elafgift"]

    boundary = pd.Timestamp("2025-12-31T23:00", tz="UTC")
    just_before = pd.Timestamp("2025-12-31T22:45", tz="UTC")
    # Last old-rate slot is 0.72; first new-rate slot (boundary) is 0.008.
    assert df.loc[just_before] == 0.72
    assert df.loc[boundary] == 0.008
    # And it flips exactly at the boundary, not before/after.
    assert (df.loc[df.index < boundary] == 0.72).all()
    assert (df.loc[df.index >= boundary] == 0.008).all()
