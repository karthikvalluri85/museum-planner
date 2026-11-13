from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from planner.graph import Graph, RouteMode
from planner.models import FloorGraph

from .helpers import FIXTURE_STORE


def mode(start="entrance", step_free=False, avoid_sensory=False):
    return RouteMode(step_free=step_free, avoid_sensory=avoid_sensory, start=start)


def hops(path):
    return [(s.from_id, s.to_id, s.edge.name) for s in path]


def test_dijkstra_unrestricted_takes_the_cheaper_stairs(data):
    paths = data.graph.shortest_paths("entrance", mode())
    assert paths.dist["north-2"] == 3
    assert hops(paths.path_to("north-2")) == [("entrance", "hall", None), ("hall", "north-2", "Grand Stair")]
    assert paths.dist["tower"] == 5


def test_step_free_uses_the_slower_elevator_and_never_stairs(data):
    paths = data.graph.shortest_paths("entrance", mode(step_free=True))
    assert paths.dist["north-2"] == 5
    assert hops(paths.path_to("north-2"))[-1] == ("hall", "north-2", "North Elevator")
    assert not paths.reaches("tower")
    for node_id in paths.dist:
        assert all(s.edge.step_free for s in paths.path_to(node_id))


def test_low_sensory_detours_around_bright_node(data):
    plain = data.graph.shortest_paths("entrance", mode())
    assert plain.dist["west-1"] == 5
    assert "bright-court" in {s.to_id for s in plain.path_to("west-1")}

    calm = data.graph.shortest_paths("entrance", mode(avoid_sensory=True))
    assert calm.dist["west-1"] == 7
    assert [s.to_id for s in calm.path_to("west-1")] == ["hall", "west-corridor", "west-1"]


def test_low_sensory_never_stops_in_a_tagged_node_but_the_start_is_exempt(data):
    graph = data.graph
    from_hall = mode(start="hall", avoid_sensory=True)
    assert not graph.allows_stop("bright-court", from_hall)
    assert not graph.allows_stop("entrance", from_hall)  # loud and crowded, and not where the visitor is
    assert graph.allows_stop("west-1", from_hall)
    assert graph.allows_stop("bright-court", mode(start="bright-court", avoid_sensory=True))
    assert graph.allows_stop("bright-court", mode(start="hall"))  # not a low-sensory visit

    from_bright = graph.shortest_paths("bright-court", mode(start="bright-court", avoid_sensory=True))
    assert from_bright.dist["west-1"] == 2


def _without_west_corridor(data):
    """The test museum with the calm detour removed: West is reachable only through the bright court."""
    doc = json.loads((FIXTURE_STORE / "floor_graphs" / "testmuseum.json").read_text(encoding="utf-8"))
    doc["edges"] = [e for e in doc["edges"] if "west-corridor" not in (e["from"], e["to"])]
    doc["nodes"] = [n for n in doc["nodes"] if n["id"] != "west-corridor"]
    return Graph(FloorGraph.model_validate(doc))


def test_low_sensory_passes_through_a_tagged_node_only_when_there_is_no_other_way(data):
    forced = _without_west_corridor(data).shortest_paths("entrance", mode(avoid_sensory=True))
    assert [s.to_id for s in forced.path_to("west-1")] == ["hall", "bright-court", "west-1"]
    assert forced.dist["west-1"] == 5  # real walking minutes, no penalty added


def test_low_sensory_prefers_fewer_tagged_nodes_over_fewer_minutes():
    def node(node_id, sensory=()):
        kind = "entrance" if node_id == "a" else "gallery"
        return {"id": node_id, "name": node_id.upper(), "kind": kind, "floor": "1", "wing": "W",
                "galleries": [] if node_id == "a" else [node_id], "sensory": list(sensory),
                "has_seating": False, "near_restroom": False, "near_cafe": False}

    def edge(a, b, minutes):
        return {"from": a, "to": b, "walk_minutes": minutes, "step_free": True, "kind": "level", "name": None}

    graph = Graph(FloorGraph.model_validate({
        "museum_id": "tiny", "version": "1", "source": "test",
        "verification": {"status": "unverified", "notes": None}, "entrance_node_id": "a",
        "nodes": [node("a"), node("loud1", ["loud"]), node("loud2", ["crowded"]), node("bright1", ["bright"]), node("z")],
        # a -> z: 2 minutes through two tagged nodes, or 20 minutes through one.
        "edges": [edge("a", "loud1", 0.5), edge("loud1", "loud2", 0.5), edge("loud2", "z", 1),
                  edge("a", "bright1", 10), edge("bright1", "z", 10)],
    }))
    assert graph.shortest_paths("a", mode(start="a")).dist["z"] == 2
    calm = graph.shortest_paths("a", mode(start="a", avoid_sensory=True))
    assert [s.to_id for s in calm.path_to("z")] == ["bright1", "z"]
    assert calm.dist["z"] == 20


def test_path_segments_follow_direction_of_travel(data):
    path = data.graph.shortest_paths("tower", mode(start="tower")).path_to("entrance")
    assert [(s.from_id, s.to_id) for s in path] == [("tower", "north-2"), ("north-2", "hall"), ("hall", "entrance")]
    assert data.graph.shortest_paths("hall", mode()).path_to("hall") == []


def test_gallery_lookup(data):
    assert data.graph.node_of_gallery["111"] == "west-1"
    assert "999" not in data.graph.node_of_gallery


def test_graph_with_stairs_marked_step_free_is_rejected():
    raw = json.loads((FIXTURE_STORE / "floor_graphs" / "brokenmuseum.json").read_text(encoding="utf-8"))
    with pytest.raises(ValidationError):
        FloorGraph.model_validate(raw)


def test_graph_with_unnamed_stairs_is_rejected():
    raw = json.loads((FIXTURE_STORE / "floor_graphs" / "testmuseum.json").read_text(encoding="utf-8"))
    raw["edges"][-1]["name"] = None
    with pytest.raises(ValidationError):
        FloorGraph.model_validate(raw)
