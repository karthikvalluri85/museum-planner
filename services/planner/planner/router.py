"""Greedy visit routing over scored candidates. Pure.

Not optimal on purpose: from where the visitor stands, repeatedly take the best-value
feasible object, where value is score minus a penalty per walking minute.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from .graph import EPS, Graph, PathCache, RouteMode, Segment, ShortestPaths
from .scoring import Candidate

AMENITY_DWELL = 5
REST_DWELL = 5
WING_TOP_N = 6
WING_WALK_PENALTY = 0.2
STAMINA_PENALTY = 0.4
DEFAULT_PENALTY = 0.15
MAX_STOPS_PER_NODE = 3
MAX_STOPS_PER_NODE_SHORT = 4


@dataclass(frozen=True)
class RouteSettings:
    budget_minutes: int
    walking_cap: float | None
    limited_stamina: bool
    with_young_child: bool
    short_visit: bool


@dataclass(frozen=True)
class Visit:
    kind: Literal["object", "rest", "amenity"]
    node_id: str
    dwell: int
    leg: tuple[Segment, ...]  # walk from the previous visit (or the start) to this one
    candidate: Candidate | None = None
    walked_since_rest: float = 0.0  # only meaningful for rest visits


@dataclass
class Route:
    start: str
    visits: list[Visit] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def object_visits(self) -> list[Visit]:
        return [v for v in self.visits if v.kind == "object"]


def leg_minutes(minutes: float) -> float:
    """Walking minutes for one leg as the response reports them; the budget uses the same value."""
    return round(minutes, 1)


def _key(value: float) -> float:
    # Rounded so float noise never decides a tie.
    return round(value, 6)


def nearest_amenity(graph: Graph, paths: ShortestPaths) -> str | None:
    """Nearest reachable node with restroom and cafe, else nearest with a restroom."""
    reachable = [graph.nodes[nid] for nid in paths.dist]
    for wanted in (lambda n: n.near_restroom and n.near_cafe, lambda n: n.near_restroom):
        options = [n.id for n in reachable if wanted(n)]
        if options:
            return min(options, key=lambda nid: (_key(paths.dist[nid]), nid))
    return None


def choose_wing(candidates: Sequence[Candidate], paths: ShortestPaths) -> str | None:
    by_wing: dict[str, list[Candidate]] = {}
    for c in candidates:
        if paths.reaches(c.node_id):
            by_wing.setdefault(c.wing, []).append(c)
    if not by_wing:
        return None

    def value(wing: str) -> float:
        members = by_wing[wing]
        top = sorted((c.score for c in members), reverse=True)[:WING_TOP_N]
        nearest = min(paths.dist[c.node_id] for c in members)
        return sum(top) - WING_WALK_PENALTY * nearest

    return min(by_wing, key=lambda w: (-_key(value(w)), w))


def plan_route(
    graph: Graph,
    candidates: Sequence[Candidate],
    *,
    start: str,
    mode: RouteMode,
    settings: RouteSettings,
) -> Route:
    paths = PathCache(graph, mode)
    route = Route(start=start)

    pool = list(candidates)
    if settings.short_visit:
        wing = choose_wing(pool, paths.from_(start))
        pool = [c for c in pool if c.wing == wing]

    def ending(node_id: str) -> tuple[float, float]:
        """(walk to the child-friendly ending, minutes to keep free for it) after node_id."""
        if not settings.with_young_child:
            return 0.0, 0.0
        from_node = paths.from_(node_id)
        target = nearest_amenity(graph, from_node)
        if target is None:
            return 0.0, 0.0
        walk = leg_minutes(from_node.dist[target])
        return walk, walk + AMENITY_DWELL

    penalty = STAMINA_PENALTY if settings.limited_stamina else DEFAULT_PENALTY
    per_node_limit = MAX_STOPS_PER_NODE_SHORT if settings.short_visit else MAX_STOPS_PER_NODE
    cap = settings.walking_cap
    rest_after = max(4.0, cap / 3) if cap is not None else None

    current = start
    remaining = float(settings.budget_minutes)
    walked = 0.0
    since_rest = 0.0
    used: set[str] = set()
    per_node: Counter[str] = Counter()

    while True:
        here = paths.from_(current)
        best: tuple[tuple[float, float, str], Candidate, float] | None = None
        for c in pool:
            if c.obj.id in used or per_node[c.node_id] >= per_node_limit:
                continue
            if not here.reaches(c.node_id):
                continue
            walk = leg_minutes(here.dist[c.node_id])
            ending_walk, reserve = ending(c.node_id)
            if walk + c.dwell + reserve > remaining + EPS:
                continue
            # The ending walk counts against the cap too, so the child ending never breaks it.
            if cap is not None and walked + walk + ending_walk > cap + EPS:
                continue
            key = (-_key(c.score - penalty * walk), _key(walk), c.obj.id)
            if best is None or key < best[0]:
                best = (key, c, walk)
        if best is None:
            break

        _, chosen, walk = best
        route.visits.append(
            Visit("object", chosen.node_id, chosen.dwell, tuple(here.path_to(chosen.node_id)), chosen)
        )
        used.add(chosen.obj.id)
        per_node[chosen.node_id] += 1
        remaining -= walk + chosen.dwell
        walked += walk
        since_rest += walk
        current = chosen.node_id

        if (
            settings.limited_stamina
            and rest_after is not None
            and since_rest >= rest_after - EPS
            and graph.nodes[current].has_seating
            and remaining - ending(current)[1] >= REST_DWELL - EPS
        ):
            route.visits.append(
                Visit("rest", current, REST_DWELL, (), walked_since_rest=since_rest)
            )
            remaining -= REST_DWELL
            since_rest = 0.0

    if settings.with_young_child and route.object_visits:
        here = paths.from_(current)
        target = nearest_amenity(graph, here)
        if target is None:
            route.warnings.append(
                "No restroom is reachable from the last stop with your constraints. "
                "Ask museum staff for the nearest one."
            )
        else:
            route.visits.append(
                Visit("amenity", target, AMENITY_DWELL, tuple(here.path_to(target)))
            )
    return route
