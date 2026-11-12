"""Plans traced by hand against the fixture. If these fail, compare with the trace in each docstring."""

from __future__ import annotations

from .helpers import object_ids, plan_body, post_plan


def test_45_minute_step_free_armor_plan(client):
    """Short visit (45 <= 60). Wing values: East 21 - 0.2*3 = 20.4, West 1.4, North 0.5.

    Greedy (penalty 0.15, max 4 per node): t01 (4.5 - 0.45) from the entrance, t05 in East 103
    (4.5 - 0.3), t06 there (walk 0), then t02, t03, t21 back in East 101-102 (ties broken by id).
    t27 is left out because East 101-102 already has 4 stops.
    """
    body = plan_body(interests=["armor"], time_budget_minutes=45, constraints={"step_free": True})
    res = post_plan(client, body)
    assert res["status"] == "ok"
    plan = res["plan"]
    assert object_ids(plan) == [f"testmuseum:{i}" for i in ("t01", "t05", "t06", "t02", "t03", "t21")]
    assert [s["walk_minutes_to_next"] for s in plan["stops"]] == [2, 0, 2, 0, 0, None]
    assert plan["start"]["walk_minutes_to_first"] == 3
    assert [s["to_node_id"] for s in plan["start"]["route_to_first"]] == ["hall", "east-1"]
    assert plan["totals"] == {"dwell_minutes": 23, "walking_minutes": 7, "total_minutes": 30, "time_budget_minutes": 45}
    assert plan["step_free"] is True and plan["stairs"] == []
    assert plan["wings"] == ["East Wing"]
    assert plan["warnings"] == []
    assert plan["stops"][0]["why"] == "Matches your interest in armor: tagged arms and armor. One of the museum's highlights."
    assert plan["stops"][0]["gallery"] == "101"
    assert res["applied"]["constraints"] == {
        "step_free": True, "limited_stamina": False, "max_walking_minutes": None,
        "with_young_child": False, "low_sensory": False, "short_visit": True,
    }


def test_45_minute_step_free_astronomy_has_no_step_free_route(client):
    """Step-free run reaches no astronomy object (Tower Room is stairs-only), so the
    unrestricted run is offered: North Wing, t17 and t18 in the Tower Room, then the three
    North highlights as filler (t13, t23, t29)."""
    body = plan_body(interests=["astronomy"], time_budget_minutes=45, constraints={"step_free": True})
    res = post_plan(client, body)
    assert res["status"] == "no_step_free_route"
    alternative = res["alternative"]
    assert object_ids(alternative) == [f"testmuseum:{i}" for i in ("t17", "t18", "t13", "t23", "t29")]
    assert alternative["totals"]["walking_minutes"] == 7
    assert alternative["totals"]["dwell_minutes"] == 22
    assert res["message"] == (
        "No step-free route reaches anything matching your interests. The closest alternative uses "
        "the Grand Stair between Central Hall and North Galleries 201-202 and "
        "the Tower Stair between North Galleries 201-202 and Tower Room 301."
    )


def test_90_minute_limited_stamina_animals_plan(client):
    """Penalty 0.4, cap 15, rest after 5 walking minutes at a node with seating."""
    body = plan_body(interests=["animals"], time_budget_minutes=90, constraints={"limited_stamina": True})
    plan = post_plan(client, body)["plan"]
    kinds = [(s["kind"], s["object"]["id"] if s["object"] else s["node_id"]) for s in plan["stops"]]
    assert kinds == [
        ("object", "testmuseum:t07"),
        ("object", "testmuseum:t09"),
        ("rest", "west-1"),
        ("object", "testmuseum:t10"),
        ("object", "testmuseum:t11"),
        ("object", "testmuseum:t08"),
        ("object", "testmuseum:t29"),
        ("rest", "north-2"),
        ("object", "testmuseum:t13"),
        ("object", "testmuseum:t23"),
        ("object", "testmuseum:t01"),
    ]
    assert plan["totals"]["walking_minutes"] == 15
    assert plan["stops"][2]["why"] == "Seating in West Galleries 111-112. You have walked about 5 minutes since your last rest."
