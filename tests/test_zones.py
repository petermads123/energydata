import pytest

from energydata.utils import BIDDING_ZONES, normalize_bidding_zones
from energydata.utils.zones import BiddingZone


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("DK1", ("DK1",)),
        ("DK2", ("DK2",)),
        (["DK2", "DK1"], ("DK2", "DK1")),
        (["DK1", "DK2"], ("DK1", "DK2")),
        (BIDDING_ZONES, ("DK1", "DK2")),
        (("DK1",), ("DK1",)),
    ],
)
def test_normalize_bidding_zones_keeps_given_order_and_wraps_a_string(
    given: BiddingZone | list[BiddingZone] | tuple[BiddingZone, ...],
    expected: tuple[str, ...],
) -> None:
    assert normalize_bidding_zones(given) == expected


def test_bidding_zones_default_order_is_dk1_then_dk2() -> None:
    assert BIDDING_ZONES == ("DK1", "DK2")


@pytest.mark.parametrize(
    "bad",
    [
        "dk1",
        " DK1",
        "DK1 ",
        "DK3",
        "",
        "DK1DK2",
        ["DK1", "SE3"],
        ["dk1"],
        ["DK1", None],
    ],
)
def test_normalize_bidding_zones_rejects_unknown_naming_value_and_valid_set(
    bad: object,
) -> None:
    offender = bad[-1] if isinstance(bad, list) else bad

    with pytest.raises(ValueError) as caught:
        normalize_bidding_zones(bad)  # type: ignore[arg-type]  # invalid on purpose

    message = str(caught.value)
    assert repr(offender) in message
    assert "['DK1', 'DK2']" in message


@pytest.mark.parametrize("empty", [[], ()])
def test_normalize_bidding_zones_rejects_an_empty_sequence(empty: list[str]) -> None:
    with pytest.raises(ValueError, match="empty"):
        normalize_bidding_zones(empty)  # type: ignore[arg-type]  # invalid on purpose


def test_normalize_bidding_zones_rejects_a_duplicate_naming_it() -> None:
    with pytest.raises(ValueError, match=r"duplicate.*'DK1'"):
        normalize_bidding_zones(["DK1", "DK2", "DK1"])


def test_normalize_bidding_zones_does_not_mutate_its_input() -> None:
    zones: list[BiddingZone] = ["DK2", "DK1"]

    result = normalize_bidding_zones(zones)

    assert zones == ["DK2", "DK1"]
    assert isinstance(result, tuple)


def test_normalize_bidding_zones_is_idempotent() -> None:
    first = normalize_bidding_zones(["DK2", "DK1"])

    assert normalize_bidding_zones(first) == first


def test_normalize_bidding_zones_rejects_none_with_a_type_error() -> None:
    with pytest.raises(TypeError):
        normalize_bidding_zones(None)  # type: ignore[arg-type]  # invalid on purpose
