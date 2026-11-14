"""Each body constraint, end to end through the API."""

from __future__ import annotations

import json
import shutil

import pytest
from fastapi.testclient import TestClient

from planner.main import create_app
from planner.store import FileStore

from .helpers import (
    FIXTURE_STORE,
    MUSEUM_ID,
    fixture_nodes,
    object_ids,
    object_stops,
    plan_body,
    post_plan,
    segments_of,
)


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
def test_low_sensory_never_stops_in_tagged_nodes_and_names_any_it_passes_through(client, interests, budget, current_gallery):
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
                if node_id != start and nodes[node_id]["sensory"]:
                    assert any(nodes[node_id]["name"] in w for w in plan["warnings"]), seg
        # The test museum always has a calm way round, so nothing should be passed through at all.
        assert not any("passes through" in w for w in plan["warnings"])


def test_low_sensory_forced_through_a_tagged_node_says_so(tmp_path):
    """Remove the calm corridor: West is now reachable only through the bright Glass Court."""
    shutil.copytree(FIXTURE_STORE, tmp_path / "store")
    graph_file = tmp_path / "store" / "floor_graphs" / f"{MUSEUM_ID}.json"
    doc = json.loads(graph_file.read_text(encoding="utf-8"))
    doc["edges"] = [e for e in doc["edges"] if "west-corridor" not in (e["from"], e["to"])]
    doc["nodes"] = [n for n in doc["nodes"] if n["id"] != "west-corridor"]
    graph_file.write_text(json.dumps(doc), encoding="utf-8")
    forced = TestClient(create_app(store=FileStore(tmp_path / "store")))

    res = post_plan(forced, plan_body(interests=["animals"], constraints={"low_sensory": True}))
    plan = res["plan"]
    assert "testmuseum:t09" in object_ids(plan)  # in West, beyond the court
    assert all(stop["node_id"] != "bright-court" for stop in plan["stops"])
    assert "bright-court" in {seg["to_node_id"] for seg in segments_of(plan)}
    court = fixture_nodes()["bright-court"]["name"]
    assert any(court in w and "bright" in w and "no way around" in w for w in plan["warnings"])


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


# ---- applied.notes: the plan says what each choice did ----


def notes_for(client, **fields):
    return post_plan(client, plan_body(**fields))["applied"]["notes"]


def test_no_constraints_and_a_long_visit_have_nothing_to_say(client):
    assert notes_for(client, interests=["animals"], time_budget_minutes=120) == []


def test_short_visit_note_names_the_wing_and_says_when_it_was_automatic(client):
    asked = post_plan(client, plan_body(interests=["armor"], time_budget_minutes=120, constraints={"short_visit": True}))
    wing = asked["plan"]["wings"][0]
    assert asked["applied"]["notes"] == [f"Short visit: this plan stays in one wing, {wing}."]

    automatic = post_plan(client, plan_body(interests=["armor"], time_budget_minutes=45))
    assert automatic["applied"]["notes"] == [
        f"You have 45 minutes, so this plan stays in one wing, {automatic['plan']['wings'][0]}. "
        "Visits of 60 minutes or less always do."
    ]
    # Ticking the box for a visit that is already short changes the wording, not the plan.
    ticked = post_plan(client, plan_body(interests=["armor"], time_budget_minutes=45, constraints={"short_visit": True}))
    assert object_ids(ticked["plan"]) == object_ids(automatic["plan"])
    assert ticked["applied"]["notes"] == [f"Short visit: this plan stays in one wing, {ticked['plan']['wings'][0]}."]


def test_low_sensory_note_counts_what_was_left_out_and_names_the_space(client):
    notes = notes_for(client, interests=["animals"], time_budget_minutes=120, constraints={"low_sensory": True})
    court = fixture_nodes()["bright-court"]["name"]
    assert len(notes) == 1 and notes[0].startswith("Low sensory: ")
    assert court in notes[0] and "does not stop there" in notes[0]


def test_low_sensory_note_says_so_when_nothing_had_to_change(client):
    body = dict(interests=["armor"], time_budget_minutes=120, constraints={"low_sensory": True}, exclude_object_ids=[])
    calm = post_plan(client, plan_body(**body))
    plain = post_plan(client, plan_body(**{**body, "constraints": {}}))
    if object_ids(calm["plan"]) == object_ids(plain["plan"]) and "no stop had to change" in calm["applied"]["notes"][0]:
        return  # the honest "same route" case
    # Otherwise something really was left out, and the note must say what.
    assert "does not stop there" in calm["applied"]["notes"][0]


def test_step_free_note_names_the_elevator_and_never_appears_for_a_route_with_stairs(client):
    ok = post_plan(client, plan_body(interests=["animals"], time_budget_minutes=120, constraints={"step_free": True}))
    note = ok["applied"]["notes"][0]
    assert ok["plan"]["step_free"] is True and note.startswith("Step-free: every connection")
    lifts = {s["name"] for s in segments_of(ok["plan"]) if s["kind"] in ("elevator", "ramp")}
    assert all(name in note for name in lifts)

    blocked = post_plan(client, plan_body(interests=["astronomy"], constraints={"step_free": True}))
    assert blocked["status"] == "no_step_free_route"
    assert blocked["applied"]["notes"][0] == "Step-free: not possible for this request. The alternative shown uses stairs."
    assert not any("every connection" in n for n in blocked["applied"]["notes"])


def test_stamina_and_child_notes_match_the_plan(client):
    res = post_plan(client, plan_body(interests=["animals"], time_budget_minutes=90, constraints={"limited_stamina": True}))
    rests = sum(1 for s in res["plan"]["stops"] if s["kind"] == "rest")
    note = res["applied"]["notes"][0]
    assert "capped at 15 minutes" in note and f"walks {res['plan']['totals']['walking_minutes']:g}" in note
    assert (f"{rests} rest stop" in note) if rests else ("No rest stop was added" in note)

    res = post_plan(client, plan_body(interests=["animals"], time_budget_minutes=120, constraints={"with_young_child": True}))
    ending = res["plan"]["stops"][-1]
    assert ending["kind"] == "amenity"
    assert res["applied"]["notes"] == [
        "With a young child: no object stop is longer than 3 minutes, and larger objects are preferred. "
        f"The plan ends at {ending['node_name']}."
    ]
    assert all(s["dwell_minutes"] <= 3 for s in res["plan"]["stops"] if s["kind"] == "object")
