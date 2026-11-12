"""Interest matching and object scoring. Pure."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .graph import Graph
from .models import MuseumObject

MATCH_WEIGHT = 3.0
HIGHLIGHT_BONUS = 1.5
FILLER_SCORE = 0.5
CHILD_SIZE_BONUS = {"monumental": 2.0, "large": 1.0, "medium": 0.0, "small": -1.0}
CHILD_MAX_DWELL = 3

_WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class Candidate:
    obj: MuseumObject
    node_id: str
    wing: str
    score: float
    dwell: int
    matched_interests: tuple[str, ...]
    matched_tags: tuple[str, ...]

    @property
    def is_match(self) -> bool:
        return bool(self.matched_interests)


def normalize_interests(raw: Iterable[str]) -> list[str]:
    out: list[str] = []
    for item in raw:
        interest = " ".join(item.strip().lower().split())
        if interest and interest not in out:
            out.append(interest)
    return out


def _words(text: str) -> tuple[str, ...]:
    return tuple(
        w[:-1] if len(w) > 1 and w.endswith("s") else w for w in _WORD.findall(text.lower())
    )


def _contains(haystack: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    n = len(needle)
    return any(haystack[i : i + n] == needle for i in range(len(haystack) - n + 1))


def interest_matches_tag(interest: str, tag: str) -> bool:
    """Whole-word, plural-insensitive containment in either direction.

    "armor" matches "arms and armor", "cat" matches "cats", "art" does not match "martial".
    """
    a, b = _words(interest), _words(tag)
    return bool(a) and bool(b) and (_contains(b, a) or _contains(a, b))


def match_object(
    interests: Sequence[str], obj: MuseumObject
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Returns (interests that matched, object tags that matched), each in its own order."""
    matched_interests = tuple(
        i for i in interests if any(interest_matches_tag(i, t) for t in obj.tags)
    )
    matched_tags = tuple(
        t for t in obj.tags if any(interest_matches_tag(i, t) for i in interests)
    )
    return matched_interests, matched_tags


def can_describe(obj: MuseumObject) -> bool:
    """An unmatched object needs tags or a department for a grounded why sentence."""
    return bool(obj.tags) or bool(obj.department)


def routable_objects(objects: Iterable[MuseumObject], graph: Graph) -> list[MuseumObject]:
    """Objects whose gallery is in the floor graph. Everything else is never planned."""
    return [o for o in objects if o.gallery in graph.node_of_gallery]


def interests_matched(
    interests: Sequence[str], objects: Iterable[MuseumObject], graph: Graph
) -> list[str]:
    routable = routable_objects(objects, graph)
    return [
        i for i in interests if any(interest_matches_tag(i, t) for o in routable for t in o.tags)
    ]


def build_candidates(
    objects: Iterable[MuseumObject],
    graph: Graph,
    interests: Sequence[str],
    *,
    exclude_ids: Iterable[str] = (),
    skip_wings: Iterable[str] = (),
    child: bool = False,
) -> list[Candidate]:
    excluded = set(exclude_ids)
    skipped = {w.strip().casefold() for w in skip_wings}
    routable = sorted(routable_objects(objects, graph), key=lambda o: o.id)
    any_highlight = any(o.is_highlight for o in routable)

    out: list[Candidate] = []
    for obj in routable:
        node = graph.nodes[graph.node_of_gallery[obj.gallery]]
        if obj.id in excluded or node.wing.casefold() in skipped:
            continue
        matched_interests, matched_tags = match_object(interests, obj)
        if matched_interests:
            score = MATCH_WEIGHT * len(matched_interests)
            score += HIGHLIGHT_BONUS if obj.is_highlight else 0.0
        elif not can_describe(obj):
            continue
        elif interests:
            if not obj.is_highlight:
                continue
            score = FILLER_SCORE
        elif any_highlight:
            if not obj.is_highlight:
                continue
            score = HIGHLIGHT_BONUS
        else:
            # No interests and a museum with no highlights at all: everything is eligible.
            score = 0.0
        dwell = obj.dwell_minutes
        if child:
            score += CHILD_SIZE_BONUS[obj.size_class]
            dwell = min(dwell, CHILD_MAX_DWELL)
        out.append(
            Candidate(
                obj=obj,
                node_id=node.id,
                wing=node.wing,
                score=score,
                dwell=dwell,
                matched_interests=matched_interests,
                matched_tags=matched_tags,
            )
        )
    return out
