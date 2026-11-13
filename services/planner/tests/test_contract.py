"""The HTTP surface matches contracts/planner/openapi.yaml, including error bodies."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from planner.main import create_app
from planner.models import PlanRequest
from planner.store import FileStore

from .helpers import EXAMPLES_DIR, FIXTURE_STORE, assert_matches_schema, openapi, plan_body, post_plan

METHODS = {"get", "post", "put", "patch", "delete"}


def operations(spec: dict) -> set[tuple[str, str]]:
    return {(m, path) for path, item in spec["paths"].items() for m in item if m in METHODS}


def test_routes_match_the_contract(client):
    assert operations(client.app.openapi()) == operations(openapi())


@pytest.mark.parametrize("name", ["plan-request.valid.json", "plan-request.replan.valid.json"])
def test_valid_example_requests_parse(name):
    PlanRequest.model_validate(json.loads((EXAMPLES_DIR / name).read_text(encoding="utf-8")))


def test_invalid_example_request_is_rejected():
    with pytest.raises(ValidationError):
        PlanRequest.model_validate(json.loads((EXAMPLES_DIR / "plan-request.invalid.json").read_text(encoding="utf-8")))


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert_matches_schema("Health", response.json())


def test_museum_list_has_only_complete_museums(client):
    response = client.get("/api/museums")
    assert response.status_code == 200
    body = response.json()
    assert_matches_schema("MuseumList", body)
    assert [m["id"] for m in body["museums"]] == ["testmuseum"]  # orphan has no graph, broken fails validation


def test_museum_detail(client):
    response = client.get("/api/museums/testmuseum")
    assert response.status_code == 200
    body = response.json()
    assert_matches_schema("MuseumDetail", body)
    assert body["wings"] == ["East Wing", "North Wing", "West Wing"]
    assert body["floor_data"]["verification_status"] == "unverified"


@pytest.mark.parametrize("museum_id", ["nosuchmuseum", "orphanmuseum", "Not-A-Valid-Id", "..."])
def test_unknown_museum_detail_is_404(client, museum_id):
    response = client.get(f"/api/museums/{museum_id}")
    assert response.status_code == 404
    body = response.json()
    assert_matches_schema("Error", body)
    assert body["error"]["code"] == "museum_not_found"


def test_museum_with_invalid_documents_is_503_not_a_crash(client):
    response = client.get("/api/museums/brokenmuseum")
    assert response.status_code == 503
    body = response.json()
    assert_matches_schema("Error", body)
    assert body["error"]["code"] == "store_unavailable"


def test_plan_for_unknown_museum_is_404(client):
    response = client.post("/api/plan", json=plan_body(museum_id="nosuchmuseum"))
    assert response.status_code == 404
    assert_matches_schema("Error", response.json())


@pytest.mark.parametrize(
    "body",
    [
        json.loads((EXAMPLES_DIR / "plan-request.invalid.json").read_text(encoding="utf-8")),
        plan_body(time_budget_minutes=241),
        plan_body(time_budget_minutes="45"),
        plan_body(constraints={"step_free": "yes"}),
        plan_body(interests=["x"] * 13),
        plan_body(surprise=True),
        {"interests": ["armor"]},
    ],
)
def test_invalid_plan_requests_are_422_invalid_request(client, body):
    response = client.post("/api/plan", json=body)
    assert response.status_code == 422
    payload = response.json()
    assert_matches_schema("Error", payload)
    assert payload["error"]["code"] == "invalid_request"
    assert payload["error"]["details"]


def test_malformed_json_is_422(client):
    response = client.post("/api/plan", content=b"{not json", headers={"content-type": "application/json"})
    assert response.status_code == 422
    assert_matches_schema("Error", response.json())


def test_example_requests_work_against_the_fixture(client):
    for name in ("plan-request.valid.json", "plan-request.replan.valid.json"):
        body = json.loads((EXAMPLES_DIR / name).read_text(encoding="utf-8"))
        body["museum_id"] = "testmuseum"
        body["current_gallery"] = None
        post_plan(client, body)


def test_app_starts_without_static_dir(monkeypatch, tmp_path):
    import planner.main

    monkeypatch.setattr(planner.main, "STATIC_DIR", tmp_path / "missing")
    app = create_app(store=FileStore(FIXTURE_STORE))
    assert all(getattr(r, "name", None) != "static" for r in app.routes)


def test_static_dir_is_served_when_present(monkeypatch, tmp_path):
    import planner.main
    from fastapi.testclient import TestClient

    (tmp_path / "index.html").write_text("<!doctype html><title>planner</title>", encoding="utf-8")
    monkeypatch.setattr(planner.main, "STATIC_DIR", tmp_path)
    static_client = TestClient(create_app(store=FileStore(FIXTURE_STORE)))
    assert "planner" in static_client.get("/").text
    assert static_client.get("/health").json() == {"status": "ok"}
