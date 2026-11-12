"""Builds API responses: museum listings, museum detail, and plans (with step-free orchestration)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .graph import Graph, RouteMode, Segment
from .models import Constraints, MuseumObject, PlanRequest
from .router import Route, RouteSettings, leg_minutes, plan_route
from .scoring import Candidate, build_candidates, interests_matched, normalize_interests
from .store import MuseumData, MuseumRepository
from .why import amenity_why, object_why, rest_why

DEFAULT_WALKING_CAP = 15
SHORT_VISIT_MAX_BUDGET = 60
MAX_STAIRS_NAMED_IN_WARNING = 3

Json = dict[str, Any]


class UnknownGallery(Exception):
    pass


class PlanIntegrityError(Exception):
    """A plan was about to make a claim its own segments do not support."""


# ---- museums ----------------------------------------------------------------


def _summary(data: MuseumData) -> Json:
    m = data.museum
    return {
        "id": m.id,
        "name": m.name,
        "city": m.city,
        "object_count": m.object_count,
        "last_ingested_at": m.last_ingested_at,
    }


def list_museums(repo: MuseumRepository) -> Json:
    return {"museums": [_summary(d) for d in repo.list_available()]}


def museum_detail(data: MuseumData) -> Json:
    verification = data.floor_graph.verification
    wings = sorted({n.wing for n in data.floor_graph.nodes if n.kind == "gallery"})
    return {
        **_summary(data),
        "facets": [f.model_dump() for f in data.museum.facets],
        "wings": wings,
        "data_notice": data.museum.data_notice,
        "floor_data": {
            "verification_status": verification.status,
            "source": data.floor_graph.source,
            "notes": verification.notes,
        },
    }


PUBLIC_DATA_NOTICE = "Built from the museum's public data; the museum has not reviewed this plan."


def notices(data: MuseumData) -> list[str]:
    name = data.museum.name
    verification = data.floor_graph.verification
    mapped = (
        f"Floor layout, elevators, and stairs for {name} were mapped by hand from its "
        "published floor plan"
    )
    if verification.status == "verified":
        when = f" on {verification.verified_on}" if verification.verified_on else ""
        floor = f"{mapped} and were checked on site{when}. Confirm step-free access with museum staff if anything has changed."
    elif verification.status == "partially_verified":
        floor = f"{mapped} and have been only partly checked on site. Confirm step-free access with museum staff."
    else:
        floor = f"{mapped} and have not been checked on site. Confirm step-free access with museum staff."
    out = [data.museum.data_notice]
    lowered = data.museum.data_notice.lower()
    # The public-data statement is a product guarantee; never depend on stored wording for it.
    if "public data" not in lowered or "not reviewed" not in lowered:
        out.append(PUBLIC_DATA_NOTICE)
    out.append(floor)
    return out


# ---- plan rendering -----------------------------------------------------------


def _segment(graph: Graph, seg: Segment) -> Json:
    return {
        "from_node_id": seg.from_id,
        "to_node_id": seg.to_id,
        "from_name": graph.nodes[seg.from_id].name,
        "to_name": graph.nodes[seg.to_id].name,
        "walk_minutes": seg.edge.walk_minutes,
        "step_free": seg.edge.step_free,
        "kind": seg.edge.kind,
        "name": seg.edge.name,
    }


def _leg_minutes(leg: Sequence[Segment]) -> float:
    return leg_minutes(sum(s.edge.walk_minutes for s in leg))


def _object(obj: MuseumObject) -> Json:
    return {
        "id": obj.id,
        "title": obj.title,
        "artist": obj.artist,
        "date": obj.date,
        "image_url": obj.image_url,
        "source_url": obj.source_url,
        "tags": list(obj.tags),
        "size_class": obj.size_class,
        "is_highlight": obj.is_highlight,
    }


def _stairs(segments: Sequence[Json]) -> list[Json]:
    out: list[Json] = []
    seen: set[tuple[str, frozenset[str]]] = set()
    for s in segments:
        if s["step_free"]:
            continue
        key = (s["name"], frozenset((s["from_node_id"], s["to_node_id"])))
        if key in seen:
            continue
        seen.add(key)
        out.append({k: s[k] for k in ("name", "kind", "from_node_id", "to_node_id", "from_name", "to_name")})
    return out


def render_plan(graph: Graph, route: Route, budget: int, with_young_child: bool) -> Json:
    visits = route.visits
    stops: list[Json] = []
    for i, visit in enumerate(visits):
        node = graph.nodes[visit.node_id]
        following = visits[i + 1] if i + 1 < len(visits) else None
        if visit.kind == "object":
            assert visit.candidate is not None
            why, grounding = object_why(visit.candidate, with_young_child)
        elif visit.kind == "rest":
            why, grounding = rest_why(node, visit.walked_since_rest)
        else:
            why, grounding = amenity_why(node)
        obj = visit.candidate.obj if visit.candidate else None
        stops.append(
            {
                "order": i + 1,
                "kind": visit.kind,
                "gallery": obj.gallery if obj else None,
                "node_id": node.id,
                "node_name": node.name,
                "floor": node.floor,
                "wing": node.wing,
                "object": _object(obj) if obj else None,
                "why": why,
                "grounding": grounding,
                "dwell_minutes": visit.dwell,
                "walk_minutes_to_next": _leg_minutes(following.leg) if following else None,
                "route_to_next": [_segment(graph, s) for s in following.leg] if following else [],
            }
        )

    start_node = graph.nodes[route.start]
    start = {
        "node_id": start_node.id,
        "node_name": start_node.name,
        "walk_minutes_to_first": _leg_minutes(visits[0].leg),
        "route_to_first": [_segment(graph, s) for s in visits[0].leg],
    }
    segments = start["route_to_first"] + [s for stop in stops for s in stop["route_to_next"]]
    dwell = sum(stop["dwell_minutes"] for stop in stops)
    walking = round(
        start["walk_minutes_to_first"] + sum(stop["walk_minutes_to_next"] or 0 for stop in stops), 1
    )
    wings: list[str] = []
    for stop in stops:
        if stop["kind"] == "object" and stop["wing"] not in wings:
            wings.append(stop["wing"])
    plan = {
        "start": start,
        "stops": stops,
        "totals": {
            "dwell_minutes": dwell,
            "walking_minutes": walking,
            "total_minutes": round(dwell + walking, 1),
            "time_budget_minutes": budget,
        },
        "step_free": all(s["step_free"] for s in segments),
        "stairs": _stairs(segments),
        "wings": wings,
        "warnings": list(route.warnings),
    }
    check_plan_integrity(plan)
    return plan


def _plan_segments(plan: Json) -> list[Json]:
    return plan["start"]["route_to_first"] + [s for stop in plan["stops"] for s in stop["route_to_next"]]


def check_plan_integrity(plan: Json) -> None:
    """I1 and the step-free/stairs claims, asserted on every plan before it leaves the service."""
    segments = _plan_segments(plan)
    stepped = [s for s in segments if not s["step_free"]]
    if plan["step_free"] != (not stepped):
        raise PlanIntegrityError("plan.step_free disagrees with its segments")
    if plan["step_free"] and plan["stairs"]:
        raise PlanIntegrityError("a step-free plan lists stairs")
    named = {(st["name"], frozenset((st["from_node_id"], st["to_node_id"]))) for st in plan["stairs"]}
    for s in stepped:
        if not s["name"] or (s["name"], frozenset((s["from_node_id"], s["to_node_id"]))) not in named:
            raise PlanIntegrityError("a non-step-free segment is not named in plan.stairs")
    if plan["totals"]["total_minutes"] > plan["totals"]["time_budget_minutes"] + 0.05:
        raise PlanIntegrityError("plan exceeds the time budget")


def check_response_integrity(response: Json, step_free_requested: bool) -> None:
    """I2 and I3: a step-free request never gets an ok plan that uses stairs."""
    status = response["status"]
    if status == "ok":
        if response["plan"] is None:
            raise PlanIntegrityError("status ok without a plan")
        if step_free_requested and not response["plan"]["step_free"]:
            raise PlanIntegrityError("step-free requested but the ok plan uses stairs")
    elif status == "no_step_free_route":
        alternative = response["alternative"]
        if response["plan"] is not None or alternative is None or not alternative["stairs"]:
            raise PlanIntegrityError("no_step_free_route must carry an alternative that names its stairs")


# ---- planning -------------------------------------------------------------------


def _start_node(graph: Graph, current_gallery: str | None) -> str:
    if current_gallery is None:
        return graph.entrance
    node_id = graph.node_of_gallery.get(current_gallery)
    if node_id is None:
        raise UnknownGallery(current_gallery)
    return node_id


def _applied_constraints(c: Constraints, budget: int) -> Json:
    return {
        "step_free": c.step_free,
        "limited_stamina": c.limited_stamina,
        "max_walking_minutes": (c.max_walking_minutes or DEFAULT_WALKING_CAP) if c.limited_stamina else None,
        "with_young_child": c.with_young_child,
        "low_sensory": c.low_sensory,
        "short_visit": c.short_visit or budget <= SHORT_VISIT_MAX_BUDGET,
    }


def _quoted(items: Sequence[str]) -> str:
    quoted = [f'"{i}"' for i in items]
    return quoted[0] if len(quoted) == 1 else ", ".join(quoted[:-1]) + " or " + quoted[-1]


def _and_join(items: Sequence[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _no_step_free_message(alternative: Json, reaches_matches: bool) -> str:
    target = "anything matching your interests" if reaches_matches else "the museum's highlights"
    stairs = _and_join(
        [f"the {s['name']} between {s['from_name']} and {s['to_name']}" for s in alternative["stairs"]]
    )
    return f"No step-free route reaches {target}. The closest alternative uses {stairs}."


def _stairs_only_warning(
    graph: Graph, candidates: Sequence[Candidate], start: str, low_sensory: bool
) -> str | None:
    """Names the stairs that keep interest-matched objects out of a step-free plan."""
    step_free = graph.shortest_paths(start, RouteMode(True, low_sensory, start))
    any_access = graph.shortest_paths(start, RouteMode(False, low_sensory, start))
    left_out = [
        c for c in candidates
        if c.is_match and not step_free.reaches(c.node_id) and any_access.reaches(c.node_id)
    ]
    if not left_out:
        return None
    names: list[str] = []
    for node_id in sorted({c.node_id for c in left_out}):
        for seg in any_access.path_to(node_id):
            if not seg.edge.step_free and seg.edge.name not in names:
                names.append(seg.edge.name)
    names = names[:MAX_STAIRS_NAMED_IN_WARNING]
    n = len(left_out)
    noun, verb = ("object", "was") if n == 1 else ("objects", "were")
    return (
        f"{n} {noun} matching your interests can only be reached by stairs "
        f"({', '.join(names)}) and {verb} left out."
    )


def _no_plan_message(
    data: MuseumData,
    request: PlanRequest,
    interests: Sequence[str],
    matched: Sequence[str],
    candidates: Sequence[Candidate],
    child: bool,
) -> str:
    parts: list[str] = []
    if interests and not matched:
        parts.append(f"Nothing on view matches {_quoted(interests)}.")
    if candidates:
        parts.append(
            f"Nothing fits in the {request.time_budget_minutes} minutes you have with these "
            "constraints. Try more time or fewer constraints."
        )
    elif build_candidates(data.objects, data.graph, interests, child=child):
        parts.append(
            "Everything that fits your request is already seen or in a wing you chose to skip. "
            "Try bringing back a skipped wing."
        )
    else:
        parts.append("This museum has no objects on view that fit your request. Try a different interest.")
    return " ".join(parts)


def create_plan(data: MuseumData, request: PlanRequest) -> Json:
    graph = data.graph
    c = request.constraints
    budget = request.time_budget_minutes
    interests = normalize_interests(request.interests)
    matched = interests_matched(interests, data.objects, graph)
    start = _start_node(graph, request.current_gallery)
    applied = _applied_constraints(c, budget)

    candidates = build_candidates(
        data.objects,
        graph,
        interests,
        exclude_ids=request.exclude_object_ids,
        skip_wings=request.skip_wings,
        child=c.with_young_child,
    )
    settings = RouteSettings(
        budget_minutes=budget,
        walking_cap=applied["max_walking_minutes"],
        limited_stamina=c.limited_stamina,
        with_young_child=c.with_young_child,
        short_visit=applied["short_visit"],
    )

    def run(step_free: bool) -> Route:
        mode = RouteMode(step_free=step_free, avoid_sensory=c.low_sensory, start=start)
        return plan_route(graph, candidates, start=start, mode=mode, settings=settings)

    def render(route: Route) -> Json | None:
        if not route.object_visits:
            return None
        return render_plan(graph, route, budget, c.with_young_child)

    def has_match(route: Route) -> bool:
        return any(v.candidate is not None and v.candidate.is_match for v in route.object_visits)

    any_match = any(cand.is_match for cand in candidates)
    status, plan, alternative, message = "no_plannable_objects", None, None, None

    if not c.step_free:
        plan = render(run(False))
    else:
        sf_route = run(True)
        sf_plan = render(sf_route)
        if sf_plan is not None:
            warning = _stairs_only_warning(graph, candidates, start, c.low_sensory)
            if warning:
                sf_plan["warnings"].append(warning)
        if sf_plan is not None and (has_match(sf_route) or not any_match):
            plan = sf_plan
        else:
            # Nothing worth visiting step-free: see what the stairs would open up.
            free_route = run(False)
            free_plan = render(free_route)
            better = free_plan is not None and (has_match(free_route) or sf_plan is None)
            if better and not free_plan["step_free"]:
                status, alternative = "no_step_free_route", free_plan
                message = _no_step_free_message(free_plan, has_match(free_route))
            elif better:
                plan = free_plan  # it turned out step-free after all
            else:
                plan = sf_plan

    if plan is not None:
        status = "ok"
    elif status == "no_plannable_objects":
        message = _no_plan_message(data, request, interests, matched, candidates, c.with_young_child)

    response = {
        "status": status,
        "museum": {"id": data.museum.id, "name": data.museum.name},
        "plan": plan,
        "alternative": alternative,
        "message": message,
        "applied": {
            "constraints": applied,
            "interests_matched": matched,
            "interests_unmatched": [i for i in interests if i not in matched],
        },
        "notices": notices(data),
    }
    check_response_integrity(response, c.step_free)
    return response
