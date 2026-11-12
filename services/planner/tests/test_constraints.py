"""Each body constraint, end to end through the API."""

from __future__ import annotations

import pytest

from .helpers import fixture_nodes, object_ids, object_stops, plan_body, post_plan, segments_of


def test_stamina_caps_walking_and_inserts_rest_stops(client):
    body = plan_body(interests=["animals"], time_budget_minutes=90, constraints={"limited_stamina": True})
    res = post_plan(client, body)
    assert res["status"] == "ok"
    assert res["applied"]["constraints"]["max_walking_minutes"] == 15
    plan = res["plan"]
    assert plan["totals"]["walking_minutes"] <= 15

    nodes = fixture_nodes()
    rests = [s for s in plan["stops"] if s["kind"] == "rest"]
    assert rests, "expected at least one rest stop"
    for rest in rests:
        assert nodes[rest["node_id"]]["has_seating"]
        assert rest["dwell_minutes"] == 5
        assert rest["gallery"] is None and rest["object"] is None
        assert rest["grounding"] == {"matched_tags": [], "fields": ["node.has_seating"]}
        assert rest["why"].startswith(f"Seating in {rest['node_name']}.")
        previous = plan["stops"][rest["order"] - 2]
        assert previous["kind"] == "object" and previous["node_id"] == rest["node_id"]
    # Hand-traced: rests after the walk into West Galleries and into North Galleries.
    assert [r["node_id"] for r in rests] == ["west-1", "north-2"]


@pytest.mark.parametrize("cap", [5, 10, 20])
def test_explicit_walking_cap_is_respected(client, cap):
    body = plan_body(
        interests=["animals", "ceramics"],
        time_budget_minutes=180,
        constraints={"limited_stamina": True, "max_walking_minutes": cap},
    )
    res = post_plan(client, body)
    assert res["applied"]["constraints"]["max_walking_minutes"] == cap
    if res["plan"]:
        assert res["plan"]["totals"]["walking_minutes"] <= cap


def test_walking_cap_is_null_without_limited_stamina(client):
    res = post_plan(client, plan_body(constraints={"max_walking_minutes": 10}))
    assert res["applied"]["constraints"]["max_walking_minutes"] is None


def test_young_child_ends_at_restroom_and_cafe_with_short_dwells(client):
    body = plan_body(interests=["animals"], time_budget_minutes=45, constraints={"with_young_child": True})
    res = post_plan(client, body)
    plan = res["plan"]
    last = plan["stops"][-1]
    assert last["kind"] == "amenity"
    assert last["node_id"] == "cafe"
    assert "restroom" in last["why"] and "cafe" in last["why"]
    assert last["grounding"]["fields"] == ["node.near_restroom", "node.near_cafe"]
    assert last["gallery"] is None and last["object"] is None
    assert all(s["dwell_minutes"] <= 3 for s in object_stops(plan))
    # Hand-traced (short visit picks West Wing; monumental and large objects first).
    assert object_ids(plan) == [
        "testmuseum:t09", "testmuseum:t11", "testmuseum:t07",
        "testmuseum:t10", "testmuseum:t08", "testmuseum:t12",
    ]
    assert plan["totals"] == {"dwell_minutes": 23, "walking_minutes": 19, "total_minutes": 42, "time_budget_minutes": 45}


def test_young_child_large_objects_say_so(client):
    res = post_plan(client, plan_body(interests=["animals"], time_budget_minutes=45, constraints={"with_young_child": True}))
    first = res["plan"]["stops"][0]
    assert first["object"]["size_class"] == "large"
    assert first["why"].endswith("It is large, easy for a child to see.")
    assert "object.size_class" in first["grounding"]["fields"]


@pytest.mark.parametrize("interests", [["animals"], [], ["armor"], ["cats", "glass"]])
@pytest.mark.parametrize("budget", [45, 120])
@pytest.mark.parametrize("current_gallery", [None, "201"])
def test_low_sensory_avoids_tagged_nodes_as_stops_and_pass_through(client, interests, budget, current_gallery):
    body = plan_body(
        interests=interests,
        time_budget_minutes=budget,
        constraints={"low_sensory": True},
        current_gallery=current_gallery,
    )
    res = post_plan(client, body)
    nodes = fixture_nodes()
    for plan in [p for p in (res["plan"], res["alternative"]) if p]:
        start = plan["start"]["node_id"]
        for stop in plan["stops"]:
            assert stop["node_id"] == start or not nodes[stop["node_id"]]["sensory"]
        for seg in segments_of(plan):
            for node_id in (seg["from_node_id"], seg["to_node_id"]):
                assert node_id == start or not nodes[node_id]["sensory"], seg


def test_low_sensory_drops_objects_only_in_bright_node(client):
    res = post_plan(client, plan_body(interests=["animals"], constraints={"low_sensory": True}))
    ids = object_ids(res["plan"])
    assert "testmuseum:t07" not in ids and "testmuseum:t08" not in ids
    assert "testmuseum:t09" in ids  # reached through the West Corridor detour


@pytest.mark.parametrize("interests", [["animals"], [], ["armor"], ["ceramics", "paintings"]])
@pytest.mark.parametrize(("budget", "flag"), [(30, False), (45, False), (60, False), (120, True), (240, True)])
def test_short_visit_stays_in_one_wing(client, interests, budget, flag):
    body = plan_body(interests=interests, time_budget_minutes=budget, constraints={"short_visit": flag})
    res = post_plan(client, body)
    assert res["applied"]["constraints"]["short_visit"] is True
    for plan in [p for p in (res["plan"], res["alternative"]) if p]:
        assert len({s["wing"] for s in object_stops(plan)}) == 1
        assert len(plan["wings"]) == 1


def test_long_visit_is_not_short(client):
    res = post_plan(client, plan_body(interests=["animals"], time_budget_minutes=120))
    assert res["applied"]["constraints"]["short_visit"] is False
    assert len(res["plan"]["wings"]) > 1


@pytest.mark.parametrize("budget", [15, 20, 30, 45, 61, 90, 150, 240])
@pytest.mark.parametrize(
    "constraints",
    [{}, {"limited_stamina": True}, {"with_young_child": True}, {"low_sensory": True}, {"step_free": True},
     {"limited_stamina": True, "with_young_child": True, "low_sensory": True, "step_free": True}],
)
def test_time_budget_is_never_exceeded(client, budget, constraints):
    res = post_plan(client, plan_body(interests=["animals", "armor"], time_budget_minutes=budget, constraints=constraints))
    for plan in [p for p in (res["plan"], res["alternative"]) if p]:
        assert plan["totals"]["total_minutes"] <= budget
        assert plan["totals"]["time_budget_minutes"] == budget
