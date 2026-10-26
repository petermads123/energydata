"""The package against its outside promises: the OpenAPI spec, its docs, the live wiring."""

import ast
import inspect
import json
import os
import re
import subprocess
import sys
import types
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, NamedTuple
from zoneinfo import ZoneInfo

import conftest
import httpx
import minstroem_live_mock as live_mock
import pandas as pd
import pytest
import test_minstroem_live as live_suite
from conftest import ADDRESS_ID, MinStroemService, minstroem_price_row
from minstroem_live_mock import (
    NOW_ENV,
    SCENARIO_ENV,
    SCENARIOS,
    SECOND_ADDRESS_ID,
    build_service,
)

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
from energydata.utils import RetriesExhaustedError, RetryPolicy

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
    # A retried status ends on the last of its requests.
    assert error.request_id == conftest.minstroem_request_id(len(service.requests))
    assert example["reason"] in str(error)


# === T10: the live suite's wiring ===============================================


def _pytest(
    args: list[str], *, env: Mapping[str, str] | None = None, cwd: Path = REPO
) -> subprocess.CompletedProcess[str]:
    """Run pytest in a fresh process, inheriting nothing of a Min Strøm setup.

    A key, an address, the plugin's scenario and clock, and `PYTEST_ADDOPTS` a
    developer exported in the shell for a hand run must not reach a probe that
    expects them unset.
    """
    base = {
        k: v
        for k, v in os.environ.items()
        if k
        not in (
            API_KEY_ENV,
            "MINSTROEM_ADDRESS_ID",
            "PYTEST_ADDOPTS",
            SCENARIO_ENV,
            NOW_ENV,
        )
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
UUID_TEXT = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


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


def _guard_project(
    tmp_path: Path,
    tests: str,
    *,
    plugins: tuple[str, ...] = (),
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run `tests` in a throwaway project that loads this suite's conftest as a plugin."""
    (tmp_path / "test_probe.py").write_text(tests, encoding="utf-8")
    return _pytest(
        [
            "-p",
            "conftest",
            *[arg for plugin in plugins for arg in ("-p", plugin)],
            f"--rootdir={tmp_path}",
            "-o",
            "addopts=",
            "-o",
            "markers=live: probe",
            "-q",
            "test_probe.py",
        ],
        env={
            "PYTHONPATH": f"{REPO / 'tests'}{os.pathsep}{REPO / 'src'}",
            **(env or {}),
        },
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


# --- the live suite, run offline against the mock ------------------------------
#
# The live tests are only ever run on the user's machine, so a defect in the tests
# themselves (not in the package) would first show there. `tests/minstroem_live_mock.py`
# is a plugin that routes every MinStroemClient to the in-repo mock, shaped by a
# scenario; these tests run the whole live suite under each scenario.

LIVE_PLUGIN = "minstroem_live_mock"
DUMMY_KEY = "dummy-key-for-the-offline-live-run"
# Every subprocess run pins the plugin's clock, so the data the mock is laid out around
# and the "today" the package and the live tests read are one instant: a run that
# straddles an hour or local midnight cannot meet data laid out for another day.
PINNED_NOW = "2026-06-15T12:00:00+02:00"
LIVE_TEST_COUNT = 21  # 18 functions, consumption once per aggregation

NEEDS_PRICES = {
    "test_live_prices_with_forecast_open_end",
    "test_live_prices_with_forecast_closed_end",
    "test_live_combined_series_is_confirmed_where_confirmed_and_forecast_after",
    "test_live_prices_hour",
    "test_live_prices_fifteen_minutes",
    "test_live_prices_future_to_probe",
    "test_live_listed_address_id_works_as_listed",
}
NEEDS_FORECAST = {
    "test_live_forecast_prices",
    "test_live_prices_with_forecast_open_end",
    "test_live_prices_with_forecast_closed_end",
    "test_live_combined_series_is_confirmed_where_confirmed_and_forecast_after",
    "test_live_forecast_hours_are_whole_local_hours_and_unique",
}

# Every test that needs an address: all but the shape of the listing, the wrong key,
# the unknown address and the address default, which are about the account itself.
NEEDS_ADDRESS = (
    NEEDS_PRICES
    | NEEDS_FORECAST
    | {
        "test_live_consumption[hour]",
        "test_live_consumption[day]",
        "test_live_consumption[month]",
        "test_live_consumption[year]",
        "test_live_solar_export",
        "test_live_consumption_costs",
        "test_live_solar_export_revenue",
        "test_live_hourly_consumption_over_two_windows",
    }
)

# Hours of the open-ended combined series with neither a confirmed nor a forecast
# row, as (least, most): <NA> in `is_forecast`, never filled from the other source.
EXPECTED_NEITHER: dict[str, tuple[int, int]] = {
    "plain": (0, 0),  # tidy data: every hour has a source
    "hole": (1, 10**6),  # a confirmed hour missing between confirmed hours
    "gappy_forecast": (2, 10**6),  # a forecast that starts after a gap and has holes
    "empty_forecast": (1, 10**6),  # no forecast rows: runs on to the confirmed cap
    "two_addresses": (0, 0),
}


class _LiveRun(NamedTuple):
    result: subprocess.CompletedProcess[str]
    outcomes: dict[str, str]  # test name -> passed, failure, error or skipped


def _run_live_suite(
    scenario: str,
    tmp_path: Path,
    now: str = PINNED_NOW,
    *,
    tb: str = "short",
    env: Mapping[str, str] | None = None,
) -> _LiveRun:
    """Run the whole live suite against the mock for `scenario` at the instant `now`.

    `tb` is pytest's traceback style and `env` what the user's shell would add
    (a different key, `MINSTROEM_ADDRESS_ID`).
    """
    report = tmp_path / "live.xml"
    result = _pytest(
        [
            "-p",
            LIVE_PLUGIN,
            "tests/test_minstroem_live.py",
            "-m",
            "live",
            "-rA",
            f"--tb={tb}",
            f"--junitxml={report}",
        ],
        env={
            API_KEY_ENV: DUMMY_KEY,
            SCENARIO_ENV: scenario,
            NOW_ENV: now,
            "PYTHONPATH": f"{REPO / 'tests'}{os.pathsep}{REPO / 'src'}",
            **(env or {}),
        },
    )
    assert report.exists(), result.stdout + result.stderr
    outcomes: dict[str, str] = {}
    for case in ET.parse(report).getroot().iter("testcase"):
        kinds = [c.tag for c in case if c.tag in ("failure", "error", "skipped")]
        outcomes[case.get("name", "")] = kinds[0] if kinds else "passed"
    return _LiveRun(result, outcomes)


def _neither(output: str) -> int:
    """The hours the open-ended combined series reported as neither confirmed nor forecast."""
    found = re.search(r"combined \(today\.\.\):.* neither (\d+)", output)
    assert found, output
    return int(found.group(1))


# (scenario, the tests it skips, a report line proving the scenario's branch ran)
PASSING_SCENARIOS: list[tuple[str, set[str], str]] = [
    ("plain", set(), "address default: one address, forecast 48 hours"),
    ("hole", set(), "combined cross-check:"),
    ("gappy_forecast", set(), "forecast hours:"),
    ("empty_forecast", set(), "forecast: EMPTY"),
    ("two_addresses", set(), "2 addresses, ValueError as designed"),
    ("no_prices", NEEDS_PRICES, "forecast:"),
    (
        "no_forecast",
        NEEDS_FORECAST,
        "address default: one address without forecast capability",
    ),
    ("no_addresses", NEEDS_ADDRESS, "address default: 0 addresses, ValueError"),
]
FAILING_SCENARIO = "prices_not_found"


@pytest.mark.parametrize(
    ("scenario", "skipped", "shows"),
    PASSING_SCENARIOS,
    ids=[scenario for scenario, _skipped, _shows in PASSING_SCENARIOS],
)
def test_every_live_test_passes_against_the_mock_in_every_shape_of_data(
    tmp_path: Path, scenario: str, skipped: set[str], shows: str
) -> None:
    run = _run_live_suite(scenario, tmp_path)

    output = run.result.stdout
    assert run.result.returncode == 0, output
    assert len(run.outcomes) == LIVE_TEST_COUNT, output
    assert {n for n, o in run.outcomes.items() if o == "skipped"} == skipped
    assert {o for n, o in run.outcomes.items() if n not in skipped} == {"passed"}
    assert "MIN STRØM LIVE SUMMARY" in output
    assert shows in output
    if scenario in EXPECTED_NEITHER:
        least, most = EXPECTED_NEITHER[scenario]
        assert least <= _neither(output) <= most, output
    # What the suite reports never includes the key, an address id or address text.
    for private in (DUMMY_KEY, ADDRESS_ID, SECOND_ADDRESS_ID, "Testvej", "Test Net"):
        assert private not in output


def test_a_failed_live_request_reports_its_facts_but_never_the_address_id(
    tmp_path: Path,
) -> None:
    run = _run_live_suite(FAILING_SCENARIO, tmp_path)

    output = run.result.stdout
    failed = {n for n, o in run.outcomes.items() if o == "failure"}
    assert run.result.returncode == 1, output
    # The probe catches the refusal and reports it; the rest of the price tests fail.
    assert failed == NEEDS_PRICES - {"test_live_prices_future_to_probe"}
    assert "PROBE /prices with to=+3 days: REFUSED, HTTP 404" in output
    # Status, error code, reason and request id survive; the id does not, anywhere
    # (not in the message, the chained httpx error or the assertion's own output).
    for kept in (
        "HTTP 404 resource_not_found on /me/addresses/<address>/prices",
        "status=404",
        "error_code=resource_not_found",
        "reason='No prices for address <address>.'",
    ):
        assert kept in output, kept
    # The request id is a UUID too (the spec's header is), and it is the one fact
    # support asks for: it must not be erased as if it were the address.
    request_ids = re.findall(r"request_id=([^\s\]]+)", output)
    assert request_ids, output
    assert all(re.fullmatch(UUID_TEXT, found) for found in request_ids), request_ids
    assert re.search(rf"\(request id {UUID_TEXT}\)", output)
    assert "request id <address>" not in output
    assert ADDRESS_ID not in output.lower()
    assert DUMMY_KEY not in output
    assert "direct cause" not in output
    assert "During handling" not in output


def test_every_scenario_of_the_mock_is_run_by_a_guard() -> None:
    guarded = {scenario for scenario, _skipped, _shows in PASSING_SCENARIOS}

    assert guarded | {FAILING_SCENARIO} == set(SCENARIOS)
    assert FAILING_SCENARIO not in guarded


def test_an_unknown_scenario_is_a_value_error_naming_it() -> None:
    now = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="'sunny'"):
        build_service("sunny", now, "test-key")


@pytest.mark.parametrize(
    "scenario", ["plain", "hole", "gappy_forecast", "two_addresses"]
)
@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 3, 29, 0, 30, tzinfo=UTC),
        datetime(2026, 10, 25, 22, 30, tzinfo=UTC),
        datetime(2026, 10, 26, 12, 0, tzinfo=UTC),
    ],
    ids=["spring-change-day", "autumn-change-day", "ordinary-day"],
)
def test_each_scenario_makes_the_documented_combined_series_on_any_day(
    scenario: str, now: datetime
) -> None:
    """The scenarios are laid out from the clock; DST days must not bend them."""
    service = build_service(scenario, now, "test-key")
    today = now.astimezone(ZoneInfo("Europe/Copenhagen")).date()
    address_id = service.addresses[-1]["id"]

    with service.client() as client:
        combined = get_prices_with_forecast(today, address_id=address_id, client=client)

    flags = combined["is_forecast"]
    least, most = EXPECTED_NEITHER[scenario]
    assert combined.index[0] == pd.Timestamp(today, tz="Europe/Copenhagen")
    assert (combined.index.to_series().diff().dropna() == pd.Timedelta(hours=1)).all()
    assert least <= int(flags.isna().sum()) <= most
    assert flags.eq(True).sum() > 0
    # Confirmed never follows forecast, whatever the holes.
    hours = flags.index
    assert (
        hours[flags.eq(False).fillna(False).to_numpy(dtype=bool)].max()
        < hours[flags.eq(True).fillna(False).to_numpy(dtype=bool)].min()
    )


def test_the_mocked_live_run_still_refuses_the_real_network_and_routes_clients(
    tmp_path: Path,
) -> None:
    result = _guard_project(
        tmp_path,
        """
import asyncio

import httpx
import pytest

from energydata.minstroem import MinStroemClient, get_addresses


@pytest.mark.live
def test_a_real_transport_is_refused():
    async def go():
        async with httpx.AsyncClient() as c:
            await c.get("https://example.invalid/")

    with pytest.raises(AssertionError, match="tried to reach the network"):
        asyncio.run(go())


@pytest.mark.live
def test_a_client_made_with_no_transport_is_answered_by_the_mock():
    with MinStroemClient() as client:
        assert len(get_addresses(client=client)) == 1
""",
        plugins=(LIVE_PLUGIN,),
        env={API_KEY_ENV: DUMMY_KEY, "MINSTROEM_LIVE_MOCK": "plain"},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout


# (scenario, the instant the clock is pinned to): the days the live tests' date
# arithmetic has to survive, and local midnight, which used to be a race.
PINNED_INSTANTS = [
    pytest.param("plain", "2026-03-29T12:00:00+02:00", id="spring-change-day"),
    pytest.param("plain", "2026-03-30T12:00:00+02:00", id="day-after-spring-change"),
    pytest.param("plain", "2026-10-25T12:00:00+01:00", id="autumn-change-day"),
    pytest.param("plain", "2026-10-26T12:00:00+01:00", id="day-after-autumn-change"),
    pytest.param("hole", "2026-10-27T00:00:00+01:00", id="hole-at-local-midnight"),
    # The autumn change repeats 02:00-03:00 local: the first pass is +02:00, the
    # second +01:00. `floor("h")` on a Copenhagen time raises in both.
    pytest.param("plain", "2026-10-25T02:30:00+02:00", id="repeated-hour-first"),
    pytest.param("plain", "2026-10-25T02:30:00+01:00", id="repeated-hour-second"),
    pytest.param("plain", "2026-10-25T02:00:00+01:00", id="repeated-hour-on-the-hour"),
    # Local [00:00, 01:00): the forecast used to start where the confirmed prices stop.
    pytest.param("gappy_forecast", "2026-06-15T00:00:00+02:00", id="gappy-at-midnight"),
    pytest.param("gappy_forecast", "2026-06-15T00:59:00+02:00", id="gappy-before-one"),
]


@pytest.mark.parametrize(("scenario", "now"), PINNED_INSTANTS)
def test_every_live_test_passes_whichever_instant_the_clock_is_pinned_to(
    tmp_path: Path, scenario: str, now: str
) -> None:
    run = _run_live_suite(scenario, tmp_path, now)

    output = run.result.stdout
    assert run.result.returncode == 0, output
    assert len(run.outcomes) == LIVE_TEST_COUNT, output
    assert {o for o in run.outcomes.values()} == {"passed"}, output
    least, most = EXPECTED_NEITHER[scenario]
    assert least <= _neither(output) <= most, output


CLOCK_PROBE = """
import pandas as pd
import pytest

from energydata.minstroem import get_forecast_prices

PINNED = pd.Timestamp("2026-06-15T12:00:00+02:00")


@pytest.mark.live
def test_the_clock_is_the_pinned_instant_in_any_zone():
    assert pd.Timestamp.now(tz="Europe/Copenhagen") == PINNED
    assert pd.Timestamp.now(tz="UTC") == PINNED
    assert str(pd.Timestamp.now(tz="Europe/Copenhagen").tz) == "Europe/Copenhagen"
    assert pd.Timestamp.now().tzinfo is None


@pytest.mark.live
def test_the_package_reads_the_pinned_clock_and_the_data_is_laid_out_around_it():
    forecast = get_forecast_prices()

    assert forecast.attrs["fetched_at"] == PINNED
    assert forecast.index[0] == PINNED.floor("h")
"""

REAL_CLOCK_PROBE = """
from datetime import UTC, datetime

import pandas as pd
import pytest


@pytest.mark.live
def test_the_clock_is_the_real_one():
    real = datetime.now(UTC)

    assert abs(pd.Timestamp.now(tz="UTC") - pd.Timestamp(real)) < pd.Timedelta("1min")
    assert pd.Timestamp.now(tz="UTC") != pd.Timestamp("2026-06-15T12:00:00+02:00")
"""


def test_the_plugin_pins_the_clock_for_the_package_and_the_tests(
    tmp_path: Path,
) -> None:
    result = _guard_project(
        tmp_path,
        CLOCK_PROBE,
        plugins=(LIVE_PLUGIN,),
        env={API_KEY_ENV: DUMMY_KEY, NOW_ENV: PINNED_NOW},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout


@pytest.mark.parametrize("env", [{}, {NOW_ENV: ""}], ids=["unset", "empty"])
def test_without_a_pinned_instant_the_plugin_leaves_the_real_clock(
    tmp_path: Path, env: dict[str, str]
) -> None:
    result = _guard_project(
        tmp_path,
        REAL_CLOCK_PROBE,
        plugins=(LIVE_PLUGIN,),
        env={API_KEY_ENV: DUMMY_KEY, **env},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


@pytest.mark.parametrize(
    "raw",
    ["2026-06-15T12:00:00", "2026-06-15 12:00", "noon", " "],
    ids=["no-offset", "no-offset-with-space", "not-a-date", "blank"],
)
def test_a_pinned_instant_without_an_offset_or_not_a_date_is_a_usage_error(
    tmp_path: Path, raw: str
) -> None:
    result = _guard_project(
        tmp_path,
        REAL_CLOCK_PROBE,
        plugins=(LIVE_PLUGIN,),
        env={API_KEY_ENV: DUMMY_KEY, NOW_ENV: raw},
    )

    assert result.returncode != 0
    assert NOW_ENV in result.stderr
    assert repr(raw) in result.stderr
    assert "passed" not in result.stdout


def test_every_live_test_is_wrapped_to_keep_the_address_id_out_of_failures() -> None:
    tree = ast.parse(
        (REPO / "tests" / "test_minstroem_live.py").read_text(encoding="utf-8")
    )
    tests = [
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name.startswith("test_live_")
    ]

    assert len(tests) == 18
    for node in tests:
        names = {ast.unparse(d) for d in node.decorator_list}
        assert "_anonymous_failures" in names, node.name


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
    assert len(forecast) >= 4
    assert len(combined) >= 3
    others = [
        sum(function in used for used in names.values())
        for function in PUBLIC_FUNCTIONS
        if function not in ("get_forecast_prices", "get_prices_with_forecast")
    ]
    # The primary goal gets the most live tests: more than any other function.
    assert len(set(forecast) | set(combined)) > max(others)


def test_the_live_suite_steps_days_on_the_calendar_never_in_elapsed_time() -> None:
    # `midnight - Timedelta(days=7)` is 01:00 after a DST change, which a day
    # interval refuses; the live run would fail twice a year for no good reason.
    text = (REPO / "tests" / "test_minstroem_live.py").read_text(encoding="utf-8")

    assert "Timedelta(days" not in text
    assert "DateOffset(days=offset)" in text


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


# === the live suite's offline guard, second pass ================================
#
# The first pass (above) runs the whole live suite under every shape of data. This
# one pins what reading the suite the way the user's run meets it turned up: the
# clock in the repeated autumn hour, the key and the environment the plugin reads,
# the failure report, the address the user picks, and the shape of the cross-check.

ONE_ADDRESS_PROBE = """
import pytest

from energydata.minstroem import get_addresses


@pytest.mark.live
def test_the_default_scenario_has_one_address():
    assert len(get_addresses()) == 1
"""

TRIVIAL_PROBE = """
import pytest


@pytest.mark.live
def test_nothing():
    pass
"""

PLUGIN_ENV = {API_KEY_ENV: DUMMY_KEY, NOW_ENV: PINNED_NOW}


@pytest.mark.parametrize(
    "key",
    ["  dummy-key  ", "dummy-key\n", "\tdummy-key"],
    ids=["spaces", "newline", "tab"],
)
def test_the_plugin_accepts_a_padded_key_the_way_the_client_strips_it(
    tmp_path: Path, key: str
) -> None:
    result = _guard_project(
        tmp_path,
        ONE_ADDRESS_PROBE,
        plugins=(LIVE_PLUGIN,),
        env={**PLUGIN_ENV, API_KEY_ENV: key},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


@pytest.mark.parametrize(
    "key", [None, "", "   ", "\n"], ids=["unset", "empty", "blank", "newline"]
)
def test_the_plugin_refuses_a_missing_or_blank_key(
    tmp_path: Path, key: str | None
) -> None:
    env = (
        {NOW_ENV: PINNED_NOW}
        if key is None
        else {NOW_ENV: PINNED_NOW, API_KEY_ENV: key}
    )

    result = _guard_project(tmp_path, TRIVIAL_PROBE, plugins=(LIVE_PLUGIN,), env=env)

    assert result.returncode == pytest.ExitCode.USAGE_ERROR, result.stdout
    assert f"minstroem_live_mock needs a dummy {API_KEY_ENV}" in result.stderr
    assert "passed" not in result.stdout


@pytest.mark.parametrize(
    "raw",
    ["sunny", "Plain", " plain", "plain "],
    ids=["unknown", "capitalised", "leading-space", "trailing-space"],
)
def test_a_scenario_that_does_not_exist_is_a_usage_error_naming_the_variable(
    tmp_path: Path, raw: str
) -> None:
    result = _guard_project(
        tmp_path,
        TRIVIAL_PROBE,
        plugins=(LIVE_PLUGIN,),
        env={**PLUGIN_ENV, SCENARIO_ENV: raw},
    )

    assert result.returncode == pytest.ExitCode.USAGE_ERROR, result.stdout
    assert SCENARIO_ENV in result.stderr
    assert repr(raw) in result.stderr
    assert "INTERNALERROR" not in result.stdout + result.stderr


def test_an_empty_scenario_variable_means_the_default_scenario(
    tmp_path: Path,
) -> None:
    result = _guard_project(
        tmp_path,
        ONE_ADDRESS_PROBE,
        plugins=(LIVE_PLUGIN,),
        env={**PLUGIN_ENV, SCENARIO_ENV: ""},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_probes_do_not_inherit_plugin_variables_left_in_the_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A developer who exported both for a hand run, as the docs say, must still see
    # the probes that expect them unset behave as if they were.
    monkeypatch.setenv(NOW_ENV, "2001-01-01T00:00:00+00:00")
    monkeypatch.setenv(SCENARIO_ENV, "two_addresses")

    real_clock = _guard_project(
        tmp_path,
        REAL_CLOCK_PROBE,
        plugins=(LIVE_PLUGIN,),
        env={API_KEY_ENV: DUMMY_KEY},
    )
    default_data = _guard_project(
        tmp_path,
        ONE_ADDRESS_PROBE,
        plugins=(LIVE_PLUGIN,),
        env={API_KEY_ENV: DUMMY_KEY},
    )

    assert real_clock.returncode == 0, real_clock.stdout + real_clock.stderr
    assert default_data.returncode == 0, default_data.stdout + default_data.stderr


def test_a_pinned_instant_the_data_cannot_be_laid_out_around_is_a_usage_error(
    tmp_path: Path,
) -> None:
    raw = "9999-12-31T23:00:00+00:00"

    result = _guard_project(
        tmp_path,
        TRIVIAL_PROBE,
        plugins=(LIVE_PLUGIN,),
        env={API_KEY_ENV: DUMMY_KEY, NOW_ENV: raw},
    )

    assert result.returncode == pytest.ExitCode.USAGE_ERROR, result.stdout
    assert NOW_ENV in result.stderr
    assert repr(raw) in result.stderr
    assert "INTERNALERROR" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-06-15T10:00:00Z", datetime(2026, 6, 15, 10, tzinfo=UTC)),
        ("2026-06-15T15:45:00+05:45", datetime(2026, 6, 15, 10, tzinfo=UTC)),
        ("2026-06-15T12:00:00+02:00", datetime(2026, 6, 15, 10, tzinfo=UTC)),
        (
            "2026-06-15T12:00:00.25+02:00",
            datetime(2026, 6, 15, 10, 0, 0, 250000, tzinfo=UTC),
        ),
    ],
    ids=["z", "odd-offset", "copenhagen-summer", "fraction"],
)
def test_a_pinned_instant_is_read_in_any_offset_form_and_becomes_utc(
    raw: str, expected: datetime
) -> None:
    pinned = live_mock._pinned_now(raw)

    assert pinned == expected
    assert pinned is not None
    assert pinned.utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    "raw", ["2026-06-15", "2026-06-15T12:00:00+02:00\n"], ids=["bare-date", "newline"]
)
def test_a_pinned_instant_that_is_a_bare_date_or_has_trailing_text_is_refused(
    raw: str,
) -> None:
    with pytest.raises(pytest.UsageError, match=NOW_ENV):
        live_mock._pinned_now(raw)


@pytest.mark.parametrize("cycles", [1, 2])
def test_unconfiguring_the_plugin_restores_the_client_the_clock_and_the_transports(
    monkeypatch: pytest.MonkeyPatch, cycles: int
) -> None:
    monkeypatch.setenv(API_KEY_ENV, DUMMY_KEY)
    monkeypatch.setenv(NOW_ENV, PINNED_NOW)
    monkeypatch.delenv(SCENARIO_ENV, raising=False)
    init = MinStroemClient.__dict__["__init__"]
    clock = pd.Timestamp.__dict__["now"]
    asynchronous = httpx.AsyncHTTPTransport.__dict__["handle_async_request"]
    synchronous = httpx.HTTPTransport.__dict__["handle_request"]

    for _ in range(cycles):
        live_mock.pytest_configure()
        try:
            assert pd.Timestamp.now(tz="UTC") == pd.Timestamp("2026-06-15T10:00:00Z")
            with MinStroemClient() as client:
                assert len(get_addresses(client=client)) == 1
        finally:
            live_mock.pytest_unconfigure()

    assert MinStroemClient.__dict__["__init__"] is init
    assert pd.Timestamp.__dict__["now"] is clock
    assert httpx.AsyncHTTPTransport.__dict__["handle_async_request"] is asynchronous
    assert httpx.HTTPTransport.__dict__["handle_request"] is synchronous
    real = pd.Timestamp(datetime.now(UTC))
    assert abs(pd.Timestamp.now(tz="UTC") - real) < pd.Timedelta("1min")


@pytest.mark.parametrize(
    "local", ["00:00", "00:30", "00:59", "01:00", "12:00", "23:30"]
)
def test_the_gappy_forecast_starts_after_a_gap_at_any_time_of_day(local: str) -> None:
    # Confirmed prices used to stop at a fixed 02:00 while the forecast started two
    # hours after the current hour, so in local [00:00, 01:00) the gap vanished.
    now = datetime.fromisoformat(f"2026-06-15T{local}:00+02:00")

    service = build_service("gappy_forecast", now, "test-key")

    confirmed_ends = max(row["end"] for row in service.rows["prices"])
    forecast_starts = min(row["start"] for row in service.forecast)
    # The API's time text sorts in time order.
    assert forecast_starts > confirmed_ends


@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 6, 15, 12, 15, tzinfo=timezone(timedelta(hours=5, minutes=30))),
        datetime(2026, 6, 15, 9, 59, 59, 999999, tzinfo=UTC),
    ],
    ids=["half-hour-offset", "last-microsecond-of-the-hour"],
)
def test_the_forecast_is_laid_out_on_utc_hours_whatever_the_offset_of_now(
    now: datetime,
) -> None:
    service = build_service("plain", now, "test-key")

    assert len(service.forecast) == 48
    for row in [*service.rows["prices"], *service.forecast]:
        assert row["start"].endswith(":00:00Z"), row
    first = service.forecast[0]["start"]
    assert first == now.astimezone(UTC).strftime("%Y-%m-%dT%H:00:00Z")


MODULE_FIXTURE_PROBE = """
import httpx
import pytest


@pytest.fixture(scope="module")
def transport_while_the_module_is_set_up():
    return httpx.AsyncHTTPTransport.handle_async_request.__name__


@pytest.mark.live
def test_a_module_scoped_fixture_sees_the_transports_already_refusing(
    transport_while_the_module_is_set_up,
):
    assert transport_while_the_module_is_set_up == "refuse_async"
"""


def test_a_module_scoped_fixture_is_set_up_with_the_real_transports_already_refusing(
    tmp_path: Path,
) -> None:
    # The live suite's `address` fixture is module-scoped and makes a request;
    # pytest sets it up before any function-scoped autouse fixture has run.
    result = _guard_project(
        tmp_path, MODULE_FIXTURE_PROBE, plugins=(LIVE_PLUGIN,), env=PLUGIN_ENV
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


ROUTING_PROBE = """
import httpx
import pytest

from energydata.minstroem import MinStroemClient, get_addresses


@pytest.mark.live
def test_a_client_given_a_real_transport_is_answered_by_the_mock():
    with MinStroemClient(transport=httpx.AsyncHTTPTransport()) as client:
        assert len(get_addresses(client=client)) == 1


@pytest.mark.live
def test_a_synchronous_httpx_client_is_refused_too():
    with pytest.raises(AssertionError, match="tried to reach the network"):
        httpx.Client().get("https://example.invalid/")
"""


def test_the_plugin_routes_a_client_given_a_real_transport_and_refuses_sync_httpx(
    tmp_path: Path,
) -> None:
    result = _guard_project(
        tmp_path, ROUTING_PROBE, plugins=(LIVE_PLUGIN,), env=PLUGIN_ENV
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout


BLOCK_PROBE = r"""
import httpx
import pytest


def test_the_synchronous_transport_refuses_and_names_the_url():
    with pytest.raises(
        AssertionError, match=r"tried to reach the network: https://example\.invalid/x\?y=1"
    ):
        httpx.Client().get("https://example.invalid/x?y=1")


def test_a_mock_transport_is_left_alone():
    transport = httpx.MockTransport(lambda request: httpx.Response(204))
    with httpx.Client(transport=transport) as client:
        assert client.get("https://example.invalid/").status_code == 204


@pytest.mark.live
def test_the_block_does_not_outlive_the_tests_it_guarded():
    assert httpx.HTTPTransport.handle_request.__name__ == "handle_request"
    assert (
        httpx.AsyncHTTPTransport.handle_async_request.__name__
        == "handle_async_request"
    )
"""


def test_the_block_names_the_url_spares_mock_transports_and_is_undone_after_each_test(
    tmp_path: Path,
) -> None:
    result = _guard_project(tmp_path, BLOCK_PROBE)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "3 passed" in result.stdout


# --- the live suite, run offline: what the user's own run varies ------------------


def test_every_live_test_passes_with_a_key_that_has_whitespace_around_it(
    tmp_path: Path,
) -> None:
    # The client strips the key it sends; the plugin, the module's skip condition
    # and the wrong-key test must read it the same way.
    run = _run_live_suite("plain", tmp_path, env={API_KEY_ENV: f"  {DUMMY_KEY}\n"})

    output = run.result.stdout
    assert run.result.returncode == 0, output
    assert {o for o in run.outcomes.values()} == {"passed"}, output
    assert DUMMY_KEY not in output


@pytest.mark.parametrize(
    "key", ["", " ", "\t\n"], ids=["empty", "space", "tab-newline"]
)
def test_dash_m_live_with_a_blank_key_skips_every_test_like_no_key(key: str) -> None:
    result = _pytest(
        ["tests/test_minstroem_live.py", "-m", "live", "-rs", "-q"],
        env={API_KEY_ENV: key},
    )

    last = result.stdout.splitlines()[-1]
    assert result.returncode == 0, result.stdout
    assert "skipped" in last
    assert "failed" not in last
    assert "error" not in last.lower()


ADDRESS_CHOICES = [
    pytest.param(
        "two_addresses", ADDRESS_ID, NEEDS_FORECAST, id="first-address-cannot-forecast"
    ),
    pytest.param("two_addresses", SECOND_ADDRESS_ID, set(), id="second-address"),
    pytest.param("two_addresses", SECOND_ADDRESS_ID.upper(), set(), id="upper-case-id"),
    pytest.param("two_addresses", f"  {SECOND_ADDRESS_ID}\n", set(), id="padded-id"),
    pytest.param(
        "plain",
        "00000000-0000-4000-8000-000000000000",
        NEEDS_ADDRESS,
        id="not-an-address-of-the-account",
    ),
]


@pytest.mark.parametrize(("scenario", "chosen", "skipped"), ADDRESS_CHOICES)
def test_the_address_named_by_minstroem_address_id_is_the_one_under_test(
    tmp_path: Path, scenario: str, chosen: str, skipped: set[str]
) -> None:
    run = _run_live_suite(scenario, tmp_path, env={"MINSTROEM_ADDRESS_ID": chosen})

    output = run.result.stdout
    assert run.result.returncode == 0, output
    assert len(run.outcomes) == LIVE_TEST_COUNT, output
    assert {n for n, o in run.outcomes.items() if o == "skipped"} == skipped, output
    assert {o for n, o in run.outcomes.items() if n not in skipped} == {"passed"}
    if skipped == NEEDS_ADDRESS:
        assert "MINSTROEM_ADDRESS_ID is not an address of this account" in output
    for private in (DUMMY_KEY, ADDRESS_ID, SECOND_ADDRESS_ID, "Testvej", "Test Net"):
        assert private.lower() not in output.lower()


@pytest.mark.parametrize("tb", ["short", "auto", "long", "native", "line", "no"])
def test_no_traceback_style_shows_the_key_an_address_id_or_the_address_text(
    tmp_path: Path, tb: str
) -> None:
    # `--tb=long` (and `auto`, for the first and last frame) prints the arguments of
    # every frame in the traceback: the fixture holding the address and the request
    # path in the package's frames. Only `--showlocals` is not covered, and says so.
    run = _run_live_suite(FAILING_SCENARIO, tmp_path, tb=tb)

    output = run.result.stdout + run.result.stderr
    failed = {n for n, o in run.outcomes.items() if o == "failure"}
    assert failed == NEEDS_PRICES - {"test_live_prices_future_to_probe"}, output
    for private in (
        DUMMY_KEY,
        ADDRESS_ID,
        SECOND_ADDRESS_ID,
        "Testvej",
        "Test Net",
    ):
        assert private not in output, (tb, private)
    if tb != "no":
        assert "HTTP 404 resource_not_found on /me/addresses/<address>/prices" in output


def _summary_lines(output: str) -> list[str]:
    """The lines of the `MIN STRØM LIVE SUMMARY` block."""
    lines = output.splitlines()
    first = next(i for i, line in enumerate(lines) if "MIN STRØM LIVE SUMMARY" in line)
    last = next(i for i in range(first + 1, len(lines)) if lines[i].startswith("="))
    return lines[first + 1 : last]


@pytest.mark.parametrize(
    ("scenario", "skipped", "_shows"),
    PASSING_SCENARIOS,
    ids=[scenario for scenario, _skipped, _shows in PASSING_SCENARIOS],
)
def test_the_summary_has_one_line_per_test_that_reported(
    tmp_path: Path, scenario: str, skipped: set[str], _shows: str
) -> None:
    run = _run_live_suite(scenario, tmp_path)

    lines = _summary_lines(run.result.stdout)
    # A test that skips reports nothing; every other test adds exactly one line.
    assert len(lines) == LIVE_TEST_COUNT - len(skipped), lines
    assert all(line.strip() for line in lines)


def test_at_a_pinned_instant_the_summary_is_the_same_every_time(
    tmp_path: Path,
) -> None:
    first = _summary_lines(_run_live_suite("plain", tmp_path).result.stdout)
    second = _summary_lines(_run_live_suite("plain", tmp_path).result.stdout)

    assert first == second
    expected = [
        "forecast: 48 hours 2026-06-15T12:00:00+02:00 .. 2026-06-17T11:00:00+02:00, "
        "region DK1, DKK, first 0.600, last 0.590",
        "combined (today..): 60 hours 2026-06-15T00:00:00+02:00 .. "
        "2026-06-17T11:00:00+02:00, confirmed 36, forecast 24, neither 0",
        "combined (yesterday..+2d): 72 hours, 0 NaN",
        "combined cross-check: 60 confirmed hours match get_prices, "
        "12 forecast hours match get_forecast_prices",
    ]
    for line in expected:
        assert line in first, line


# --- the live suite's helpers, in process -----------------------------------------

REQUEST_ID = "9f1c2d3e-4b5a-4c6d-8e7f-0a1b2c3d4e5f"


def test_a_failure_report_keeps_a_uuid_request_id_and_removes_only_the_address_id() -> (
    None
):
    error = MinStroemError(
        f"Min Strøm API: HTTP 404 resource_not_found on /me/addresses/{ADDRESS_ID}"
        f"/prices: The resource was not found. (request id {REQUEST_ID})",
        status=404,
        error_code="resource_not_found",
        reason="The resource was not found.",
        request_id=REQUEST_ID,
    )

    text = live_suite._describe(error)

    assert f"request_id={REQUEST_ID}" in text
    assert f"(request id {REQUEST_ID})" in text
    assert "/me/addresses/<address>/prices" in text
    assert ADDRESS_ID not in text.lower()


def test_a_failure_report_removes_an_address_id_the_reason_repeats() -> None:
    error = MinStroemError(
        f"Min Strøm API: HTTP 404 on /me/addresses/{ADDRESS_ID}/prices: "
        f"No prices for address {ADDRESS_ID.upper()}. (request id {REQUEST_ID})",
        status=404,
        reason=f"No prices for address {ADDRESS_ID.upper()}.",
        request_id=REQUEST_ID,
    )

    text = live_suite._describe(error)

    assert "reason='No prices for address <address>.'" in text
    assert ADDRESS_ID not in text.lower()
    assert text.count(REQUEST_ID) == 2


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"/me/addresses/{ADDRESS_ID}/prices", "/me/addresses/<address>/prices"),
        (ADDRESS_ID.upper(), "<address>"),
        ("{" + ADDRESS_ID + "}", "{<address>}"),
        (f"{ADDRESS_ID} and {SECOND_ADDRESS_ID}", "<address> and <address>"),
        ("ff" + ADDRESS_ID + "ff", "ff<address>ff"),
        ("5b0ac4d2 only", "5b0ac4d2 only"),
        ("", ""),
    ],
    ids=["path", "upper", "braces", "two", "glued", "partial", "empty"],
)
def test_every_form_of_an_address_id_is_replaced(text: str, expected: str) -> None:
    assert live_suite._anonymous(text) == expected


def test_one_uuid_can_be_kept_whatever_its_case() -> None:
    text = f"{ADDRESS_ID} {REQUEST_ID.upper()} {REQUEST_ID}"

    assert live_suite._anonymous(text, keep=REQUEST_ID) == (
        f"<address> {REQUEST_ID.upper()} {REQUEST_ID}"
    )


def _frames(caught: pytest.ExceptionInfo[BaseException]) -> list[str]:
    return [entry.name for entry in caught.traceback]


def _deeper() -> None:
    cause = httpx.ConnectError(
        f"refused for https://api.minstroem.app/consumer/v1/me/addresses/{ADDRESS_ID}/prices"
    )
    raise RetriesExhaustedError(3, None, cause) from cause


def test_a_retry_error_becomes_an_anonymous_assertion_that_keeps_only_the_test_frame() -> (
    None
):
    def failing() -> None:
        _deeper()

    with pytest.raises(AssertionError) as caught:
        live_suite._anonymous_failures(failing)()

    error = caught.value
    assert str(error).startswith("a request failed: RetriesExhaustedError: ")
    assert "/me/addresses/<address>/prices" in str(error)
    assert ADDRESS_ID not in str(error).lower()
    assert error.__cause__ is None
    assert error.__suppress_context__ is True
    # The test's own frame is the last one shown; the package's frames, whose
    # arguments hold the request path, are not in the report at all.
    assert _frames(caught)[-1] == "failing"
    assert "_deeper" not in _frames(caught)


@pytest.mark.parametrize(
    "exc",
    [
        pytest.skip.Exception("lacks prices"),
        AssertionError("plain"),
        KeyboardInterrupt(),
    ],
    ids=["skip", "assertion", "interrupt"],
)
def test_other_exceptions_pass_through_a_live_test_untouched(
    exc: BaseException,
) -> None:
    def failing() -> None:
        raise exc

    with pytest.raises(type(exc)) as caught:
        live_suite._anonymous_failures(failing)()

    assert caught.value is exc


def test_a_live_test_that_passes_returns_what_it_returned() -> None:
    assert live_suite._anonymous_failures(lambda: 42)() == 42


def test_hours_treats_an_unknown_flag_as_neither_and_keeps_the_zone() -> None:
    index = pd.date_range("2026-06-15", periods=4, freq="h", tz="Europe/Copenhagen")
    flags = pd.Series([False, pd.NA, True, True], index=index, dtype="boolean")
    nothing = pd.Series([pd.NA, pd.NA], index=index[:2], dtype="boolean")
    empty = pd.Series([], index=index[:0], dtype="boolean")

    assert list(live_suite._hours(flags, False)) == [index[0]]
    assert list(live_suite._hours(flags, True)) == list(index[2:])
    assert str(live_suite._hours(flags, True).tz) == "Europe/Copenhagen"
    assert len(live_suite._hours(nothing, True)) == 0
    assert len(live_suite._hours(nothing, False)) == 0
    assert len(live_suite._hours(empty, True)) == 0
    assert str(live_suite._hours(empty, True).tz) == "Europe/Copenhagen"


def test_the_address_and_its_id_hide_themselves_from_a_report_of_arguments() -> None:
    address = live_suite._Address(
        {
            "id": ADDRESS_ID,
            "label": "Home",
            "formatted_address": "Testvej 1, 1000 Testby",
            "prices": True,
        }
    )

    identifier = live_suite._need(address, "prices")

    # pytest prints the arguments of a frame, and `-l` its locals, with `repr`.
    assert repr(address) == "<address>"
    assert repr({"argument": address}) == "{'argument': <address>}"
    assert repr(identifier) == "<private>"
    assert identifier == ADDRESS_ID
    assert f"/me/addresses/{identifier}/prices" == f"/me/addresses/{ADDRESS_ID}/prices"
    assert address["label"] == "Home"


@pytest.fixture
def pinned_clock(monkeypatch: pytest.MonkeyPatch) -> datetime:
    """`pandas.Timestamp.now` fixed to `PINNED_NOW`, as the plugin does for a run."""
    pinned = datetime.fromisoformat(PINNED_NOW).astimezone(UTC)
    monkeypatch.setattr(
        pd.Timestamp, "now", staticmethod(live_mock._fixed_clock(pinned))
    )
    return pinned


def test_the_cross_check_survives_day_ahead_publication_between_its_requests(
    pinned_clock: datetime,
) -> None:
    # "Forecasts follow the latest confirmed price": when tomorrow's prices are
    # published (around 13:00) the confirmed prices grow by a day and the forecast
    # starts a day later. The cross-check makes four requests and may meet that.
    service = build_service("plain", pinned_clock, DUMMY_KEY)
    midnight = datetime(2026, 6, 14, 22, tzinfo=UTC)  # local midnight, 15 June

    def day(offset: int) -> datetime:
        return midnight + timedelta(days=offset)

    service.rows["prices"] = live_mock._hourly(day(-3), day(1))
    service.forecast = live_mock._hourly(day(1), day(3))
    published: list[bool] = []

    def answer(request: httpx.Request) -> httpx.Response:
        response = service(request)
        if request.url.path.endswith("/prices") and not published:
            published.append(True)
            service.rows["prices"] = live_mock._hourly(day(-3), day(2))
            service.forecast = live_mock._hourly(day(2), day(4))
        return response

    address = live_suite._Address(
        {"id": ADDRESS_ID, "forecast_prices": True, "prices": True}
    )
    lines: list[str] = []
    client = MinStroemClient(
        DUMMY_KEY,
        policy=RetryPolicy(base_delay=0.0, max_delay=0.0),
        transport=httpx.MockTransport(answer),
    )

    with client:
        live_suite.test_live_combined_series_is_confirmed_where_confirmed_and_forecast_after(
            client, address, lines.append
        )

    assert published
    assert any("forecast moved during the run" in line for line in lines), lines


def test_a_forecast_that_holds_still_is_compared_value_for_value(
    pinned_clock: datetime,
) -> None:
    service = build_service("plain", pinned_clock, DUMMY_KEY)
    address = live_suite._Address(
        {"id": ADDRESS_ID, "forecast_prices": True, "prices": True}
    )
    lines: list[str] = []

    with service.client(DUMMY_KEY) as client:
        live_suite.test_live_combined_series_is_confirmed_where_confirmed_and_forecast_after(
            client, address, lines.append
        )

    assert [line for line in lines if "match get_forecast_prices" in line], lines


def test_the_package_and_the_live_suite_read_the_clock_only_through_timestamp_now() -> (
    None
):
    # The plugin pins `pandas.Timestamp.now` and nothing else: any other read of the
    # clock would escape the pin and bring back the race the pin was added to fix.
    clock = re.compile(
        r"\b(?:datetime|date)\.(?:now|today|utcnow)\("
        r"|Timestamp\.utcnow\(|\btime\.time\("
        r"|(?:Timestamp|to_datetime)\(\s*[\"'](?:now|today)[\"']"
    )
    sources = [
        *sorted(PACKAGE_DIR.glob("*.py")),
        REPO / "tests" / "test_minstroem_live.py",
    ]

    for path in sources:
        text = path.read_text(encoding="utf-8")
        assert clock.search(text) is None, path.name
    assert "pd.Timestamp.now(" in (PACKAGE_DIR / "prices.py").read_text(
        encoding="utf-8"
    )


def test_the_mocks_request_ids_are_uuids_like_the_specs_header() -> None:
    header = SPEC["components"]["headers"]["RequestId"]
    service = MinStroemService()

    seen = [
        service(httpx.Request("GET", f"{BASE_URL}/me/addresses")).headers[
            "X-Request-ID"
        ]
        for _ in range(3)
    ]

    assert header["schema"]["format"] == "uuid"
    assert seen == [conftest.minstroem_request_id(n) for n in (1, 2, 3)]
    assert len(set(seen)) == 3
    for found in seen:
        assert re.fullmatch(UUID_TEXT, found)


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
    for path in [
        *modules,
        *tests,
        "tests/fixtures/minstroem_openapi.json",
        "tests/minstroem_live_mock.py",
    ]:
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
    assert "tests/minstroem_live_mock.py" in RULES


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
