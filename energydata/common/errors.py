"""Shared exception hierarchy for the ``energydata`` package.

All errors raised by the public API of this package derive from
:class:`EnergyDataError`, so callers can catch a single base class if they
don't care about the specific failure mode.
"""


class EnergyDataError(Exception):
    """Base class for all errors raised by the ``energydata`` package."""


class NoDataError(EnergyDataError):
    """Raised when an API query returns zero records for the whole request.

    This is distinct from partial coverage (e.g. some rows in the middle of
    the requested period being missing), which is represented as ``NaN``
    values rather than an exception.
    """


class InvalidPeriodError(EnergyDataError):
    """Raised for invalid time periods.

    Covers cases such as ``start >= end`` or timestamps that cannot be
    parsed into a valid point in time.
    """


class UnknownAreaError(EnergyDataError):
    """Raised when a bidding zone / DSO identifier is not recognized.

    The exception message lists the allowed values so callers can quickly
    spot typos.
    """


def main() -> None:
    """Demonstrate raising and catching each exception in this module."""
    # EnergyDataError is the common base class for everything below.
    try:
        raise EnergyDataError("generic energydata failure")
    except EnergyDataError as exc:
        print(f"Caught EnergyDataError: {exc}")

    try:
        raise NoDataError("no records returned for 2026-01-01..2026-01-02")
    except EnergyDataError as exc:
        print(f"Caught NoDataError (as EnergyDataError): {exc}")

    try:
        raise InvalidPeriodError("start (2026-01-02) is not before end (2026-01-01)")
    except EnergyDataError as exc:
        print(f"Caught InvalidPeriodError (as EnergyDataError): {exc}")

    try:
        raise UnknownAreaError("unknown bidding zone 'DK3'; allowed: DE, DK1, DK2, NO2, SE3, SE4")
    except EnergyDataError as exc:
        print(f"Caught UnknownAreaError (as EnergyDataError): {exc}")


if __name__ == "__main__":
    main()
