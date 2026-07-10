"""Tests for the shared exception hierarchy."""

import pytest

from energydata.common.errors import (
    EnergyDataError,
    InvalidPeriodError,
    NoDataError,
    UnknownAreaError,
)


@pytest.mark.parametrize(
    "subclass",
    [NoDataError, InvalidPeriodError, UnknownAreaError],
)
def test_all_errors_derive_from_base(subclass: type[EnergyDataError]) -> None:
    # Every specific error must be catchable via the single base class.
    assert issubclass(subclass, EnergyDataError)


def test_base_error_is_an_exception() -> None:
    assert issubclass(EnergyDataError, Exception)


def test_error_carries_message() -> None:
    exc = NoDataError("nothing here")
    assert str(exc) == "nothing here"


def test_can_catch_specific_via_base() -> None:
    with pytest.raises(EnergyDataError):
        raise UnknownAreaError("bad zone")
