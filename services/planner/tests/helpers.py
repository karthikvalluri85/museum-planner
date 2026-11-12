"""Shared test helpers: contract schema validation, fixture access, and the checked plan call."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from .invariants import check_response

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parents[2]
FIXTURE_STORE = TESTS_DIR / "fixtures" / "store"
OPENAPI_PATH = REPO_ROOT / "contracts" / "planner" / "openapi.yaml"
EXAMPLES_DIR = REPO_ROOT / "contracts" / "examples"
MUSEUM_ID = "testmuseum"

UNMAPPED_OBJECT_IDS = {"testmuseum:t19", "testmuseum:t20"}
STALE_OBJECT_ID = "testmuseum:stale1"


@lru_cache(maxsize=1)
def openapi() -> dict[str, Any]:
    return yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def _validator(schema_name: str) -> Draft202012Validator:
    return Draft202012Validator(
        {"$ref": f"#/components/schemas/{schema_name}", "components": openapi()["components"]}
    )


def assert_matches_schema(schema_name: str, body: Any) -> None:
    errors = list(_validator(schema_name).iter_errors(body))
    assert not errors, "\n".join(f"{list(e.absolute_path)}: {e.message}" for e in errors)


@lru_cache(maxsize=1)
def fixture_graph() -> dict[str, Any]:
    return json.loads((FIXTURE_STORE / "floor_graphs" / f"{MUSEUM_ID}.json").read_text(encoding="utf-8"))


def fixture_nodes() -> dict[str, dict[str, Any]]:
    return {n["id"]: n for n in fixture_graph()["nodes"]}


@lru_cache(maxsize=1)
def stored_objects() -> dict[str, dict[str, Any]]:
    """Objects as the read protocol sees them: chunks 0000..chunk_count-1 only."""
    museum = json.loads((FIXTURE_STORE / "museums" / f"{MUSEUM_ID}.json").read_text(encoding="utf-8"))
    out: dict[str, dict[str, Any]] = {}
    for index in range(museum["chunk_count"]):
        path = FIXTURE_STORE / "museums" / MUSEUM_ID / "object_chunks" / f"{index:04d}.json"
        for obj in json.loads(path.read_text(encoding="utf-8"))["objects"]:
            out[obj["id"]] = obj
    return out


def plan_body(**fields: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"museum_id": MUSEUM_ID, "interests": [], "time_budget_minutes": 90}
    body.update(fields)
    return body


def post_plan(client: TestClient, body: dict[str, Any]) -> dict[str, Any]:
    """POST /api/plan, then check the schema and invariants I1 to I8 on the 200 response."""
    response = client.post("/api/plan", json=body)
    assert response.status_code == 200, response.text
    data = response.json()
    assert_matches_schema("PlanResponse", data)
    problems = check_response(data, request=body, objects=stored_objects())
    assert not problems, problems
    return data


def plans_in(response: dict[str, Any]) -> list[dict[str, Any]]:
    return [p for p in (response["plan"], response["alternative"]) if p is not None]


def segments_of(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return plan["start"]["route_to_first"] + [s for stop in plan["stops"] for s in stop["route_to_next"]]


def object_stops(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in plan["stops"] if s["kind"] == "object"]


def object_ids(plan: dict[str, Any]) -> list[str]:
    return [s["object"]["id"] for s in object_stops(plan)]
