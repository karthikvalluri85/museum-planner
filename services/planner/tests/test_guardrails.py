"""The product's guardrails, one test each (plus the in-code integrity checks)."""

from __future__ import annotations

import dataclasses
import itertools

import pytest

from planner.models import Verification
from planner.service import PUBLIC_DATA_NOTICE, PlanIntegrityError, check_plan_integrity, notices

from .helpers import (
    STALE_OBJECT_ID,
    UNMAPPED_OBJECT_IDS,
    fixture_nodes,
    object_stops,
    plan_body,
    plans_in,
    post_plan,
    segments_of,
    stored_objects,
)

INTEREST_SETS = [[], ["armor"], ["animals"], ["astronomy"], ["ceramics", "astronomy"], ["dinosaurs"], ["cats"]]
BUDGETS = [15, 30, 45, 90, 240]
FLAG_COMBOS = [
    dict(zip(("limited_stamina", "with_young_child", "low_sensory"), flags))
    for flags in itertools.product([False, True], repeat=3)
]
START_GALLERIES = [None, "201", "110", "301"]


def grid_bodies(step_free: bool):
    for interests, budget, flags, gallery in itertools.product(INTEREST_SETS, BUDGETS, FLAG_COMBOS, START_GALLERIES):
        yield plan_body(
            interests=interests,
            time_budget_minutes=budget,
            constraints={"step_free": step_free, **flags},
            current_gallery=gallery,
        )


def test_1_step_free_ok_plans_never_use_stairs(client):
    seen_statuses = set()
    for body in grid_bodies(step_free=True):
        res = post_plan(client, body)
        seen_statuses.add(res["status"])
        if res["status"] == "ok":
            plan = res["plan"]
            assert plan["step_free"] is True, body
            assert plan["stairs"] == [], body
            assert all(seg["step_free"] for seg in segments_of(plan)), body
            assert all(seg["kind"] not in ("stairs", "escalator") for seg in segments_of(plan)), body
    # The grid must actually exercise both outcomes, or this test proves little.
    assert {"ok", "no_step_free_route"} <= seen_statuses


def test_step_free_is_computed_not_copied(client):
    # Not requested, yet the plan is honestly step-free when its segments are.
    res = post_plan(client, plan_body(interests=["armor"], time_budget_minutes=45))
    assert res["plan"]["step_free"] is True
    # Not requested, and the plan uses stairs: step_free is false and the stairs are named.
    res = post_plan(client, plan_body(interests=["astronomy"], time_budget_minutes=45))
    assert res["plan"]["step_free"] is False
    assert {s["name"] for s in res["plan"]["stairs"]} == {"Grand Stair", "Tower Stair"}


def test_2_stairs_only_interest_reports_no_step_free_route(client):
    body = plan_body(interests=["astronomy"], time_budget_minutes=45, constraints={"step_free": True})
    res = post_plan(client, body)
    assert res["status"] == "no_step_free_route"
    assert res["plan"] is None
    alternative = res["alternative"]
    names = [s["name"] for s in alternative["stairs"]]
    assert names == ["Grand Stair", "Tower Stair"]
    for name in names:
        assert name in res["message"]
    assert "testmuseum:t17" in [s["object"]["id"] for s in object_stops(alternative)]
    assert res["applied"]["interests_matched"] == ["astronomy"]


def test_step_free_plan_warns_about_stairs_only_matches(client):
    body = plan_body(interests=["ceramics", "astronomy"], time_budget_minutes=90, constraints={"step_free": True})
    res = post_plan(client, body)
    assert res["status"] == "ok"
    assert res["plan"]["step_free"] is True
    warning = next(w for w in res["plan"]["warnings"] if "stairs" in w)
    assert warning == "2 objects matching your interests can only be reached by stairs (Grand Stair, Tower Stair) and were left out."


def test_3_unmapped_galleries_never_appear(client):
    for interests in (["cats"], ["armor"], ["animals"], []):
        for step_free in (False, True):
            res = post_plan(client, plan_body(interests=interests, time_budget_minutes=240, constraints={"step_free": step_free}))
            for plan in plans_in(res):
                for stop in object_stops(plan):
                    assert stop["object"]["id"] not in UNMAPPED_OBJECT_IDS
                    assert stop["object"]["id"] != STALE_OBJECT_ID
                    assert stop["gallery"] == stored_objects()[stop["object"]["id"]]["gallery"]
                    assert stop["gallery"] in fixture_nodes()[stop["node_id"]]["galleries"]


def test_4_matched_tags_are_a_subset_of_object_tags(client):
    for body in grid_bodies(step_free=False):
        res = post_plan(client, body)
        for plan in plans_in(res):
            for stop in object_stops(plan):
                assert set(stop["grounding"]["matched_tags"]) <= set(stop["object"]["tags"])
                for tag in stop["grounding"]["matched_tags"][:3]:  # the sentence names up to 3
                    assert tag in stop["why"]


def test_5_notices_state_public_data_and_floor_status(client):
    res = post_plan(client, plan_body(interests=["armor"]))
    assert any("public data" in n and "not reviewed" in n for n in res["notices"])
    assert res["notices"][0] == "Built from the museum's public data. The museum has not reviewed this plan."
    assert res["notices"][-1] == (
        "Floor layout, elevators, and stairs for Test Museum (synthetic) were mapped by hand from its "
        "published floor plan and have not been checked on site. Confirm step-free access with museum staff."
    )


def _with_verification(data, status, verified_on=None):
    fg = data.floor_graph.model_copy(
        update={"verification": Verification(status=status, notes=None, verified_on=verified_on)}
    )
    return dataclasses.replace(data, floor_graph=fg)


def test_floor_notice_follows_verification_status(data):
    assert "only partly checked" in notices(_with_verification(data, "partially_verified"))[-1]
    assert "checked on site on 2026-11-01" in notices(_with_verification(data, "verified", "2026-11-01"))[-1]


def test_public_data_notice_is_guaranteed_even_if_stored_text_omits_it(data):
    museum = data.museum.model_copy(update={"data_notice": "Collection data courtesy of the museum API."})
    out = notices(dataclasses.replace(data, museum=museum))
    assert out[0] == "Collection data courtesy of the museum API."
    assert PUBLIC_DATA_NOTICE in out


def _plan_with_segment(step_free_claim: bool, segment_step_free: bool, stairs: list) -> dict:
    seg = {
        "from_node_id": "hall", "to_node_id": "north-2", "from_name": "Central Hall", "to_name": "North",
        "walk_minutes": 2, "step_free": segment_step_free, "kind": "stairs", "name": "Grand Stair",
    }
    return {
        "start": {"node_id": "hall", "node_name": "Central Hall", "walk_minutes_to_first": 2, "route_to_first": [seg]},
        "stops": [],
        "totals": {"dwell_minutes": 0, "walking_minutes": 2, "total_minutes": 2, "time_budget_minutes": 30},
        "step_free": step_free_claim,
        "stairs": stairs,
        "wings": [],
        "warnings": [],
    }


def test_integrity_check_refuses_a_false_step_free_claim():
    with pytest.raises(PlanIntegrityError):
        check_plan_integrity(_plan_with_segment(step_free_claim=True, segment_step_free=False, stairs=[]))


def test_integrity_check_refuses_unnamed_stairs_in_plan():
    with pytest.raises(PlanIntegrityError):
        check_plan_integrity(_plan_with_segment(step_free_claim=False, segment_step_free=False, stairs=[]))
