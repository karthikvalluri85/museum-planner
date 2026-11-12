"""Floor graph routing: Dijkstra on walking minutes, with step-free and sensory filters. Pure."""

from __future__ import annotations

import heapq
from dataclasses import dataclass

from .models import Edge, FloorGraph, Node

EPS = 1e-9


@dataclass(frozen=True)
class Segment:
    """One edge as travelled: from_id -> to_id in the direction of travel."""

    from_id: str
    to_id: str
    edge: Edge


@dataclass(frozen=True)
class RouteMode:
    step_free: bool
    avoid_sensory: bool
    # The visitor is already standing here, so it is allowed even if it is loud or bright.
    start: str


@dataclass(frozen=True)
class ShortestPaths:
    source: str
    dist: dict[str, float]
    prev: dict[str, Segment]

    def reaches(self, node_id: str) -> bool:
        return node_id in self.dist

    def path_to(self, node_id: str) -> list[Segment]:
        if node_id not in self.dist:
            raise KeyError(f"{node_id} is not reachable from {self.source}")
        path: list[Segment] = []
        current = node_id
        while current != self.source:
            segment = self.prev[current]
            path.append(segment)
            current = segment.from_id
        path.reverse()
        return path


class Graph:
    def __init__(self, floor_graph: FloorGraph) -> None:
        self.floor_graph = floor_graph
        self.nodes: dict[str, Node] = {n.id: n for n in floor_graph.nodes}
        self.entrance = floor_graph.entrance_node_id
        self.node_of_gallery: dict[str, str] = {
            g: n.id for n in floor_graph.nodes for g in n.galleries
        }
        self._adjacent: dict[str, list[tuple[str, Edge]]] = {nid: [] for nid in self.nodes}
        for edge in floor_graph.edges:
            self._adjacent[edge.from_].append((edge.to, edge))
            self._adjacent[edge.to].append((edge.from_, edge))
        # Cheapest parallel edge first, so strict-improvement relaxation keeps it; the rest
        # of the key only makes ties deterministic.
        for neighbours in self._adjacent.values():
            neighbours.sort(
                key=lambda t: (t[0], t[1].walk_minutes, not t[1].step_free, t[1].name or "")
            )

    def allows_node(self, node_id: str, mode: RouteMode) -> bool:
        if node_id == mode.start or not mode.avoid_sensory:
            return True
        return not self.nodes[node_id].sensory

    def shortest_paths(self, source: str, mode: RouteMode) -> ShortestPaths:
        dist: dict[str, float] = {source: 0.0}
        prev: dict[str, Segment] = {}
        done: set[str] = set()
        heap: list[tuple[float, str]] = [(0.0, source)]
        while heap:
            d, node_id = heapq.heappop(heap)
            if node_id in done:
                continue
            done.add(node_id)
            for neighbour, edge in self._adjacent[node_id]:
                if mode.step_free and not edge.step_free:
                    continue
                if not self.allows_node(neighbour, mode):
                    continue
                candidate = d + edge.walk_minutes
                if neighbour not in dist or candidate < dist[neighbour] - EPS:
                    dist[neighbour] = candidate
                    prev[neighbour] = Segment(node_id, neighbour, edge)
                    heapq.heappush(heap, (candidate, neighbour))
        return ShortestPaths(source, dist, prev)


class PathCache:
    """Memoizes shortest paths per source for one planning run (one RouteMode)."""

    def __init__(self, graph: Graph, mode: RouteMode) -> None:
        self.graph = graph
        self.mode = mode
        self._by_source: dict[str, ShortestPaths] = {}

    def from_(self, source: str) -> ShortestPaths:
        if source not in self._by_source:
            self._by_source[source] = self.graph.shortest_paths(source, self.mode)
        return self._by_source[source]
