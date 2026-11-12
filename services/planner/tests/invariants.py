"""Python port of tools/lib/plan-invariants.mjs: invariants I1 to I8 from contracts/README.md.

check_response returns a list of violation strings (empty means the response is clean).
request: the PlanRequest body (enables I2). objects: {object id: stored object dict} (enables
the store half of I4).
"""

from __future__ import annotations

import re
from typing import Any

EPS = 0.051


def _segments(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return plan["start"]["route_to_first"] + [s for stop in plan["stops"] for s in stop["route_to_next"]]


def _same_segment(stair: dict[str, Any], seg: dict[str, Any]) -> bool:
    forward = stair["from_node_id"] == seg["from_node_id"] and stair["to_node_id"] == seg["to_node_id"]
    backward = stair["from_node_id"] == seg["to_node_id"] and stair["to_node_id"] == seg["from_node_id"]
    return stair["name"] == seg["name"] and (forward or backward)


def _check_plan(plan: dict[str, Any], label: str, objects: dict[str, dict] | None) -> list[str]:
    out: list[str] = []

    def bad(rule: str, msg: str) -> None:
        out.append(f"{rule} {label}: {msg}")

    stepped = [s for s in _segments(plan) if not s["step_free"]]

    # I1
    if plan["step_free"] != (len(stepped) == 0):
        bad("I1", f"step_free is {plan['step_free']} but {len(stepped)} segment(s) are not step-free")
    if plan["step_free"] and plan["stairs"]:
        bad("I1", "step_free is true but stairs is not empty")
    for seg in stepped:
        if not seg["name"]:
            bad("I1", f"non-step-free segment {seg['from_node_id']} -> {seg['to_node_id']} has no name")
        elif not any(_same_segment(st, seg) for st in plan["stairs"]):
            bad("I1", f"stairs does not name {seg['name']!r} ({seg['from_node_id']} -> {seg['to_node_id']})")

    # I4, I5
    for stop in plan["stops"]:
        if stop["kind"] != "object":
            continue
        if not stop["gallery"]:
            bad("I4", f"stop {stop['order']} has no gallery")
        if not stop["object"]:
            bad("I4", f"stop {stop['order']} has no object")
            continue
        stored = objects.get(stop["object"]["id"]) if objects is not None else None
        if objects is not None and stored is None:
            bad("I4", f"stop {stop['order']}: object {stop['object']['id']} is not in the store")
        if stored is not None and stored["gallery"] != stop["gallery"]:
            bad("I4", f"stop {stop['order']}: gallery {stop['gallery']!r} differs from stored {stored['gallery']!r}")
        tags = set(stored["tags"] if stored is not None else stop["object"]["tags"])
        for tag in stop["grounding"]["matched_tags"]:
            if tag not in tags:
                bad("I5", f"stop {stop['order']}: matched tag {tag!r} is not one of the object's tags")
        if not stop["grounding"]["fields"]:
            bad("I5", f"stop {stop['order']}: grounding.fields is empty")

    # I6
    dwell = sum(s["dwell_minutes"] for s in plan["stops"])
    walking = plan["start"]["walk_minutes_to_first"] + sum(s["walk_minutes_to_next"] or 0 for s in plan["stops"])
    t = plan["totals"]
    if t["dwell_minutes"] != dwell:
        bad("I6", f"totals.dwell_minutes {t['dwell_minutes']} != sum of stops {dwell}")
    if abs(t["walking_minutes"] - walking) > EPS:
        bad("I6", f"totals.walking_minutes {t['walking_minutes']} != sum of legs {walking}")
    if abs(t["total_minutes"] - (dwell + walking)) > EPS:
        bad("I6", f"totals.total_minutes {t['total_minutes']} != dwell + walking {dwell + walking}")
    if t["total_minutes"] > t["time_budget_minutes"] + EPS:
        bad("I6", f"total {t['total_minutes']} exceeds budget {t['time_budget_minutes']}")
    legs = [(plan["start"]["walk_minutes_to_first"], plan["start"]["route_to_first"], "start")]
    legs += [(s["walk_minutes_to_next"] or 0, s["route_to_next"], f"stop {s['order']}") for s in plan["stops"]]
    for walk, route, name in legs:
        route_sum = sum(seg["walk_minutes"] for seg in route)
        if walk + EPS < route_sum:
            bad("I6", f"{name}: walk minutes {walk} is less than its route segments {route_sum}")

    # I8
    stops = plan["stops"]
    for i, stop in enumerate(stops):
        if stop["order"] != i + 1:
            bad("I8", f"stop at index {i} has order {stop['order']}")
        last = i == len(stops) - 1
        if last and (stop["walk_minutes_to_next"] is not None or stop["route_to_next"]):
            bad("I8", "last stop must have null walk_minutes_to_next and empty route_to_next")
        if not last and stop["walk_minutes_to_next"] is None:
            bad("I8", f"stop {stop['order']} is not last but walk_minutes_to_next is null")

    return out


def check_response(
    response: dict[str, Any],
    request: dict[str, Any] | None = None,
    objects: dict[str, dict] | None = None,
) -> list[str]:
    out: list[str] = []
    status, plan, alternative = response["status"], response["plan"], response["alternative"]

    if status == "ok":
        if not plan:
            out.append("I3 status is ok but plan is null")
        step_free_requested = bool(((request or {}).get("constraints") or {}).get("step_free"))
        if step_free_requested and plan and not plan["step_free"]:
            out.append("I2 step-free was requested, status is ok, but plan.step_free is false")
    elif status == "no_step_free_route":
        if plan is not None:
            out.append("I3 status is no_step_free_route but plan is not null")
        if not response["message"]:
            out.append("I3 status is no_step_free_route but message is empty")
        if not alternative:
            out.append("I3 status is no_step_free_route but alternative is null")
        elif not alternative["stairs"]:
            out.append("I3 alternative must name the stairs it uses")
    elif status == "no_plannable_objects":
        if plan is not None or alternative is not None:
            out.append("I3 status is no_plannable_objects but a plan was returned")
        if not response["message"]:
            out.append("I3 status is no_plannable_objects but message is empty")

    if plan:
        out += _check_plan(plan, "plan", objects)
    if alternative:
        out += _check_plan(alternative, "alternative", objects)

    # I7
    if not any(re.search("public data", n, re.I) and re.search("not reviewed", n, re.I) for n in response["notices"]):
        out.append("I7 notices does not state that the plan is built from public data and not reviewed by the museum")
    return out
