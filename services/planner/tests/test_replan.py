"""Replanning: current_gallery, exclude_object_ids, skip_wings, and the error for unknown galleries."""

from __future__ import annotations

from .helpers import assert_matches_schema, object_ids, object_stops, plan_body, post_plan

HIGHLIGHTS = [f"testmuseum:{i}" for i in ("t01", "t05", "t07", "t09", "t12", "t13", "t23", "t29", "t30")]


def test_replan_from_current_gallery_with_exclusions_and_skipped_wing(client):
    body = plan_body(
        interests=["animals"],
        time_budget_minutes=90,
        current_gallery="111",
        exclude_object_ids=["testmuseum:t09"],
        skip_wings=["east wing"],
    )
    res = post_plan(client, body)
    plan = res["plan"]
    assert plan["start"]["node_id"] == "west-1"
    assert "testmuseum:t09" not in object_ids(plan)
    assert all(stop["wing"] != "East Wing" for stop in object_stops(plan))
    assert plan["start"]["node_name"] == "West Galleries 111-112"
    first_leg = plan["start"]["route_to_first"]
    assert not first_leg or first_leg[0]["from_node_id"] == "west-1"


def test_excluded_objects_never_return(client):
    first = post_plan(client, plan_body(interests=["armor"], time_budget_minutes=45))
    seen = object_ids(first["plan"])
    second = post_plan(client, plan_body(interests=["armor"], time_budget_minutes=45, exclude_object_ids=seen))
    assert not set(seen) & set(object_ids(second["plan"]) if second["plan"] else [])


def test_replan_is_deterministic(client):
    body = plan_body(interests=["animals", "ceramics"], time_budget_minutes=120, current_gallery="201",
                     constraints={"limited_stamina": True, "with_young_child": True})
    assert post_plan(client, body) == post_plan(client, body)


def test_replan_from_stairs_only_room_stays_step_free(client):
    body = plan_body(interests=["astronomy"], time_budget_minutes=30, current_gallery="301", constraints={"step_free": True})
    res = post_plan(client, body)
    assert res["status"] == "ok"
    plan = res["plan"]
    assert plan["step_free"] is True
    assert set(object_ids(plan)) == {"testmuseum:t17", "testmuseum:t18"}
    assert all(stop["node_id"] == "tower" for stop in plan["stops"])


def test_skipping_every_wing_leaves_nothing_plannable(client):
    body = plan_body(interests=["animals"], skip_wings=["East Wing", "West Wing", "North Wing"])
    res = post_plan(client, body)
    assert res["status"] == "no_plannable_objects"
    assert res["plan"] is None and res["alternative"] is None
    assert "skip" in res["message"]


def test_nothing_reachable_explains_and_suggests(client):
    # Only the glass objects remain, and they sit in the bright Glass Court.
    body = plan_body(interests=["glass"], constraints={"low_sensory": True}, exclude_object_ids=HIGHLIGHTS)
    res = post_plan(client, body)
    assert res["status"] == "no_plannable_objects"
    assert res["applied"]["interests_matched"] == ["glass"]
    assert "Nothing fits in the 90 minutes" in res["message"]


def test_unmatched_interest_falls_back_to_highlights(client):
    res = post_plan(client, plan_body(interests=["dinosaurs"], time_budget_minutes=30))
    assert res["status"] == "ok"
    assert res["applied"]["interests_unmatched"] == ["dinosaurs"]
    for stop in object_stops(res["plan"]):
        assert stop["object"]["is_highlight"]
        assert stop["why"].startswith("One of the museum's highlights")
        assert stop["grounding"]["matched_tags"] == []


def test_unknown_current_gallery_is_422(client):
    # Gallery 999 exists in the object data but not in the floor graph.
    response = client.post("/api/plan", json=plan_body(current_gallery="999"))
    assert response.status_code == 422
    body = response.json()
    assert_matches_schema("Error", body)
    assert body["error"]["code"] == "unknown_gallery"
