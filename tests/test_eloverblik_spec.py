"""Contract test against the published OpenAPI spec, the live suite's wiring and the docs."""

import json
import os
import re
import subprocess
import sys
import uuid
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pandas as pd
import pytest
import test_eloverblik_live as live_suite
from conftest import (
    ELOVERBLIK_POINT_A,
    ELOVERBLIK_POINT_B,
    EloverblikService,
    eloverblik_point,
)
from test_eloverblik_live import (
    _body_shape,
    _is_resolution_refusal,
    _refusal,
    _report_year_raw,
    _scrub_raw,
    scrub,
)

import energydata.eloverblik as package
from energydata.eloverblik import (
    AGGREGATIONS,
    BASE_URL,
    REFRESH_TOKEN_ENV,
    EloverblikError,
    _common,
    charges,
    client,
    get_charges,
    get_meter_data,
    get_metering_point_details,
    get_metering_points,
    meterdata,
    meteringpoints,
)
from energydata.eloverblik.meteringpoints import _DETAIL_FIELDS, _POINT_TEXT

REPO = Path(__file__).resolve().parent.parent
SPEC: dict[str, Any] = json.loads(
    (REPO / "tests" / "fixtures" / "eloverblik_openapi.json").read_text(
        encoding="utf-8"
    )
)
SCHEMAS: dict[str, Any] = SPEC["components"]["schemas"]
PREFIX = "/customerapi/api"


def _operation(path: str, method: str) -> dict[str, Any]:
    operation: dict[str, Any] = SPEC["paths"][f"{PREFIX}{path}"][method.lower()]
    return operation


def _props(name: str) -> set[str]:
    return set(SCHEMAS[name]["properties"])


# --- the package reads what the spec publishes ---------------------------------------


def test_the_base_url_is_the_specs_server_path() -> None:
    assert BASE_URL.endswith(PREFIX)
    assert f"https://api.eloverblik.dk{PREFIX}" == BASE_URL
    assert SPEC["openapi"].startswith("3.0")


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/token"),
        ("GET", "/isalive"),
        ("GET", "/meteringpoints/meteringpoints"),
        ("POST", "/meteringpoints/meteringpoint/getdetails"),
        ("POST", "/meteringpoints/meteringpoint/getcharges"),
        ("POST", "/meterdata/gettimeseries/{dateFrom}/{dateTo}/{aggregation}"),
    ],
)
def test_every_endpoint_the_package_calls_exists_in_the_spec(
    method: str, path: str
) -> None:
    assert method.lower() in SPEC["paths"][f"{PREFIX}{path}"]


def test_the_listing_takes_the_include_all_query_parameter() -> None:
    names = {
        p["name"]: p
        for p in _operation("/meteringpoints/meteringpoints", "GET")["parameters"]
    }

    assert names["includeAll"]["in"] == "query"


def test_the_timeseries_path_parameters_and_aggregation_names() -> None:
    operation = _operation(
        "/meterdata/gettimeseries/{dateFrom}/{dateTo}/{aggregation}", "POST"
    )
    params = {p["name"] for p in operation["parameters"] if p["in"] == "path"}
    described = next(p for p in operation["parameters"] if p["name"] == "aggregation")

    assert params == {"dateFrom", "dateTo", "aggregation"}
    for name in meterdata._API_NAME.values():
        assert f"'{name}'" in described["description"]
    assert set(meterdata._API_NAME) == set(AGGREGATIONS)


def test_the_request_body_schema_is_the_nested_metering_point_list() -> None:
    assert _props("MeteringPointsRequest") == {"meteringPoints"}
    assert _props("MeteringPoints") == {"meteringPoint"}


def test_the_listing_fields_the_package_reads_exist_in_the_spec() -> None:
    fields = {source for _, source in _POINT_TEXT} | {
        "hasRelation",
        "isMovedOut",
        "childMeteringPoints",
    }

    assert fields <= _props("MeteringPointApiDto")
    assert "meteringPointId" in _props("ChildMeteringPointDto")


def test_the_detail_fields_are_every_scalar_of_the_spec_in_spec_order() -> None:
    scalars = [
        n
        for n in SCHEMAS["MeteringPointDetailsCustomerDto"]["properties"]
        if n not in ("contactAddresses", "childMeteringPoints")
    ]

    assert scalars == _DETAIL_FIELDS


def test_the_charge_fields_the_package_reads_exist_in_the_spec() -> None:
    common = {
        "priceId",
        "name",
        "description",
        "owner",
        "validFromDate",
        "validToDate",
        "periodType",
    }

    assert common <= _props("MeteringPointChargeDto")
    assert common <= _props("MeteringPointTariffDto")
    assert {"price", "quantity"} <= _props("MeteringPointChargeDto")
    assert "prices" in _props("MeteringPointTariffDto")
    assert {"position", "price"} <= _props("MeteringPointTariffPriceDto")
    assert {"subscriptions", "tariffs", "fees"} <= _props("MeteringPointFeesDto")


def test_the_timeseries_fields_the_package_reads_exist_in_the_spec() -> None:
    assert {"success", "errorCode", "errorText", "id"} <= _props(
        "MyEnergyDataMarketDocumentResponse"
    )
    assert "MyEnergyData_MarketDocument" in _props("MyEnergyDataMarketDocumentResponse")
    assert "TimeSeries" in _props("MyEnergyDataMarketDocument")
    assert {"mRID", "measurement_Unit.name", "Period"} <= _props("TimeSeries")
    assert {"resolution", "timeInterval", "Point"} <= _props("Period")
    assert "start" in _props("PeriodtimeInterval")
    assert {"position", "out_Quantity.quantity", "out_Quantity.quality"} <= _props(
        "Point"
    )


# --- the mock's bodies validate against the spec ------------------------------------------


def _check(value: object, schema: Mapping[str, Any], where: str) -> None:
    """Assert `value` is a valid instance of `schema` (a small OpenAPI 3.0 subset)."""
    if "$ref" in schema:
        _check(value, SCHEMAS[schema["$ref"].rsplit("/", 1)[1]], where)
        return
    if value is None:
        assert schema.get("nullable"), f"{where}: null is not allowed"
        return
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        assert isinstance(value, dict), f"{where}: not an object"
        known = schema.get("properties", {})
        for key, item in value.items():
            assert key in known, f"{where}: '{key}' is not in the spec"
            _check(item, known[key], f"{where}.{key}")
    elif kind == "array":
        assert isinstance(value, list), f"{where}: not an array"
        for number, item in enumerate(value):
            _check(item, schema["items"], f"{where}[{number}]")
    elif kind == "string":
        assert isinstance(value, str), f"{where}: not a string"
    elif kind == "boolean":
        assert isinstance(value, bool), f"{where}: not a boolean"
    elif kind == "integer":
        assert isinstance(value, int) and not isinstance(value, bool), where
    elif kind == "number":
        assert isinstance(value, int | float) and not isinstance(value, bool), where


def _recorded(service: EloverblikService) -> dict[str, Any]:
    """The 200 body of each endpoint family, replayed through the mock."""
    with service.client() as c:
        get_metering_points(client=c)
        get_metering_point_details(client=c)
        get_charges(client=c)
        for aggregation, start, end in (
            ("hour", "2026-10-25", "2026-10-26"),
            ("day", "2026-10-01", "2026-10-03"),
            ("month", "2026-01-01", "2026-03-01"),
            ("year", "2024-01-01", "2026-01-01"),
        ):
            get_meter_data(start, end, aggregation=aggregation, client=c)  # type: ignore[arg-type]  # parametrised text
    bodies: dict[str, Any] = {}
    for request in list(service.requests):
        response = service(request)  # replay: the mock is deterministic
        if request.url.path.endswith("/token") or response.status_code != 200:
            continue
        bodies[request.url.path.split(PREFIX, 1)[1]] = response.json()
    return bodies


def test_the_mocks_bodies_validate_against_the_spec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = pd.Timestamp("2026-11-02 12:00", tz="Europe/Copenhagen")
    monkeypatch.setattr(
        pd.Timestamp,
        "now",
        classmethod(lambda _c, tz=None: clock if tz is None else clock.tz_convert(tz)),
    )
    service = EloverblikService(
        [eloverblik_point(ELOVERBLIK_POINT_A), eloverblik_point(ELOVERBLIK_POINT_B)]
    )
    service.unlinked = [eloverblik_point("571313100000000009")]
    service.a02 = {"2026-10-24T22:00:00Z"}

    bodies = _recorded(service)

    expect = {
        "/meteringpoints/meteringpoints": "MeteringPointApiDtoListApiResponse",
        "/meteringpoints/meteringpoint/getdetails": "MeteringPointDetailsCustomerDtoResponseListApiResponse",
        "/meteringpoints/meteringpoint/getcharges": "MeteringPointFeesDtoResponseListApiResponse",
    }
    for path, schema in expect.items():
        _check(bodies[path], SCHEMAS[schema], path)
    timeseries = [p for p in bodies if "/gettimeseries/" in p]
    assert len(timeseries) >= 4
    for path in timeseries:
        _check(
            bodies[path],
            SCHEMAS["MyEnergyDataMarketDocumentResponseListApiResponse"],
            path,
        )


def test_the_mocks_error_bodies_are_problem_details() -> None:
    service = EloverblikService()
    body = {"meteringPoints": {"meteringPoint": [ELOVERBLIK_POINT_A]}}
    path = "/meterdata/gettimeseries/2026-01-01/2026-01-01/Day"

    with service.client() as c, pytest.raises(EloverblikError):
        c.get_json("POST", path, body=body)

    bad = [r for r in service.requests if "gettimeseries" in r.url.path][0]
    problem = service(bad).json()
    assert set(problem) <= _props("ProblemDetails") | {"errorCode"}
    assert "errorCode" in problem


def test_the_mocks_invalid_period_error_is_the_real_bare_string() -> None:
    service = EloverblikService()
    body = {"meteringPoints": {"meteringPoint": [ELOVERBLIK_POINT_A]}}
    path = "/meterdata/gettimeseries/2026-01-02/2026-01-01/Day"

    with service.client() as c, pytest.raises(EloverblikError) as caught:
        c.get_json("POST", path, body=body)

    made = len(service.requests)
    bad = [r for r in service.requests if "gettimeseries" in r.url.path][0]
    assert (
        service(bad).json() == "#30001: Period not allowed, ToDate is before FromDate."
    )
    assert caught.value.error_code == 30001
    assert caught.value.error_text == "Period not allowed, ToDate is before FromDate."
    assert caught.value.status == 400
    assert caught.value.metering_point_id is None
    assert caught.value.correlation_id == str(
        uuid.UUID(int=made, version=4)
    )
    assert str(caught.value) == (
        f"Eloverblik API: HTTP 400 error 30001 on {path}: Period not allowed, "
        f"ToDate is before FromDate. (correlation id {caught.value.correlation_id})"
    )


# --- exports, showcases ----------------------------------------------------------------------


def test_the_package_exports_exactly_the_documented_names() -> None:
    names = {
        "BASE_URL",
        "REFRESH_TOKEN_ENV",
        "DEFAULT_POLICY",
        "EloverblikClient",
        "EloverblikError",
        "MissingTokenError",
        "Aggregation",
        "AGGREGATIONS",
        "get_charges",
        "get_meter_data",
        "get_metering_point_details",
        "get_metering_points",
    }

    assert names <= set(dir(package))
    assert package.REFRESH_TOKEN_ENV == "ELOVERBLIK_REFRESH_TOKEN"
    assert client.REFRESH_TOKEN_ENV == REFRESH_TOKEN_ENV


@pytest.mark.parametrize("module", [client, meteringpoints, charges])
def test_a_live_showcase_prints_the_no_token_line_without_a_token(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv(REFRESH_TOKEN_ENV, raising=False)

    module.main()

    out = capsys.readouterr().out
    assert out.strip() == (
        f"Set {REFRESH_TOKEN_ENV} to run this showcase against the live Eloverblik API."
    )


def test_the_common_showcase_runs_offline(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(REFRESH_TOKEN_ENV, raising=False)

    _common.main()

    out = capsys.readouterr().out
    assert "23 ids -> batches of [10, 10, 3]" in out
    assert "refused:" in out
    assert "Set ELOVERBLIK_REFRESH_TOKEN" in out


def test_the_meter_data_showcase_runs_offline_up_to_the_live_call(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(REFRESH_TOKEN_ENV, raising=False)

    meterdata.main()

    out = capsys.readouterr().out
    assert "request window(s)" in out
    assert "refused:" in out
    assert "Set ELOVERBLIK_REFRESH_TOKEN" in out


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_token_counts_as_none_for_the_showcases(
    blank: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(REFRESH_TOKEN_ENV, blank)

    charges.main()

    assert "Set ELOVERBLIK_REFRESH_TOKEN" in capsys.readouterr().out


@pytest.mark.parametrize(
    "name", ["client", "_common", "meteringpoints", "meterdata", "charges"]
)
def test_every_module_runs_standalone(name: str) -> None:
    env = {k: v for k, v in os.environ.items() if k != REFRESH_TOKEN_ENV}
    env["PYTHONPATH"] = str(REPO / "src")

    done = subprocess.run(
        [sys.executable, "-W", "ignore", "-m", f"energydata.eloverblik.{name}"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )

    assert done.returncode == 0, done.stderr
    assert "Set ELOVERBLIK_REFRESH_TOKEN" in done.stdout


# --- the live suite: wiring, offline run, scrubbing ---------------------------------------------

LIVE = "tests/test_eloverblik_live.py"
DUMMY_TOKEN = "dummy-refresh-token-never-sent"


def _pytest(
    args: list[str], env: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    base = {
        k: v
        for k, v in os.environ.items()
        if k
        not in (
            REFRESH_TOKEN_ENV,
            "ELOVERBLIK_LIVE_MOCK",
            "ELOVERBLIK_LIVE_REPORT",
            "PYTEST_ADDOPTS",
        )
    }
    base.update(PYTHONIOENCODING="utf-8", PYTHONPATH=str(REPO / "src"))
    base.update(env or {})
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args],
        cwd=REPO,
        env=base,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
        check=False,
    )


def test_plain_pytest_deselects_the_live_tests_even_with_a_token_set() -> None:
    done = _pytest([LIVE], {REFRESH_TOKEN_ENV: DUMMY_TOKEN})

    assert "deselected" in done.stdout
    assert " passed" not in done.stdout
    assert "failed" not in done.stdout


def test_the_live_tests_are_skipped_without_a_token() -> None:
    done = _pytest([LIVE, "-m", "live", "-rs"])

    assert done.returncode == 0, done.stdout
    assert "skipped" in done.stdout
    assert " passed" not in done.stdout
    assert "ELOVERBLIK_REFRESH_TOKEN" in done.stdout


def test_a_blank_token_skips_the_live_tests() -> None:
    done = _pytest([LIVE, "-m", "live"], {REFRESH_TOKEN_ENV: "   "})

    assert "skipped" in done.stdout
    assert " passed" not in done.stdout


def test_every_live_test_is_marked_live_and_the_marker_names_both_apis() -> None:
    done = _pytest(
        [LIVE, "-o", "addopts=", "--collect-only", "-q", "-m", "live"],
        {REFRESH_TOKEN_ENV: DUMMY_TOKEN},
    )
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")

    collected = [line for line in done.stdout.splitlines() if "::test_" in line]
    assert len(collected) == 17
    assert "MINSTROEM_API_KEY" in text
    assert "ELOVERBLIK_REFRESH_TOKEN" in text


@pytest.fixture(scope="module")
def mock_run(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[subprocess.CompletedProcess[str], str]:
    report = tmp_path_factory.mktemp("live") / "report.txt"
    report.write_text("", encoding="utf-8")
    done = _pytest(
        [LIVE, "-m", "live", "--tb=long", "-rA"],
        {
            REFRESH_TOKEN_ENV: f"  {DUMMY_TOKEN}  ",
            "ELOVERBLIK_LIVE_MOCK": "1",
            "ELOVERBLIK_LIVE_REPORT": str(report),
        },
    )
    return done, report.read_text(encoding="utf-8")


def test_the_live_suite_passes_offline_against_the_mock(
    mock_run: tuple[subprocess.CompletedProcess[str], str],
) -> None:
    done, _ = mock_run

    assert done.returncode == 0, done.stdout[-3000:]
    assert "17 passed" in done.stdout
    assert "failed" not in done.stdout
    assert "skipped" not in done.stdout


def test_the_offline_live_run_leaks_nothing_private(
    mock_run: tuple[subprocess.CompletedProcess[str], str],
) -> None:
    done, report = mock_run
    everything = done.stdout + done.stderr + report

    assert DUMMY_TOKEN not in everything
    assert not re.search(r"(?<!\d)\d{18}(?!\d)", everything)
    for private in ("Test Person", "Testvej", "Testby", "Test Supplier", "99887766"):
        assert private not in everything
    for field in (
        "firstConsumerPartyName",
        "consumerCVR",
        "protectedName",
        "streetName",
    ):
        assert field not in report  # no master-data value reaches the report
    assert "..." in report  # ids are shortened, not dropped


def test_the_offline_live_run_reports_every_probe(
    mock_run: tuple[subprocess.CompletedProcess[str], str],
) -> None:
    _, report = mock_run

    for line in (
        "refresh token was accepted",
        "linked metering point(s)",
        "detail row(s)",
        "quality codes seen",
        "quarter:",
        "charge row(s)",
        "invalid request: status=400 code=30001",
        "730 days: accepted",
        "child ...0099: error status=",
        "invalid request body: content_type=",
        "success=True errorCode=10000",
    ):
        assert line in report, line
    lines = [
        line
        for line in report.splitlines()
        if line.startswith("[test_every_aggregation_on_a_recent_period")
    ]
    assert sum("errorCode=" not in line for line in lines) == 5
    assert sum("errorCode=" in line for line in lines) == 2  # the year probe, per point


def test_the_offline_live_run_never_reaches_a_real_transport(
    mock_run: tuple[subprocess.CompletedProcess[str], str],
) -> None:
    done, _ = mock_run

    assert "real network" not in done.stdout.lower()
    assert "ConnectError" not in done.stdout
    assert "getaddrinfo" not in done.stdout


def test_a_failing_live_request_is_reported_without_its_path_or_ids() -> None:
    """With a dummy token the mock refuses it: the failure must not echo it."""
    done = _pytest(
        [LIVE, "-m", "live", "--tb=long", "-k", "test_token_is_accepted"],
        {
            REFRESH_TOKEN_ENV: DUMMY_TOKEN,
            "ELOVERBLIK_LIVE_MOCK": "1",
        },
    )

    assert "1 passed" in done.stdout
    assert DUMMY_TOKEN not in done.stdout


# --- scrub (the runner's filter) -----------------------------------------------------------------


def test_scrub_shortens_an_18_digit_id_to_its_last_four_digits() -> None:
    assert scrub("point 571313100000000001 ok") == "point ...0001 ok"


def test_scrub_leaves_shorter_and_longer_digit_runs_alone() -> None:
    assert scrub("57131310000000001") == "57131310000000001"
    assert scrub("5713131000000000012") == "5713131000000000012"


def test_scrub_shortens_every_id_in_a_text() -> None:
    assert scrub("571313100000000001, 571313100000000002") == "...0001, ...0002"


def test_scrub_removes_every_given_secret_and_ignores_empty_ones() -> None:
    text = "token SECRET-abc and again SECRET-abc"

    assert scrub(text, ["SECRET-abc", ""]) == "token <token> and again <token>"


def test_scrub_removes_a_jwt_even_when_it_is_not_given() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.c2lnbmF0dXJl"

    assert scrub(f"Bearer {jwt}!") == "Bearer <token>!"


def test_scrub_is_idempotent_and_keeps_ordinary_text() -> None:
    once = scrub("a 571313100000000001 b æøå", ["x" * 5])

    assert scrub(once) == once == "a ...0001 b æøå"


# --- docs -------------------------------------------------------------------------------------------


def _read(relative: str) -> str:
    return (REPO / relative).read_text(encoding="utf-8")


def test_the_readme_names_every_function_and_the_variable() -> None:
    readme = _read("README.md")

    for name in (
        "get_meter_data",
        "get_metering_points",
        "get_metering_point_details",
        "get_charges",
        "EloverblikClient",
        "EloverblikError",
        "MissingTokenError",
        "ELOVERBLIK_REFRESH_TOKEN",
        "Opret token",
    ):
        assert name in readme, name


def test_structure_md_lists_every_eloverblik_module_and_test() -> None:
    text = _read("STRUCTURE.md")

    for path in (
        "client.py",
        "_common.py",
        "meteringpoints.py",
        "meterdata.py",
        "charges.py",
        "__init__.py",
    ):
        assert f"src/energydata/eloverblik/{path}" in text, path
    assert "structure-eloverblik.md" in text
    assert "tests/test_eloverblik_live.py" in text


def test_the_rules_file_lists_every_public_signature() -> None:
    rules = _read(".claude/rules/structure-eloverblik.md")

    for name in (
        "BASE_URL",
        "REFRESH_TOKEN_ENV",
        "DEFAULT_POLICY",
        "EloverblikError",
        "MissingTokenError",
        "EloverblikClient",
        "has_refresh_token",
        "fetch_json",
        "get_json",
        "get_metering_points",
        "get_metering_point_details",
        "get_meter_data",
        "get_charges",
        "Aggregation",
        "AGGREGATIONS",
        "scrub",
        "BATCH_SIZE",
    ):
        assert name in rules, name


def test_the_utils_rules_file_documents_headers_json_and_gather_ordered() -> None:
    rules = _read(".claude/rules/structure-utils.md")

    assert r"headers: Mapping[str, str] \| None = None, json: object = None" in rules
    assert "gather_ordered" in rules
    assert "_gather_ordered" not in rules


def test_the_package_exposes_no_name_the_docs_do_not_mention() -> None:
    rules = _read(".claude/rules/structure-eloverblik.md")
    public = {n for n in dir(package) if not n.startswith("_")} - {
        "charges",
        "client",
        "meterdata",
        "meteringpoints",
    }

    for name in public:
        assert name in rules, name


# --- the live suite's helpers (the step-8 send-back) ------------------------------------------------

A, B = ELOVERBLIK_POINT_A, ELOVERBLIK_POINT_B
TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlc2lnbmF0dXJl"
DIGITS = re.compile(r"\d{6,}")


@pytest.fixture
def pinned_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Today is 2026-11-02 12:00 Copenhagen for the package, the mock and the suite."""
    now = pd.Timestamp("2026-11-02 12:00", tz="Europe/Copenhagen")

    def fixed(_cls: type, tz: str | None = None) -> pd.Timestamp:
        return now if tz is None else now.tz_convert(tz)

    monkeypatch.setattr(pd.Timestamp, "now", classmethod(fixed))


def _error_with_body(
    body: str, *, content_type: str | None = None, status: int = 400
) -> EloverblikError:
    """An `EloverblikError` chained on an HTTP error whose response holds `body`."""
    headers = {"Content-Type": content_type} if content_type else {}
    request = httpx.Request("POST", "https://example.invalid/x")
    response = httpx.Response(
        status, content=body.encode(), headers=headers, request=request
    )
    error = EloverblikError("bad", status=status)
    error.__cause__ = httpx.HTTPStatusError("bad", request=request, response=response)
    return error


def _mismatch(aggregation: str, label: object) -> EloverblikError:
    """The error `get_meter_data` raises when the mock answers with `label`."""
    service = EloverblikService([eloverblik_point(A)])
    service.resolution_override[A] = label  # type: ignore[assignment]  # None is a payload case
    end = {"quarter": "2026-10-02", "hour": "2026-10-02", "day": "2026-10-03"}[
        aggregation
    ]
    with service.client() as client_, pytest.raises(EloverblikError) as caught:
        get_meter_data(
            "2026-10-01",
            end,
            aggregation=aggregation,  # type: ignore[arg-type]  # parametrised text
            metering_point_ids=A,
            client=client_,
        )
    return caught.value


def test_body_shape_never_leaks_a_token_cut_by_the_300_character_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(REFRESH_TOKEN_ENV, TOKEN)
    error = _error_with_body("x" * 270 + TOKEN)

    shape = _body_shape(error)

    assert TOKEN[:20] not in shape
    assert "eyJhbGciOiJIUzI1NiJ9" not in shape


def test_body_shape_never_leaks_a_digit_run_cut_by_the_300_character_limit() -> None:
    error = _error_with_body("x" * 290 + A)

    shape = _body_shape(error)

    assert A[:10] not in shape
    assert not DIGITS.search(shape)


@pytest.mark.parametrize(
    "secret",
    ["12345678", "010203-1234", "571313100000008261", "123456", "1234567890123"],
)
def test_body_shape_redacts_cvr_cpr_and_long_digit_runs(secret: str) -> None:
    error = _error_with_body(f'{{"detail": "owner {secret} unknown"}}')

    shape = _body_shape(error)

    assert not DIGITS.search(shape)
    assert "<digits>" in shape or "...8261" in shape


def test_body_shape_keeps_short_numbers_and_shortened_ids_readable() -> None:
    error = _error_with_body(f'{{"status": 400, "id": "{A}"}}')

    shape = _body_shape(error)

    assert "400" in shape
    assert f"...{A[-4:]}" in shape


def test_body_shape_reports_type_keys_and_excerpt_of_a_problem_details_body() -> None:
    body = f'{{"type": "x", "status": 400, "point": "{A}"}}'
    error = _error_with_body(body, content_type="application/problem+json")

    shape = _body_shape(error)

    assert "content_type='application/problem+json'" in shape
    assert "keys=['point', 'status', 'type']" in shape
    assert f"...{A[-4:]}" in shape


@pytest.mark.parametrize("body", ["Bad Request", "[1]", ""])
def test_body_shape_says_so_when_the_body_is_not_a_json_object(body: str) -> None:
    shape = _body_shape(_error_with_body(body))

    assert "keys=not a JSON object" in shape
    assert "content_type=None" in shape


def test_body_shape_caps_the_excerpt_at_300_characters() -> None:
    shape = _body_shape(_error_with_body("y" * 1000))

    assert "y" * 300 in shape
    assert "y" * 301 not in shape


@pytest.mark.parametrize("cause", [None, ValueError("x")])
def test_body_shape_without_a_chained_response_says_so(
    cause: Exception | None,
) -> None:
    error = EloverblikError("payload")
    error.__cause__ = cause

    assert _body_shape(error) == "no HTTP response is attached to the error"


def test_refusal_scrubs_and_caps_the_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(REFRESH_TOKEN_ENV, "SECRET-refresh-xyz")

    line = _refusal(EloverblikError(f"point {A} SECRET-refresh-xyz " + "y" * 300))

    assert f"...{A[-4:]}" in line
    assert "<token>" in line
    assert A not in line
    assert "SECRET-refresh-xyz" not in line
    assert "y" * 200 not in line


def test_refusal_scrubs_the_error_text_and_redacts_digit_runs() -> None:
    error = EloverblikError(
        "m",
        status=200,
        error_code=30001,
        error_text=f"point {A} owner 12345678 " + "z" * 300,
    )

    line = _refusal(error)

    assert A not in line
    assert not DIGITS.search(line)
    assert "z" * 201 not in line
    assert "status=200 code=30001" in line


@pytest.mark.usefixtures("pinned_clock")
def test_refusal_reports_the_package_raised_message_when_status_is_none() -> None:
    line = _refusal(_mismatch("quarter", "PT1H"))

    assert "error status=None code=None text=None message=" in line
    assert "returned resolution 'PT1H' for aggregation 'quarter'" in line


@pytest.mark.usefixtures("pinned_clock")
def test_the_quarter_refusal_is_the_designed_one() -> None:
    assert _is_resolution_refusal(_mismatch("quarter", "PT1H")) is True


@pytest.mark.parametrize(
    ("aggregation", "label"), [("day", "PT1H"), ("day", "P1M"), ("hour", "PT15M")]
)
@pytest.mark.usefixtures("pinned_clock")
def test_no_other_resolution_mismatch_is_a_designed_refusal(
    aggregation: str, label: str
) -> None:
    assert _is_resolution_refusal(_mismatch(aggregation, label)) is False


@pytest.mark.usefixtures("pinned_clock")
def test_a_period_with_no_resolution_is_not_a_designed_refusal() -> None:
    assert _is_resolution_refusal(_mismatch("quarter", None)) is False


def test_only_a_package_raised_mismatch_is_a_designed_refusal() -> None:
    text = (
        "point x returned resolution 'PT1H' for aggregation 'quarter', expected 'PT15M'"
    )

    assert not _is_resolution_refusal(
        EloverblikError(text, status=200, error_code=30001)
    )
    assert not _is_resolution_refusal(EloverblikError(text, status=400))
    assert not _is_resolution_refusal(
        EloverblikError("point x reports unit 'MWH', expected KWH")
    )
    assert _is_resolution_refusal(EloverblikError(text))


@pytest.mark.parametrize(
    ("aggregation", "label"),
    [("day", "P1M"), ("hour", "PT15M"), ("month", "P1Y"), ("year", "P1M")],
)
@pytest.mark.usefixtures("pinned_clock")
def test_the_aggregation_test_fails_on_a_mismatch_other_than_quarter(
    aggregation: str, label: str
) -> None:
    service = EloverblikService([eloverblik_point(A)])
    service.resolution_override[A] = label
    lines: list[str] = []

    with service.client(service.refresh_token) as live, pytest.raises(AssertionError):
        live_suite.test_every_aggregation_on_a_recent_period(
            live,
            lines.append,
            aggregation,  # type: ignore[arg-type]  # parametrised text
        )

    assert not any("refused" in line for line in lines)


@pytest.mark.usefixtures("pinned_clock")
def test_the_aggregation_test_records_quarter_on_an_hourly_point() -> None:
    service = EloverblikService([eloverblik_point(A)])
    service.resolution_override[A] = "PT1H"
    lines: list[str] = []

    with service.client(service.refresh_token) as live:
        live_suite.test_every_aggregation_on_a_recent_period(
            live, lines.append, "quarter"
        )

    assert len(lines) == 1
    assert "refused, error status=None code=None text=None message=" in lines[0]
    assert "returned resolution 'PT1H' for aggregation 'quarter'" in lines[0]


@pytest.mark.usefixtures("pinned_clock")
def test_the_aggregation_test_failure_is_scrubbed_and_records_nothing() -> None:
    service = EloverblikService([eloverblik_point(A)])
    service.codes[A] = {None: 30001}
    lines: list[str] = []

    with (
        service.client(service.refresh_token) as live,
        pytest.raises(AssertionError) as caught,
    ):
        live_suite.test_every_aggregation_on_a_recent_period(live, lines.append, "hour")

    assert A not in str(caught.value)
    assert lines == []


@pytest.mark.usefixtures("pinned_clock")
def test_the_quarter_probe_records_a_mismatch_per_point() -> None:
    service = EloverblikService([eloverblik_point(A)])
    service.resolution_override[A] = "PT1H"
    lines: list[str] = []

    with service.client(service.refresh_token) as live:
        live_suite.test_quarter_on_each_point(live, lines.append)

    assert len(lines) == 1
    assert lines[0].startswith(f"...{A[-4:]} quarter: error status=None")


@pytest.mark.usefixtures("pinned_clock")
def test_the_year_probe_reports_a_tolerated_code_and_each_points_periods() -> None:
    service = EloverblikService([eloverblik_point(A), eloverblik_point(B)])
    service.codes[A] = {None: 30018}
    lines: list[str] = []

    with service.client(service.refresh_token) as live:
        _report_year_raw(live, lines.append, date(2025, 1, 1), date(2026, 1, 1))

    assert len(lines) == 2
    first = next(line for line in lines if f"...{A[-4:]}" in line)
    second = next(line for line in lines if f"...{B[-4:]}" in line)
    assert "success=False errorCode=30018" in first
    assert "series=0 periods=0" in first
    assert "resolutions=['P1Y']" in second
    assert "periods=1" in second
    assert not any(re.search(r"\d{18}", line) for line in lines)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("CVR 12345678 x", "CVR <digits> x"),
        ("010203-1234", "<digits>-1234"),
        ("12345 stays", "12345 stays"),
        ("id 571313100000008261", "id ...8261"),
        ("2026-10-01 stays", "2026-10-01 stays"),
    ],
)
def test_scrub_raw_redacts_runs_of_six_or_more_digits(text: str, expected: str) -> None:
    assert _scrub_raw(text) == expected
