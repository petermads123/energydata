import dataclasses
import re
from pathlib import Path

import pytest
from conftest import MarketRecords

from energydata.energidataservice import dsos
from energydata.energidataservice.dsos import DSOS, Dso, main

README = Path(__file__).parent.parent / "README.md"
ENERGINET_GLN = "5790000432752"
# GLNs that publish a C tariff in the price list but are not DSOs of ours:
# the TSO (own functions), the price list's test owner, and two DSOs since merged.
NOT_DSOS = {
    ENERGINET_GLN,
    "5799994000107",
    "5790000610822",
    "5790001088231",
}
TODAY = "2026-10-06"
C_TARIFF_NOTE = re.compile(r"Nettarif C|C-Kunde", re.IGNORECASE)
NOT_STANDARD_NOTE = re.compile(
    r"rabat|produktion|indf[oø]dning|r[aå]dighed|regional|discount", re.IGNORECASE
)

# The 35 rows the user checked at the plan gate, written out independently of dsos.py.
PLAN_TABLE: list[tuple[str, str, str, tuple[str, ...], tuple[str, ...]]] = [
    ("aal", "Aal El-Net A M B A", "5790001095451", ("AAL-NT-05",), ("AAL-E-50",)),
    ("cerius", "Cerius A/S", "5790000705184", ("30TR_C_ET",), ("30AB_CT",)),
    ("dinel", "Dinel A/S", "5790000610099", ("TCL<100_02",), ("ACL<100_01",)),
    ("elektrus", "Elektrus A/S", "5790000836239", ("6000091",), ("6000082",)),
    ("elinord", "Elinord A/S", "5790001095277", ("43300",), ("41300",)),
    ("elnet-midt", "Elnet Midt A/S", "5790001100520", ("T3001",), ("20001",)),
    ("elvaerk", "Netselskabet Elværk A/S", "5790000681358", ("5NCFF",), ("5ACFF",)),
    ("flow", "FLOW Elnet A/S", "5790000392551", ("FE1 NT-01",), ("FE1 E-50",)),
    (
        "forsyning-elnet",
        "Forsyning Elnet A/S",
        "5790001088309",
        ("STR-NT-03",),
        ("STR-E-50",),
    ),
    (
        "grindsted",
        "Grindsted Elnet A/S",
        "5790000681105",
        ("GEV-NT-01",),
        ("GEV-E-50",),
    ),
    ("hammel", "Hammel Elforsyning Net A/S", "5790001090166", ("C-Tarif",), ("55000",)),
    (
        "hjerting",
        "Hjerting Transformatorforening",
        "5790001095376",
        ("C-Tarif",),
        ("HE-E-50",),
    ),
    ("hurup", "Hurup Elværk Net A/S", "5790000610839", ("HEV-NT-01T",), ("HEV-E-50",)),
    ("ikast", "Ikast El Net A/S", "5790000682102", ("IEV-NT-01",), ("IEV-E-50",)),
    ("kimbrer", "Kimbrer Elnet A/S", "5790001095239", ("C-Tarif",), ("AARS-E-50",)),
    (
        "konstant-151",
        "Konstant Net A/S - 151",
        "5790000704842",
        ("151-NT01T", "C_FBTNTR_B"),
        ("151-E5004", "C_FBAHM__B"),
    ),
    (
        "konstant-245",
        "Konstant Net A/S - 245",
        "5790000683345",
        ("245-NT01T", "C_FBTNTR_B"),
        ("245-E5004", "C_FBAHM__B"),
    ),
    ("l-net", "L-Net A/S", "5790001090111", ("3000",), ("4100",)),
    ("laesoe", "Læsø Elnet A/S", "5790001103460", ("43100",), ("41100",)),
    (
        "midtfyns",
        "Midtfyns Elforsyning A.m.b.A",
        "5790001089023",
        ("TNT15000",),
        ("AB15000",),
    ),
    ("n1-016", "N1 A/S - 016", "5790002502699", ("C-Tarif",), ("K_22000",)),
    ("n1-131", "N1 A/S - 131", "5790001089030", ("CD",), ("CD",)),
    ("n1-344", "N1 A/S - 344", "5790000611003", ("T-C-F-F-TD",), ("A-C-F-04",)),
    ("noe", "NOE Net A/S", "5790000395620", ("30030",), ("32310",)),
    ("nord-energi", "Nord Energi Net A/S", "5790000610877", ("TAC",), ("ABC",)),
    ("radius", "Radius Elnet A/S", "5790000705689", ("DT_C_01",), ("DA_C_F_01",)),
    ("rah", "RAH Net A/S", "5790000681327", ("RAH-C",), ("ABON-COPG",)),
    ("ravdex", "Ravdex A/S", "5790000836727", ("NT-C",), ("E-50C1",)),
    ("sunds", "Sunds Net A.m.b.a", "5790001095444", ("SEF-NT-05",), ()),
    ("tarm", "Tarm Elværk Net A/S", "5790000706419", ("TEV-NT-01T",), ("TEV-E-50",)),
    ("trefor", "TREFOR El-net A/S", "5790000392261", ("C",), ("E-51",)),
    ("trefor-oest", "TREFOR El-net Øst A/S", "5790000706686", ("46",), ("E-50",)),
    ("veksel", "Veksel A/S", "5790001088217", ("NT-01",), ("E-50",)),
    ("vores-elnet", "Vores Elnet A/S", "5790000610976", ("TNT1009",), ("AB1012",)),
    ("zeanet", "Zeanet A/S", "5790001089375", ("43110",), ("41100",)),
]


@pytest.mark.parametrize(
    ("name", "owner", "gln", "tariffs", "subscriptions"), PLAN_TABLE
)
def test_dsos_matches_the_plan_table(
    name: str,
    owner: str,
    gln: str,
    tariffs: tuple[str, ...],
    subscriptions: tuple[str, ...],
) -> None:
    assert DSOS[name] == Dso(name, owner, gln, tariffs, subscriptions)


def test_dsos_holds_exactly_the_plan_names() -> None:
    assert set(DSOS) == {row[0] for row in PLAN_TABLE}
    assert len(DSOS) == 35


def test_dsos_keys_are_sorted_lowercase_kebab_and_equal_the_name() -> None:
    assert list(DSOS) == sorted(DSOS)
    for key, dso in DSOS.items():
        assert key == dso.name
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", key), key


def test_dsos_glns_are_thirteen_digits_and_unique_and_never_energinets() -> None:
    glns = [dso.gln for dso in DSOS.values()]
    assert all(re.fullmatch(r"\d{13}", gln) for gln in glns)
    assert len(set(glns)) == len(glns)
    assert ENERGINET_GLN not in glns


def test_dsos_codes_are_tuples_of_non_empty_strings() -> None:
    for dso in DSOS.values():
        for codes in (dso.tariff_codes, dso.subscription_codes):
            assert isinstance(codes, tuple), dso.name  # ("x") without a comma is a str
            assert all(isinstance(code, str) and code for code in codes), dso.name
        assert dso.tariff_codes, dso.name


def test_only_sunds_has_no_subscription_code() -> None:
    assert [d.name for d in DSOS.values() if not d.subscription_codes] == ["sunds"]
    assert DSOS["sunds"].subscription_codes == ()


def test_dsos_is_read_only_and_dso_is_frozen() -> None:
    with pytest.raises(TypeError):
        DSOS["x"] = DSOS["radius"]  # type: ignore[index]  # the mapping is read-only
    with pytest.raises(dataclasses.FrozenInstanceError):
        DSOS["radius"].gln = "1"  # type: ignore[misc]  # the dataclass is frozen
    assert DSOS["radius"].gln == "5790000705689"


def test_dsos_codes_may_be_shared_between_dsos_but_not_within_one_gln() -> None:
    by_code: dict[str, set[str]] = {}
    for dso in DSOS.values():
        for code in dso.tariff_codes:
            by_code.setdefault(code, set()).add(dso.name)
    assert by_code["C-Tarif"] == {"hammel", "hjerting", "kimbrer", "n1-016"}
    assert by_code["C_FBTNTR_B"] == {"konstant-151", "konstant-245"}


# --- completeness against the catalogue (T1) -----------------------------------


def _current_c_tariff_glns(catalogue: list[dict[str, str | None]]) -> set[str]:
    glns = set()
    for charge in catalogue:
        valid_to = charge["LatestValidTo"]
        note = charge["Note"] or ""
        if (
            charge["ChargeType"] == "D03"
            and (valid_to is None or valid_to > TODAY)
            and C_TARIFF_NOTE.search(note)
            and not NOT_STANDARD_NOTE.search(note)
        ):
            glns.add(str(charge["GLN_Number"]))
    return glns


def test_dsos_cover_every_gln_with_a_current_c_consumption_tariff(
    pricelist_fixture: MarketRecords,
) -> None:
    wanted = _current_c_tariff_glns(pricelist_fixture["catalogue"])  # type: ignore[arg-type]  # rows are str or None
    wanted |= {DSOS["hurup"].gln}  # Hurup's note is "Nettarif fra 0-100000 kWh"

    missing = wanted - {dso.gln for dso in DSOS.values()} - NOT_DSOS

    assert not missing
    assert len(wanted) >= 30  # the fixture really has the catalogue


def test_every_dso_gln_is_in_the_catalogue_with_a_current_tariff(
    pricelist_fixture: MarketRecords,
) -> None:
    current = {
        c["GLN_Number"]
        for c in pricelist_fixture["catalogue"]
        if c["ChargeType"] == "D03"
        and (c["LatestValidTo"] is None or c["LatestValidTo"] > TODAY)
    }
    assert {dso.gln for dso in DSOS.values()} <= current


def test_every_mapped_code_exists_under_its_gln_and_charge_type(
    pricelist_fixture: MarketRecords,
) -> None:
    known = {
        (c["GLN_Number"], c["ChargeType"], c["ChargeTypeCode"])
        for c in pricelist_fixture["catalogue"]
    }
    for dso in DSOS.values():
        for code in dso.tariff_codes:
            assert (dso.gln, "D03", code) in known, (dso.name, code)
        for code in dso.subscription_codes:
            assert (dso.gln, "D01", code) in known, (dso.name, code)


def test_catalogue_owners_match_the_dso_owner_names(
    pricelist_fixture: MarketRecords,
) -> None:
    owners = {
        c["GLN_Number"]: c["ChargeOwner"]
        for c in pricelist_fixture["catalogue"]
        if c["ChargeOwner"]
    }
    for dso in DSOS.values():
        # the price list spells Midtfyns' name with a double space
        assert " ".join(str(owners[dso.gln]).split()) == dso.owner, dso.name


def test_the_catalogue_carries_no_prices(pricelist_fixture: MarketRecords) -> None:
    assert not any(
        key.startswith("Price") for c in pricelist_fixture["catalogue"] for key in c
    )


# --- README (T8) ---------------------------------------------------------------


def test_readme_lists_every_dso_with_its_gln_and_codes() -> None:
    text = README.read_text(encoding="utf-8")
    for dso in DSOS.values():
        line = next(
            (ln for ln in text.splitlines() if ln.startswith(f"| `{dso.name}`")), ""
        )
        assert line, dso.name
        assert dso.gln in line, dso.name
        for code in (*dso.tariff_codes, *dso.subscription_codes):
            assert f"`{code}`" in line, (dso.name, code)


def test_readme_names_the_five_functions_dataset_and_units() -> None:
    text = README.read_text(encoding="utf-8")
    for needle in (
        "get_dso_tariffs",
        "get_energinet_tariffs",
        "get_dso_subscriptions",
        "get_energinet_subscriptions",
        "get_electricity_tax",
        "DatahubPricelist",
        "DKK/kWh",
        "DKK/month",
        "`EA-001`",
        "`41004`",
        "5790000432752",
    ):
        assert needle in text, needle


# --- module ----------------------------------------------------------------------


def test_main_prints_every_name_and_gln(capsys: pytest.CaptureFixture[str]) -> None:
    main()

    out = capsys.readouterr().out
    for dso in DSOS.values():
        assert dso.name in out
        assert dso.gln in out


def test_dsos_module_is_documented() -> None:
    assert dsos.__doc__
    assert Dso.__doc__
