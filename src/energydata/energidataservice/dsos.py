"""The Danish distribution system operators (DSOs) the price-list functions know by name."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class Dso:
    """One DSO and where its standard C-customer prices sit in *DatahubPricelist*.

    Attributes:
        name: The friendly name, lowercase, with the grid-area number where one
            company runs several areas (`n1-131`).
        owner: The `ChargeOwner` as the price list spells it.
        gln: The DSO's `GLN_Number`.
        tariff_codes: `ChargeTypeCode`s of the standard C consumption tariff
            (`ChargeType` `D03`), in order of precedence.
        subscription_codes: `ChargeTypeCode`s of the standard C consumption
            subscription (`ChargeType` `D01`), in order of precedence; empty
            when the DSO publishes none.
    """

    name: str
    owner: str
    gln: str
    tariff_codes: tuple[str, ...]
    subscription_codes: tuple[str, ...]


_TABLE: tuple[Dso, ...] = (
    Dso("aal", "Aal El-Net A M B A", "5790001095451", ("AAL-NT-05",), ("AAL-E-50",)),
    Dso("cerius", "Cerius A/S", "5790000705184", ("30TR_C_ET",), ("30AB_CT",)),
    Dso("dinel", "Dinel A/S", "5790000610099", ("TCL<100_02",), ("ACL<100_01",)),
    Dso("elektrus", "Elektrus A/S", "5790000836239", ("6000091",), ("6000082",)),
    Dso("elinord", "Elinord A/S", "5790001095277", ("43300",), ("41300",)),
    Dso("elnet-midt", "Elnet Midt A/S", "5790001100520", ("T3001",), ("20001",)),
    Dso("elvaerk", "Netselskabet Elværk A/S", "5790000681358", ("5NCFF",), ("5ACFF",)),
    Dso("flow", "FLOW Elnet A/S", "5790000392551", ("FE1 NT-01",), ("FE1 E-50",)),
    Dso(
        "forsyning-elnet",
        "Forsyning Elnet A/S",
        "5790001088309",
        ("STR-NT-03",),
        ("STR-E-50",),
    ),
    Dso(
        "grindsted",
        "Grindsted Elnet A/S",
        "5790000681105",
        ("GEV-NT-01",),
        ("GEV-E-50",),
    ),
    Dso(
        "hammel",
        "Hammel Elforsyning Net A/S",
        "5790001090166",
        ("C-Tarif",),
        ("55000",),
    ),
    Dso(
        "hjerting",
        "Hjerting Transformatorforening",
        "5790001095376",
        ("C-Tarif",),
        ("HE-E-50",),
    ),
    Dso(
        "hurup", "Hurup Elværk Net A/S", "5790000610839", ("HEV-NT-01T",), ("HEV-E-50",)
    ),
    Dso("ikast", "Ikast El Net A/S", "5790000682102", ("IEV-NT-01",), ("IEV-E-50",)),
    Dso("kimbrer", "Kimbrer Elnet A/S", "5790001095239", ("C-Tarif",), ("AARS-E-50",)),
    Dso(
        "konstant-151",
        "Konstant Net A/S - 151",
        "5790000704842",
        ("151-NT01T", "C_FBTNTR_B"),
        ("151-E5004", "C_FBAHM__B"),
    ),
    Dso(
        "konstant-245",
        "Konstant Net A/S - 245",
        "5790000683345",
        ("245-NT01T", "C_FBTNTR_B"),
        ("245-E5004", "C_FBAHM__B"),
    ),
    Dso("l-net", "L-Net A/S", "5790001090111", ("3000",), ("4100",)),
    Dso("laesoe", "Læsø Elnet A/S", "5790001103460", ("43100",), ("41100",)),
    Dso(
        "midtfyns",
        "Midtfyns Elforsyning A.m.b.A",
        "5790001089023",
        ("TNT15000",),
        ("AB15000",),
    ),
    Dso("n1-016", "N1 A/S - 016", "5790002502699", ("C-Tarif",), ("K_22000",)),
    Dso("n1-131", "N1 A/S - 131", "5790001089030", ("CD",), ("CD",)),
    Dso("n1-344", "N1 A/S - 344", "5790000611003", ("T-C-F-F-TD",), ("A-C-F-04",)),
    Dso("noe", "NOE Net A/S", "5790000395620", ("30030",), ("32310",)),
    Dso("nord-energi", "Nord Energi Net A/S", "5790000610877", ("TAC",), ("ABC",)),
    Dso("radius", "Radius Elnet A/S", "5790000705689", ("DT_C_01",), ("DA_C_F_01",)),
    Dso("rah", "RAH Net A/S", "5790000681327", ("RAH-C",), ("ABON-COPG",)),
    Dso("ravdex", "Ravdex A/S", "5790000836727", ("NT-C",), ("E-50C1",)),
    Dso("sunds", "Sunds Net A.m.b.a", "5790001095444", ("SEF-NT-05",), ()),
    Dso("tarm", "Tarm Elværk Net A/S", "5790000706419", ("TEV-NT-01T",), ("TEV-E-50",)),
    Dso("trefor", "TREFOR El-net A/S", "5790000392261", ("C",), ("E-51",)),
    Dso("trefor-oest", "TREFOR El-net Øst A/S", "5790000706686", ("46",), ("E-50",)),
    Dso("veksel", "Veksel A/S", "5790001088217", ("NT-01",), ("E-50",)),
    Dso("vores-elnet", "Vores Elnet A/S", "5790000610976", ("TNT1009",), ("AB1012",)),
    Dso("zeanet", "Zeanet A/S", "5790001089375", ("43110",), ("41100",)),
)

DSOS: Mapping[str, Dso] = MappingProxyType(
    {dso.name: dso for dso in sorted(_TABLE, key=lambda dso: dso.name)}
)
"""Every supported DSO by friendly name, sorted, read-only."""


def main() -> None:
    """Showcase this module's functionality (offline)."""
    name = "radius"  # any key of DSOS, for example "radius", "cerius", "n1-131"

    dso = DSOS[name]

    print(f"{len(DSOS)} DSOs; {name}: {dso}")
    for key, known in DSOS.items():
        print(f"  {key:<16} {known.gln}  {known.owner}")


if __name__ == "__main__":
    main()
