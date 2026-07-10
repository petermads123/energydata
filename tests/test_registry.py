"""Tests for the tariffs identifier registry."""

import dataclasses

import pytest

from energydata.common.errors import UnknownAreaError
from energydata.tariffs.registry import (
    DSO_REGISTRY,
    ELAFGIFT_CHARGE_CODE,
    ELAFGIFT_GLN,
    ENERGINET_GLN,
    ENERGINET_SYSTEM_CODE,
    ENERGINET_TRANSMISSION_CODE,
    DsoConfig,
    lookup_dso,
)


def test_radius_registered_with_verified_identifiers() -> None:
    config = DSO_REGISTRY["Radius"]
    assert config.name == "Radius"
    assert config.gln == "5790000705689"
    assert config.charge_type_code == "DT_C_01"


def test_dso_config_is_frozen() -> None:
    config = DSO_REGISTRY["Radius"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.name = "Other"  # type: ignore[misc]


def test_lookup_dso_case_insensitive() -> None:
    assert lookup_dso("radius") is DSO_REGISTRY["Radius"]
    assert lookup_dso("RADIUS") is DSO_REGISTRY["Radius"]
    assert lookup_dso("Radius") is DSO_REGISTRY["Radius"]


def test_lookup_returns_canonical_name() -> None:
    # Even given lowercase input, the canonical registry name is returned.
    assert lookup_dso("radius").name == "Radius"


def test_lookup_unknown_raises() -> None:
    with pytest.raises(UnknownAreaError):
        lookup_dso("NotARealDso")


def test_energinet_codes() -> None:
    assert ENERGINET_GLN == "5790000432752"
    assert ENERGINET_TRANSMISSION_CODE == "40000"
    assert ENERGINET_SYSTEM_CODE == "41000"


def test_elafgift_codes() -> None:
    # Elafgift shares Energinet's GLN, charge code EA-001.
    assert ELAFGIFT_GLN == "5790000432752"
    assert ELAFGIFT_GLN == ENERGINET_GLN
    assert ELAFGIFT_CHARGE_CODE == "EA-001"
