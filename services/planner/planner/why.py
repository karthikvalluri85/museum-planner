"""Deterministic "why this stop" sentences, built only from stored fields. Pure.

Each function returns (sentence, grounding) where grounding lists exactly the fields used.
"""

from __future__ import annotations

from typing import Any

from .models import Node
from .scoring import Candidate

MAX_TAGS_IN_SENTENCE = 3

Grounding = dict[str, Any]


def _grounding(fields: list[str], matched_tags: tuple[str, ...] = ()) -> Grounding:
    return {"matched_tags": list(matched_tags), "fields": fields}


def format_minutes(minutes: float) -> str:
    rounded = round(minutes, 1)
    return str(int(rounded)) if rounded == int(rounded) else str(rounded)


def object_why(candidate: Candidate, with_young_child: bool) -> tuple[str, Grounding]:
    obj = candidate.obj
    if candidate.is_match:
        tags = ", ".join(candidate.matched_tags[:MAX_TAGS_IN_SENTENCE])
        sentence = f"Matches your interest in {', '.join(candidate.matched_interests)}: tagged {tags}."
        fields = ["object.tags"]
        if obj.is_highlight:
            sentence += " One of the museum's highlights."
            fields.append("object.is_highlight")
        if with_young_child and obj.size_class in ("large", "monumental"):
            sentence += f" It is {obj.size_class}, easy for a child to see."
            fields.append("object.size_class")
        return sentence, _grounding(fields, candidate.matched_tags)

    first_tags = ", ".join(obj.tags[:MAX_TAGS_IN_SENTENCE])
    if obj.is_highlight:
        if obj.tags:
            return (
                f"One of the museum's highlights, tagged {first_tags}.",
                _grounding(["object.is_highlight", "object.tags"]),
            )
        return (
            f"One of the museum's highlights, from the {obj.department} department.",
            _grounding(["object.is_highlight", "object.department"]),
        )
    # Only reached for a museum with no highlights and a request with no interests.
    if obj.tags:
        return (
            f"On view in gallery {obj.gallery}, tagged {first_tags}.",
            _grounding(["object.gallery", "object.tags"]),
        )
    return (
        f"On view in gallery {obj.gallery}, from the {obj.department} department.",
        _grounding(["object.gallery", "object.department"]),
    )


def rest_why(node: Node, walked_since_rest: float) -> tuple[str, Grounding]:
    return (
        f"Seating in {node.name}. You have walked about {format_minutes(walked_since_rest)} "
        "minutes since your last rest.",
        _grounding(["node.has_seating"]),
    )


def amenity_why(node: Node) -> tuple[str, Grounding]:
    """The router only ends at nodes with a restroom, so near_restroom is always true here."""
    if node.near_cafe:
        return (
            f"Ends at {node.name}, next to a restroom and the cafe.",
            _grounding(["node.near_restroom", "node.near_cafe"]),
        )
    return f"Ends at {node.name}, next to a restroom.", _grounding(["node.near_restroom"])
