from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from planner.graph import RouteMode
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
    assert not calm.reaches("bright-court")
    assert [s.to_id for s in calm.path_to("west-1")] == ["hall", "west-corridor", "west-1"]


def test_low_sensory_allows_the_start_node_only(data):
    from_bright = data.graph.shortest_paths("bright-court", mode(start="bright-court", avoid_sensory=True))
    assert from_bright.dist["west-1"] == 2
    from_hall = data.graph.shortest_paths("hall", mode(start="hall", avoid_sensory=True))
    assert not from_hall.reaches("entrance")  # loud and crowded, and not where the visitor is
    assert not from_hall.reaches("bright-court")


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
