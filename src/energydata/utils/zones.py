"""Bidding zones: the supported set and normalising what a caller passes."""

from collections.abc import Sequence
from typing import Literal

type BiddingZone = Literal["DK1", "DK2"]

BIDDING_ZONES: tuple[BiddingZone, ...] = ("DK1", "DK2")


def normalize_bidding_zones(
    bidding_zones: BiddingZone | Sequence[BiddingZone],
) -> tuple[BiddingZone, ...]:
    """Turn one zone or a sequence of zones into a validated tuple.

    Args:
        bidding_zones: A single zone name, or a sequence of them. The given
            order is kept.

    Returns:
        The zones as a tuple, in the order given.

    Raises:
        ValueError: If the sequence is empty, a zone is unknown (names are
            case-sensitive) or a zone is repeated.
    """
    names: Sequence[str] = (
        (bidding_zones,) if isinstance(bidding_zones, str) else bidding_zones
    )
    if len(names) == 0:
        raise ValueError(
            f"bidding_zones must not be empty, expected from {list(BIDDING_ZONES)}"
        )
    seen: list[BiddingZone] = []
    for name in names:
        zone = next((known for known in BIDDING_ZONES if known == name), None)
        if zone is None:
            raise ValueError(
                f"unknown bidding zone {name!r}, expected one of {list(BIDDING_ZONES)}"
            )
        if zone in seen:
            raise ValueError(
                f"duplicate bidding zone {name!r} in {list(names)}, "
                f"expected each of {list(BIDDING_ZONES)} at most once"
            )
        seen.append(zone)
    return tuple(seen)


def main() -> None:
    """Showcase this module's functionality."""
    single: BiddingZone = "DK1"  # "DK1", "DK2"
    several: list[BiddingZone] = ["DK2", "DK1"]  # any order, kept as given

    from_single = normalize_bidding_zones(single)
    from_several = normalize_bidding_zones(several)

    print(f"{single!r} -> {from_single}")
    print(f"{several} -> {from_several}")
    print(f"supported zones: {BIDDING_ZONES}")

    # An unknown zone is refused, naming the value.
    unknown: list[str] = ["dk1"]  # names are case-sensitive

    try:
        normalize_bidding_zones(unknown)  # type: ignore[arg-type]  # invalid on purpose
    except ValueError as error:
        print(f"error: {error}")


if __name__ == "__main__":
    main()
