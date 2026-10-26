"""The package against its outside promises: the OpenAPI spec, its docs, the live wiring."""

import ast
import inspect
import json
import os
import re
import subprocess
import sys
import types
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, NamedTuple

import httpx
import pandas as pd
import pytest
from conftest import ADDRESS_ID, MinStroemService, minstroem_price_row

import energydata.minstroem as package
from energydata.minstroem import (
    API_KEY_ENV,
    BASE_URL,
    MinStroemClient,
    MinStroemError,
    _series,
    client,
    costs,
    get_addresses,
    get_consumption,
    get_consumption_costs,
    get_forecast_prices,
    get_prices,
    get_prices_with_forecast,
    get_solar_export,
    get_solar_export_revenue,
    prices,
    usage,
)

REPO = Path(__file__).resolve().parents[1]
SPEC: dict[str, Any] = json.loads(
    (REPO / "tests" / "fixtures" / "minstroem_openapi.json").read_text(encoding="utf-8")
)
README = (REPO / "README.md").read_text(encoding="utf-8")
STRUCTURE = (REPO / "STRUCTURE.md").read_text(encoding="utf-8")
RULES = (REPO / ".claude" / "rules" / "structure-minstroem.md").read_text(
    encoding="utf-8"
)
PACKAGE_DIR = REPO / "src" / "energydata" / "minstroem"
LIVE_COMMAND = (
    "$env:MINSTROEM_API_KEY='<din-nøgle>'; .venv\\Scripts\\python.exe -m pytest "
    "tests/test_minstroem_live.py -m live -rA --tb=short -p no:cacheprovider; "
    "Remove-Item Env:MINSTROEM_API_KEY"
)

PUBLIC_FUNCTIONS = sorted(
    name
    for name, value in vars(package).items()
    if inspect.isfunction(value) and not name.startswith("_")
)


# --- reading the spec ---------------------------------------------------------


def _resolve(node: Any) -> Any:  # noqa: ANN401 - a node of an arbitrary JSON document
    """Follow `$ref`s to the node they name."""
    while isinstance(node, dict) and "$ref" in node:
        target: Any = SPEC
        for part in node["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        node = target
    return node


def _schema(name: str) -> dict[str, Any]:
    schema: dict[str, Any] = SPEC["components"]["schemas"][name]
    return schema


def _operation(path: str) -> dict[str, Any]:
    operation: dict[str, Any] = SPEC["paths"][path]["get"]
    return operation


def _query_parameters(path: str) -> dict[str, dict[str, Any]]:
    resolved = [_resolve(p) for p in _operation(path).get("parameters", [])]
    return {p["name"]: p for p in resolved if p["in"] == "query"}


def _check(value: object, schema: Any, where: str) -> list[str]:  # noqa: ANN401 - JSON schema node
    """A small JSON-schema check: types, required, no extras, enum, const, items."""
    schema = _resolve(schema)
    problems: list[str] = []
    if "const" in schema and value != schema["const"]:
        problems.append(f"{where}: {value!r} is not the constant {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        problems.append(f"{where}: {value!r} is not one of {schema['enum']}")
    declared = schema.get("type")
    kinds = [declared] if isinstance(declared, str) else list(declared or [])
    checks: dict[str, Callable[[object], bool]] = {
        "string": lambda v: isinstance(v, str),
        "number": lambda v: isinstance(v, int | float) and not isinstance(v, bool),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "null": lambda v: v is None,
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
    }
    if kinds and not any(checks[kind](value) for kind in kinds):
        problems.append(f"{where}: {value!r} is not of type {kinds}")
        return problems
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                problems.append(f"{where}: missing required '{name}'")
        if schema.get("additionalProperties") is False:
            problems.extend(
                f"{where}: unexpected '{name}'"
                for name in value
                if name not in properties
            )
        for name, item in value.items():
            if name in properties:
                problems.extend(_check(item, properties[name], f"{where}.{name}"))
    if isinstance(value, list) and "items" in schema:
        for position, item in enumerate(value):
            problems.extend(_check(item, schema["items"], f"{where}[{position}]"))
    return problems


# Spec field names whose column is not a plain snake-case of them.
COLUMN_OF = {
    "energyKWh": "energy_kwh",
    "totalPricePerKWh": "total_price_per_kwh",
    "variableChargesPerKWh": "variable_charges_per_kwh",
    "fixedFeesForInterval": "fixed_fees",
}


def _snake(name: str) -> str:
    return re.sub(r"([A-Z])", r"_\1", name).lower()


# === T11: the spec ==============================================================


def test_the_spec_is_the_consumer_api_and_the_base_url_matches_its_server() -> None:
    assert SPEC["openapi"].startswith("3.1")
    assert SPEC["servers"][0]["url"] == BASE_URL


def test_the_api_is_read_only_and_bearer_authenticated() -> None:
    assert {m for item in SPEC["paths"].values() for m in item} == {"get"}
    scheme = SPEC["components"]["securitySchemes"]["ConsumerBearer"]
    assert (scheme["type"], scheme["scheme"]) == ("http", "bearer")


def test_the_seven_paths_are_the_ones_the_package_has_a_function_for() -> None:
    assert sorted(SPEC["paths"]) == sorted(
        [
            "/me/addresses",
            "/me/addresses/{addressId}/consumption",
            "/me/addresses/{addressId}/solar-export",
            "/me/addresses/{addressId}/consumption-costs",
            "/me/addresses/{addressId}/solar-export-revenue",
            "/me/addresses/{addressId}/prices",
            "/me/addresses/{addressId}/forecast-prices",
        ]
    )


def test_the_enums_the_package_offers_are_the_specs() -> None:
    assert tuple(_schema("Aggregation")["enum"]) == package.AGGREGATIONS
    assert tuple(_schema("PriceResolution")["enum"]) == package.PRICE_RESOLUTIONS
    assert tuple(_schema("Currency")["enum"]) == _series._CURRENCIES


class Contract(NamedTuple):
    """An endpoint of the package, its spec row schema and its spec response schema."""

    endpoint: _series._Endpoint
    interval: str
    response: str


ENDPOINTS = [
    pytest.param(
        Contract(usage._CONSUMPTION, "UsageInterval", "UsageResponse"), id="consumption"
    ),
    pytest.param(
        Contract(usage._SOLAR_EXPORT, "UsageInterval", "UsageResponse"),
        id="solar-export",
    ),
    pytest.param(
        Contract(costs._CONSUMPTION_COSTS, "CostInterval", "CostResponse"),
        id="consumption-costs",
    ),
    pytest.param(
        Contract(costs._SOLAR_EXPORT_REVENUE, "RevenueInterval", "RevenueResponse"),
        id="solar-export-revenue",
    ),
    pytest.param(
        Contract(prices._PRICES, "PriceInterval", "PriceResponse"), id="prices"
    ),
    pytest.param(
        Contract(prices._FORECAST, "PriceInterval", "ForecastResponse"), id="forecast"
    ),
]


@pytest.mark.parametrize("contract", ENDPOINTS)
def test_each_endpoint_reads_exactly_the_fields_the_spec_requires(
    contract: Contract,
) -> None:
    endpoint, interval = contract.endpoint, contract.interval
    required = [n for n in _schema(interval)["required"] if n not in ("start", "end")]

    assert [f.source for f in endpoint.fields] == required
    assert [f.column for f in endpoint.fields] == [
        COLUMN_OF.get(n) or _snake(n) for n in required
    ]


@pytest.mark.parametrize("contract", ENDPOINTS)
def test_field_kinds_and_nullability_follow_the_spec_but_may_be_more_lenient(
    contract: Contract,
) -> None:
    endpoint, interval = contract.endpoint, contract.interval
    properties = _schema(interval)["properties"]

    for field in endpoint.fields:
        declared = properties[field.source]["type"]
        kinds = [declared] if isinstance(declared, str) else declared
        assert ("boolean" in kinds) == (field.kind == "bool")
        assert ("number" in kinds) == (field.kind == "float")
        spec_nullable = "null" in kinds
        # The package may accept null where the spec forbids it, never the reverse.
        assert field.nullable or not spec_nullable
        if field.nullable and not spec_nullable:
            # The one deliberate leniency: A1 promises NaN for a null total price.
            assert (interval, field.source) == ("PriceInterval", "totalPricePerKWh")


@pytest.mark.parametrize("contract", ENDPOINTS)
def test_path_interval_parameter_and_response_shape_match_the_spec(
    contract: Contract,
) -> None:
    endpoint = contract.endpoint
    path = f"/me/addresses/{{addressId}}/{endpoint.suffix}"
    query = _query_parameters(path)
    body = _schema(contract.response)

    assert path in SPEC["paths"]
    interval_params = set(query) - {"from", "to"}
    if endpoint.param:
        assert interval_params == {endpoint.param}
        assert (
            tuple(_resolve(query[endpoint.param]["schema"])["enum"])
            == endpoint.intervals
        )
        assert set(query) == {"from", "to", endpoint.param}
    else:
        assert query == {}
    assert all(q["required"] for q in query.values())
    # Money endpoints, prices and the forecast carry currency and a VAT flag.
    assert endpoint.monetary == ("currency" in body["required"])
    assert endpoint.monetary == ("vatIncluded" in body["required"])
    assert endpoint.capability in _schema("Capabilities")["required"]


def test_the_vat_flags_the_package_reports_are_the_specs_constants() -> None:
    assert _schema("CostResponse")["properties"]["vatIncluded"]["const"] is True
    assert _schema("RevenueResponse")["properties"]["vatIncluded"]["const"] is False
    assert (
        _schema("PriceResponse")["properties"]["vatIncluded"].get("const", True) is True
    )
    forecast = _schema("ForecastResponse")["properties"]
    assert forecast["resolution"]["const"] == "hour"
    assert forecast["vatIncluded"]["const"] is True
    assert forecast["isEstimate"]["const"] is True


def test_the_address_columns_are_the_specs_properties_in_the_specs_order() -> None:
    address = [n for n in _schema("Address")["required"] if n != "capabilities"]
    capabilities = _schema("Capabilities")["required"]

    assert list(_series._ADDRESS_COLUMNS) == [_snake(n) for n in address + capabilities]
    assert [source for _, source in _series._CAPABILITY_COLUMNS] == capabilities
    assert _schema("Address")["required"] == [
        "id",
        "label",
        "formattedAddress",
        "region",
        "timeZone",
        "gridOperatorName",
        "supplierName",
        "capabilities",
    ]


def test_the_capability_a_path_needs_is_the_one_the_spec_names_for_it() -> None:
    capabilities = set(_schema("Capabilities")["required"])
    suffixes = {p.rsplit("/", 1)[1] for p in SPEC["paths"] if p != "/me/addresses"}

    assert set(client._CAPABILITIES) == suffixes
    assert set(client._CAPABILITIES.values()) == capabilities
    assert len(set(client._CAPABILITIES.values())) == len(suffixes)


def test_the_error_shape_and_the_unavailable_feature_code_are_the_specs() -> None:
    error = _schema("Error")

    assert error["required"] == ["error", "reason", "errorCode"]
    assert error["properties"]["error"]["const"] is True
    unavailable = SPEC["components"]["responses"]["UnavailableFeature"]
    example = unavailable["content"]["application/json"]["example"]
    assert example["errorCode"] == "feature_unavailable"
    assert "forecast-prices" in client._CAPABILITIES


# --- every function sends a request the spec allows --------------------------------


def _calls() -> dict[str, Callable[[MinStroemClient], pd.DataFrame]]:
    return {
        "get_addresses": lambda c: get_addresses(client=c),
        "get_consumption": lambda c: get_consumption("2026-11-02", client=c),
        "get_solar_export": lambda c: get_solar_export("2026-11-02", client=c),
        "get_consumption_costs": lambda c: get_consumption_costs(
            "2026-11-02", client=c
        ),
        "get_solar_export_revenue": lambda c: get_solar_export_revenue(
            "2026-11-02", client=c
        ),
        "get_prices": lambda c: get_prices("2026-11-02", client=c),
        "get_forecast_prices": lambda c: get_forecast_prices(client=c),
        "get_prices_with_forecast": lambda c: get_prices_with_forecast(
            "2026-11-02", "2026-11-03", client=c
        ),
    }


def _template(path: str) -> str:
    return re.sub(
        r"/me/addresses/[^/]+/", "/me/addresses/{addressId}/", path
    ).removeprefix("/consumer/v1")


@pytest.mark.parametrize("name", sorted(_calls()))
def test_every_request_a_function_makes_is_a_documented_operation(
    monkeypatch: pytest.MonkeyPatch,
    minstroem_service: MinStroemService,
    name: str,
) -> None:
    monkeypatch.setattr(
        prices,
        "_local_midnight",
        lambda days: (
            (pd.Timestamp("2026-11-02") + pd.DateOffset(days=days))
            .tz_localize("Europe/Copenhagen")
            .as_unit("ns")
        ),
    )
    with minstroem_service.client() as c:
        _calls()[name](c)

    assert minstroem_service.requests
    for request in minstroem_service.requests:
        path = _template(request.url.path)
        assert request.method == "GET"
        assert path.removeprefix("/consumer/v1") in SPEC["paths"], path
        spec_params = _query_parameters(path)
        sent = dict(request.url.params)
        assert set(sent) == set(spec_params), path
        for key, value in sent.items():
            schema = _resolve(spec_params[key]["schema"])
            if "enum" in schema:
                assert value in schema["enum"]
        for key in ("from", "to"):
            if key in sent:
                assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", sent[key])
        assert request.headers["Authorization"].startswith("Bearer ")


def test_the_eight_functions_between_them_use_all_seven_documented_paths(
    monkeypatch: pytest.MonkeyPatch, minstroem_service: MinStroemService
) -> None:
    monkeypatch.setattr(
        prices,
        "_local_midnight",
        lambda days: (
            (pd.Timestamp("2026-11-02") + pd.DateOffset(days=days))
            .tz_localize("Europe/Copenhagen")
            .as_unit("ns")
        ),
    )
    with minstroem_service.client() as c:
        for call in _calls().values():
            call(c)

    used = {_template(r.url.path) for r in minstroem_service.requests}
    assert used == set(SPEC["paths"])


# --- the mock serves what the spec describes (T13) ----------------------------------

_GOOD = {"from": "2026-11-01T23:00:00Z", "to": "2026-11-02T23:00:00Z"}


@pytest.mark.parametrize(
    ("suffix", "extra", "schema"),
    [
        ("consumption", {"aggregation": "hour"}, "UsageResponse"),
        ("solar-export", {"aggregation": "day"}, "UsageResponse"),
        ("consumption-costs", {"aggregation": "hour"}, "CostResponse"),
        ("solar-export-revenue", {"aggregation": "hour"}, "RevenueResponse"),
        ("prices", {"resolution": "hour"}, "PriceResponse"),
        ("prices", {"resolution": "fifteenMinutes"}, "PriceResponse"),
        ("forecast-prices", {}, "ForecastResponse"),
    ],
)
def test_the_mock_serves_bodies_that_validate_against_the_spec(
    suffix: str, extra: dict[str, str], schema: str
) -> None:
    service = MinStroemService()
    service.forecast = [
        minstroem_price_row(
            pd.Timestamp("2026-11-02 12:00", tz="Europe/Copenhagen").to_pydatetime(),
            None,
            0.25,
        )
    ]
    params = {} if suffix == "forecast-prices" else {**_GOOD, **extra}
    request = httpx.Request(
        "GET",
        f"{BASE_URL}/me/addresses/{ADDRESS_ID}/{suffix}",
        params=params,
        headers={"Authorization": "Bearer test-key"},
    )

    body = service(request).json()

    assert body["data"], "the check below must see at least one row"
    # The forecast row has a null total on purpose; that is the package's leniency.
    problems = [
        p for p in _check(body, _schema(schema), schema) if "totalPricePerKWh" not in p
    ]
    assert problems == []


def test_the_mock_serves_an_address_list_that_validates_against_the_spec() -> None:
    service = MinStroemService()
    request = httpx.Request(
        "GET", f"{BASE_URL}/me/addresses", headers={"Authorization": "Bearer test-key"}
    )

    assert (
        _check(service(request).json(), _schema("AddressResponse"), "addresses") == []
    )


def test_every_error_the_mock_sends_has_the_specs_shape_and_a_documented_code() -> None:
    service = MinStroemService()
    documented = json.dumps(SPEC)
    errors = [
        service(httpx.Request("GET", f"{BASE_URL}/me/addresses")),
        service(
            httpx.Request(
                "GET",
                f"{BASE_URL}/me/addresses/{ADDRESS_ID}/consumption",
                params={"from": "x", "to": "y", "aggregation": "hour"},
                headers={"Authorization": "Bearer test-key"},
            )
        ),
        service(
            httpx.Request(
                "GET",
                f"{BASE_URL}/me/addresses/{ADDRESS_ID}/consumption",
                params={
                    "from": "2026-01-01T00:00:00Z",
                    "to": "2026-01-01T00:00:00Z",
                    "aggregation": "hour",
                },
                headers={"Authorization": "Bearer test-key"},
            )
        ),
        service(
            httpx.Request(
                "GET",
                f"{BASE_URL}/me/addresses/{ADDRESS_ID}/consumption",
                params={
                    "from": "2020-01-01T00:00:00Z",
                    "to": "2026-01-01T00:00:00Z",
                    "aggregation": "hour",
                },
                headers={"Authorization": "Bearer test-key"},
            )
        ),
        service(
            httpx.Request(
                "GET",
                f"{BASE_URL}/me/addresses/2b4bf7a8-b5d0-45ab-8d42-3a5688bba167/prices",
                headers={"Authorization": "Bearer test-key"},
            )
        ),
    ]

    assert {e.status_code for e in errors} == {400, 401, 404}
    for response in errors:
        body = response.json()
        assert _check(body, _schema("Error"), "error") == []
        assert f'"{body["errorCode"]}"' in documented or body["errorCode"] in documented


# --- the spec's own examples go through the package ---------------------------------


def _example(path: str, name: str) -> dict[str, Any]:
    content = _operation(path)["responses"]["200"]["content"]["application/json"]
    example: dict[str, Any] = content["examples"][name]["value"]
    return example


@pytest.mark.parametrize("example", ["synthetic", "empty"])
@pytest.mark.parametrize(
    ("path", "call"),
    [
        (
            "/me/addresses/{addressId}/consumption",
            lambda c: get_consumption(
                "2026-09-05T12:00", "2026-09-05T13:00", address_id=ADDRESS_ID, client=c
            ),
        ),
        (
            "/me/addresses/{addressId}/solar-export",
            lambda c: get_solar_export(
                "2026-09-05T12:00", "2026-09-05T13:00", address_id=ADDRESS_ID, client=c
            ),
        ),
        (
            "/me/addresses/{addressId}/consumption-costs",
            lambda c: get_consumption_costs(
                "2026-09-05T12:00", "2026-09-05T13:00", address_id=ADDRESS_ID, client=c
            ),
        ),
        (
            "/me/addresses/{addressId}/solar-export-revenue",
            lambda c: get_solar_export_revenue(
                "2026-09-05T12:00", "2026-09-05T13:00", address_id=ADDRESS_ID, client=c
            ),
        ),
        (
            "/me/addresses/{addressId}/prices",
            lambda c: get_prices(
                "2026-09-05T12:00", "2026-09-05T13:00", address_id=ADDRESS_ID, client=c
            ),
        ),
    ],
)
def test_the_specs_series_examples_parse(
    path: str,
    call: Callable[[MinStroemClient], pd.DataFrame],
    example: str,
) -> None:
    body = _example(path, example)
    service = MinStroemService()
    suffix = path.rsplit("/", 1)[1]
    service.overrides[suffix] = lambda _r: httpx.Response(200, json=body)

    with service.client() as c:
        frame = call(c)

    assert len(frame) == 1
    assert frame.index[0] == pd.Timestamp(body["from"]).tz_convert("Europe/Copenhagen")
    if example == "empty":
        assert frame.drop(columns="is_estimate", errors="ignore").isna().all().all()
    else:
        first_row = body["data"][0]
        assert (
            frame.iloc[0, 0]
            == first_row[
                "energyKWh" if "energyKWh" in first_row else "totalPricePerKWh"
            ]
        )
    assert frame.attrs.get("currency", body.get("currency")) == body.get("currency")


@pytest.mark.parametrize("example", ["synthetic", "empty"])
def test_the_specs_forecast_example_parses(example: str) -> None:
    path = "/me/addresses/{addressId}/forecast-prices"
    body = _example(path, example)
    service = MinStroemService()
    service.overrides["forecast-prices"] = lambda _r: httpx.Response(200, json=body)

    with service.client() as c:
        frame = get_forecast_prices(address_id=ADDRESS_ID, client=c)

    assert len(frame) == len(body["data"])
    if example == "synthetic":
        assert frame[frame.columns[0]].iloc[0] == 1.234
        assert pd.isna(frame["variable_charges_per_kwh"].iloc[0])
    assert frame.attrs["currency"] == body["currency"]
    assert frame.attrs["vat_included"] is body["vatIncluded"]


@pytest.mark.parametrize("example", ["synthetic", "empty"])
def test_the_specs_address_example_parses(example: str) -> None:
    body = _example("/me/addresses", example)
    service = MinStroemService()
    service.overrides["addresses"] = lambda _r: httpx.Response(200, json=body)

    with service.client() as c:
        frame = get_addresses(client=c)

    assert len(frame) == len(body["data"])
    if example == "synthetic":
        assert frame.loc[0, "region"] == "DK2"
        assert frame.loc[0, "grid_operator_name"] is None
        assert bool(frame.loc[0, "forecast_prices"]) is True


ERROR_STATUS = {
    "BadRequest": 400,
    "Unauthorized": 401,
    "Forbidden": 403,
    "NotFound": 404,
    "UnavailableFeature": 422,
    "RateLimited": 429,
    "InternalError": 500,
    "UpstreamUnavailable": 502,
    "ServiceUnavailable": 503,
}


def test_every_documented_error_response_is_one_of_the_nine_statuses_tested_below() -> (
    None
):
    assert set(SPEC["components"]["responses"]) == set(ERROR_STATUS)
    for name, status in ERROR_STATUS.items():
        referenced = {
            code
            for item in SPEC["paths"].values()
            for code, response in item["get"]["responses"].items()
            if response.get("$ref", "").endswith(f"/{name}")
        }
        assert referenced == {str(status)}


@pytest.mark.parametrize(("name", "status"), sorted(ERROR_STATUS.items()))
def test_every_documented_error_example_becomes_a_minstroem_error(
    name: str, status: int
) -> None:
    response = SPEC["components"]["responses"][name]
    example = response["content"]["application/json"]["example"]
    service = MinStroemService()
    service.overrides["addresses"] = lambda _r: httpx.Response(status, json=example)

    with service.client() as c, pytest.raises(MinStroemError) as caught:
        c.get_json("/me/addresses")

    error = caught.value
    assert error.status == status
    assert error.error_code == example["errorCode"]
    assert error.reason == example["reason"]
    assert error.request_id is not None
    assert error.request_id.startswith("req-")
    assert example["reason"] in str(error)


# === T10: the live suite's wiring ===============================================


def _pytest(
    args: list[str], *, env: Mapping[str, str] | None = None, cwd: Path = REPO
) -> subprocess.CompletedProcess[str]:
    """Run pytest in a fresh process, never inheriting a Min Strøm key."""
    base = {
        k: v
        for k, v in os.environ.items()
        if k not in (API_KEY_ENV, "MINSTROEM_ADDRESS_ID", "PYTEST_ADDOPTS")
    }
    base.update(PYTHONIOENCODING="utf-8", PYTHONPATH=f"{REPO / 'src'}")
    base.update(env or {})
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args],
        cwd=cwd,
        env=base,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )


FAKE_KEY = {API_KEY_ENV: "fake-key-that-is-never-sent"}


def test_plain_pytest_deselects_the_live_tests_even_with_a_key_set() -> None:
    result = _pytest(
        ["tests/test_minstroem_live.py", "--collect-only", "-q"], env=FAKE_KEY
    )

    assert "no tests collected" in result.stdout
    assert "deselected" in result.stdout
    assert "::test_live" not in result.stdout
    assert result.returncode == 5


def test_dash_m_live_collects_every_live_test() -> None:
    result = _pytest(
        ["tests/test_minstroem_live.py", "--collect-only", "-q", "-m", "live"],
        env=FAKE_KEY,
    )

    collected = [
        line for line in result.stdout.splitlines() if "<Function test_live_" in line
    ]
    assert result.returncode == 0
    assert len(collected) >= 18
    assert "<Module test_minstroem_live.py>" in result.stdout
    assert "deselected" not in result.stdout


def test_dash_m_live_without_a_key_skips_every_test_and_does_not_fail() -> None:
    result = _pytest(["tests/test_minstroem_live.py", "-m", "live", "-rs", "-q"])

    assert result.returncode == 0
    assert "skipped" in result.stdout.splitlines()[-1]
    assert "failed" not in result.stdout.splitlines()[-1]
    assert "error" not in result.stdout.splitlines()[-1].lower()
    assert f"set {API_KEY_ENV} to run the live Min Strøm tests" in result.stdout


def _guard_project(tmp_path: Path, tests: str) -> subprocess.CompletedProcess[str]:
    """Run `tests` in a throwaway project that loads this suite's conftest as a plugin."""
    (tmp_path / "test_probe.py").write_text(tests, encoding="utf-8")
    return _pytest(
        [
            "-p",
            "conftest",
            f"--rootdir={tmp_path}",
            "-o",
            "addopts=",
            "-o",
            "markers=live: probe",
            "-q",
            "test_probe.py",
        ],
        env={"PYTHONPATH": f"{REPO / 'tests'}{os.pathsep}{REPO / 'src'}"},
        cwd=tmp_path,
    )


GUARD_PROBE = """
import httpx
import pytest


@pytest.mark.live
def test_a_live_test_may_build_a_real_transport():
    assert httpx.AsyncHTTPTransport.handle_async_request.__name__ != "refuse_async"
    assert httpx.HTTPTransport.handle_request.__name__ != "refuse_sync"


def test_an_unmarked_test_gets_the_refusing_transports():
    assert httpx.AsyncHTTPTransport.handle_async_request.__name__ == "refuse_async"
    assert httpx.HTTPTransport.handle_request.__name__ == "refuse_sync"


def test_an_unmarked_test_that_reaches_the_network_fails():
    import asyncio

    async def go():
        async with httpx.AsyncClient() as c:
            await c.get("https://example.invalid/")

    with pytest.raises(AssertionError, match="tried to reach the network"):
        asyncio.run(go())
"""


def test_the_no_network_guard_exempts_only_tests_marked_live(tmp_path: Path) -> None:
    result = _guard_project(tmp_path, GUARD_PROBE)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "3 passed" in result.stdout


def test_the_summary_block_appears_only_when_a_test_reported_a_line(
    tmp_path: Path,
) -> None:
    reporting = _guard_project(
        tmp_path,
        "def test_reports(live_report):\n    live_report('count 3, region DK1')\n",
    )
    silent = _guard_project(tmp_path, "def test_quiet():\n    assert True\n")

    assert "MIN STRØM LIVE SUMMARY" in reporting.stdout
    assert "count 3, region DK1" in reporting.stdout
    assert "MIN STRØM LIVE SUMMARY" not in silent.stdout
    assert silent.returncode == 0


def test_the_summary_lists_the_lines_in_the_order_they_were_reported(
    tmp_path: Path,
) -> None:
    result = _guard_project(
        tmp_path,
        "def test_a(live_report):\n    live_report('first line')\n"
        "def test_b(live_report):\n    live_report('second line')\n",
    )

    out = result.stdout
    assert (
        out.index("MIN STRØM LIVE SUMMARY")
        < out.index("first line")
        < out.index("second line")
    )


def _called_names(path: Path) -> dict[str, set[str]]:
    """The names each test function in `path` references."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name: {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    }


def test_the_live_suite_covers_every_function_and_the_forecast_the_most() -> None:
    names = _called_names(REPO / "tests" / "test_minstroem_live.py")

    for function in PUBLIC_FUNCTIONS:
        assert any(function in used for used in names.values()), function
    forecast = [t for t, used in names.items() if "get_forecast_prices" in used]
    combined = [t for t, used in names.items() if "get_prices_with_forecast" in used]
    assert len(forecast) >= 3
    assert len(combined) >= 2
    others = [
        sum(function in used for used in names.values())
        for function in PUBLIC_FUNCTIONS
        if function not in ("get_forecast_prices", "get_prices_with_forecast")
    ]
    assert len(forecast) >= max(others)


def test_every_live_test_is_marked_live_and_skipped_without_a_key() -> None:
    tree = ast.parse(
        (REPO / "tests" / "test_minstroem_live.py").read_text(encoding="utf-8")
    )
    (assignment,) = [
        n
        for n in tree.body
        if isinstance(n, ast.Assign)
        and any(getattr(t, "id", "") == "pytestmark" for t in n.targets)
    ]
    text = ast.unparse(assignment.value)

    assert "pytest.mark.live" in text
    assert "skipif" in text
    assert "API_KEY_ENV" in text


# === the package surface =========================================================


def test_the_package_exports_exactly_the_planned_names() -> None:
    exported = {
        name
        for name, value in vars(package).items()
        if not name.startswith("_") and not isinstance(value, types.ModuleType)
    }

    assert exported == {
        "BASE_URL",
        "API_KEY_ENV",
        "MinStroemClient",
        "MinStroemError",
        "MissingApiKeyError",
        "Aggregation",
        "AGGREGATIONS",
        "PriceResolution",
        "PRICE_RESOLUTIONS",
        "get_addresses",
        "get_consumption",
        "get_solar_export",
        "get_consumption_costs",
        "get_solar_export_revenue",
        "get_prices",
        "get_forecast_prices",
        "get_prices_with_forecast",
    }
    assert len(PUBLIC_FUNCTIONS) == 8


@pytest.mark.parametrize(
    "module", ["client", "_series", "addresses", "usage", "costs", "prices"]
)
def test_every_module_runs_standalone_and_without_a_key_says_so(
    tmp_path: Path, module: str
) -> None:
    result = subprocess.run(
        [sys.executable, "-m", f"energydata.minstroem.{module}"],
        cwd=tmp_path,
        env={
            **{k: v for k, v in os.environ.items() if k != API_KEY_ENV},
            "PYTHONPATH": f"{REPO / 'src'}",
            "PYTHONIOENCODING": "utf-8",
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert f"Set {API_KEY_ENV} to run this showcase" in result.stdout


# === T12: the docs ================================================================


@pytest.mark.parametrize(
    "name",
    [*PUBLIC_FUNCTIONS, "MinStroemClient", "MinStroemError", "MissingApiKeyError"],
)
def test_the_readme_names_every_public_function_and_class(name: str) -> None:
    assert f"`{name}" in README or f"{name}(" in README, name


def test_the_readme_names_the_key_variable_and_the_live_command_exactly() -> None:
    assert API_KEY_ENV in README
    assert LIVE_COMMAND in README.splitlines()


def test_the_readme_names_every_column_the_functions_return() -> None:
    columns = {
        "total_price_per_kwh",
        "variable_charges_per_kwh",
        "is_forecast",
        "energy_kwh",
        "is_estimate",
        "fixed_fees",
        "total_cost",
        "net_revenue",
        "formatted_address",
        "fetched_at",
    }

    for column in columns:
        assert f"`{column}`" in README, column


def test_the_columns_the_readme_names_are_the_ones_the_functions_return(
    minstroem_service: MinStroemService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        prices,
        "_local_midnight",
        lambda days: (
            (pd.Timestamp("2026-11-02") + pd.DateOffset(days=days))
            .tz_localize("Europe/Copenhagen")
            .as_unit("ns")
        ),
    )
    returned: set[str] = set()
    with minstroem_service.client() as c:
        for name, call in _calls().items():
            if name != "get_addresses":
                returned |= {str(col) for col in call(c).columns}

    assert returned == {
        "total_price_per_kwh",
        "variable_charges_per_kwh",
        "is_forecast",
        "energy_kwh",
        "is_estimate",
        "fixed_fees",
        "total_cost",
        "net_revenue",
    }
    assert all(f"`{column}`" in README for column in returned)


def test_structure_md_names_every_module_and_test_file_of_the_subpackage() -> None:
    modules = sorted(p.relative_to(REPO).as_posix() for p in PACKAGE_DIR.glob("*.py"))
    tests = sorted(
        p.relative_to(REPO).as_posix()
        for p in (REPO / "tests").glob("test_minstroem_*.py")
    )

    assert len(modules) == 7
    assert len(tests) == 8
    for path in [*modules, *tests, "tests/fixtures/minstroem_openapi.json"]:
        assert path in STRUCTURE, path


def test_the_minstroem_rules_file_names_every_public_function_and_module() -> None:
    for name in [
        *PUBLIC_FUNCTIONS,
        "MinStroemClient",
        "MinStroemError",
        "MissingApiKeyError",
    ]:
        assert name in RULES, name
    for module in sorted(p.name for p in PACKAGE_DIR.glob("*.py")):
        assert module in RULES, module
    assert "tests/test_minstroem_spec.py" in RULES
    assert "test_minstroem_live.py" in RULES


def test_the_stop_gates_structure_check_reports_nothing_for_this_subpackage() -> None:
    import stop_gate

    problems = stop_gate.structure_problems(REPO)

    assert [p for p in problems if "minstroem" in p] == []


def test_the_pyproject_registers_the_live_marker_and_deselects_it_by_default() -> None:
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")

    assert (
        'markers = ["live: calls the real Min Strøm API; needs MINSTROEM_API_KEY"]'
        in text
    )
    assert "addopts = \"-v -m 'not live'\"" in text
