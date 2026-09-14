"""Registry of well-known identifiers used by the tariffs subpackage.

The ``DatahubPricelist`` dataset on Energi Data Service identifies every
charge (DSO grid tariff, Energinet transmission/system tariff, elafgift,
...) by a combination of ``GLN_Number`` (the owner's Global Location
Number) and ``ChargeTypeCode``. Matching on the human-readable
``ChargeOwner`` string is brittle (spelling, casing, or company-name
changes), so this module centralizes the verified GLN/charge-type-code
pairs and exposes small config objects that the rest of the ``tariffs``
subpackage builds queries from.

All identifiers below were verified against the live
``https://api.energidataservice.dk/dataset/DatahubPricelist`` endpoint.
"""

from dataclasses import dataclass

from energydata.common.errors import UnknownAreaError


@dataclass(frozen=True)
class DsoConfig:
    """Identifiers needed to fetch a single DSO's grid tariff.

    Attributes:
        name: Canonical, human-readable name of the DSO (used as the
            output DataFrame column name).
        gln: The DSO's Global Location Number (``GLN_Number`` field in the
            ``DatahubPricelist`` dataset).
        charge_type_code: The ``ChargeTypeCode`` identifying the specific
            grid tariff product (e.g. ``"DT_C_01"`` for Nettarif C).
    """

    name: str
    gln: str
    charge_type_code: str


# Registry of supported DSOs, keyed by canonical name.
#
# Radius Elnet A/S (GLN 5790000705689), charge type code DT_C_01
# ("Nettarif C"), resolution PT1H, verified live 2026-07-10: the API
# returns records owned by "Radius Elnet A/S" with Note "Nettarif C".
DSO_REGISTRY: dict[str, DsoConfig] = {
    "Radius": DsoConfig("Radius", "5790000705689", "DT_C_01"),
}

# Energinet (the Danish TSO) identifiers, verified live 2026-07-10 against
# ChargeOwner "Energinet Systemansvar A/S (SYO)":
#   - 40000: Note "Transmissions nettarif" (transmission grid tariff), P1D flat.
#   - 41000: Note "Systemtarif" (system tariff), P1D flat.
ENERGINET_GLN = "5790000432752"
ENERGINET_TRANSMISSION_CODE = "40000"
ENERGINET_SYSTEM_CODE = "41000"

# Elafgift (Danish electricity tax), charge type code EA-001, P1D flat.
# Verified live 2026-07-10: EA-001 records are owned by
# "Energinet Systemansvar A/S (SYO)" and share Energinet's GLN
# 5790000432752 (i.e. the same GLN as the transmission/system tariffs
# above) — this was confirmed by querying DatahubPricelist filtered on
# ChargeTypeCode=EA-001 and inspecting the returned GLN_Number.
ELAFGIFT_GLN = "5790000432752"
ELAFGIFT_CHARGE_CODE = "EA-001"
ELAFGIFT_OWNER = "Energinet Systemansvar A/S (SYO)"


def lookup_dso(name: str) -> DsoConfig:
    """Look up a :class:`DsoConfig` in :data:`DSO_REGISTRY` case-insensitively.

    Args:
        name: DSO name to look up, e.g. ``"radius"`` or ``"Radius"``.

    Returns:
        The matching :class:`DsoConfig`, with its canonical ``name``.

    Raises:
        UnknownAreaError: If ``name`` does not match any registered DSO
            (case-insensitively). The error message lists the known keys.
    """
    # Build a case-insensitive lookup table on demand.
    lowered = {key.lower(): config for key, config in DSO_REGISTRY.items()}
    match = lowered.get(name.lower())
    if match is None:
        known = ", ".join(sorted(DSO_REGISTRY))
        raise UnknownAreaError(f"unknown DSO {name!r}; known DSOs: {known}")
    return match


def main() -> None:
    """Demonstrate the registry contents and :func:`lookup_dso`."""
    print("Registered DSOs:", list(DSO_REGISTRY))
    print("Radius config:", DSO_REGISTRY["Radius"])

    # Case-insensitive lookup returns the canonical DsoConfig.
    found = lookup_dso("radius")
    print("Case-insensitive lookup 'radius' ->", found)

    print("Energinet GLN:", ENERGINET_GLN)
    print("Energinet transmission code:", ENERGINET_TRANSMISSION_CODE)
    print("Energinet system code:", ENERGINET_SYSTEM_CODE)
    print("Elafgift GLN:", ELAFGIFT_GLN, "code:", ELAFGIFT_CHARGE_CODE)

    try:
        lookup_dso("Unknown DSO")
    except UnknownAreaError as exc:
        print(f"lookup_dso('Unknown DSO') raised: {exc}")


if __name__ == "__main__":
    main()
