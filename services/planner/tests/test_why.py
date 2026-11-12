from __future__ import annotations

from planner.scoring import build_candidates
from planner.why import amenity_why, format_minutes, object_why, rest_why


def candidate(data, short_id, interests, child=False):
    cands = build_candidates(data.objects, data.graph, interests, child=child)
    return next(c for c in cands if c.obj.id == f"testmuseum:{short_id}")


def test_matched_why_uses_only_matched_tags_and_flags(data):
    why, grounding = object_why(candidate(data, "t05", ["animals", "armor"], child=True), with_young_child=True)
    assert why == (
        "Matches your interest in animals, armor: tagged arms and armor, animals."
        " One of the museum's highlights. It is monumental, easy for a child to see."
    )
    assert grounding == {
        "matched_tags": ["arms and armor", "animals"],
        "fields": ["object.tags", "object.is_highlight", "object.size_class"],
    }


def test_matched_why_without_highlight_or_child(data):
    why, grounding = object_why(candidate(data, "t02", ["armor"]), with_young_child=False)
    assert why == "Matches your interest in armor: tagged arms and armor."
    assert grounding == {"matched_tags": ["arms and armor"], "fields": ["object.tags"]}


def test_small_object_gets_no_size_sentence_for_child(data):
    why, grounding = object_why(candidate(data, "t07", ["cats"], child=True), with_young_child=True)
    assert "child" not in why
    assert "object.size_class" not in grounding["fields"]


def test_filler_highlight_why_from_tags(data):
    why, grounding = object_why(candidate(data, "t13", ["armor"]), with_young_child=False)
    assert why == "One of the museum's highlights, tagged ceramics, vessels."
    assert grounding == {"matched_tags": [], "fields": ["object.is_highlight", "object.tags"]}


def test_filler_highlight_why_from_department(data):
    why, grounding = object_why(candidate(data, "t30", []), with_young_child=False)
    assert why == "One of the museum's highlights, from the Modern Art department."
    assert grounding == {"matched_tags": [], "fields": ["object.is_highlight", "object.department"]}


def test_every_matched_tag_in_sentence_is_a_real_tag(data):
    for c in build_candidates(data.objects, data.graph, ["animals", "armor", "ceramics", "cat"]):
        _, grounding = object_why(c, with_young_child=False)
        assert set(grounding["matched_tags"]) <= set(c.obj.tags)
        assert grounding["fields"]


def test_rest_why(data):
    why, grounding = rest_why(data.graph.nodes["west-1"], 5.0)
    assert why == "Seating in West Galleries 111-112. You have walked about 5 minutes since your last rest."
    assert grounding == {"matched_tags": [], "fields": ["node.has_seating"]}


def test_amenity_why_names_what_is_there(data):
    why, grounding = amenity_why(data.graph.nodes["cafe"])
    assert "restroom" in why and "cafe" in why
    assert grounding["fields"] == ["node.near_restroom", "node.near_cafe"]
    why, grounding = amenity_why(data.graph.nodes["entrance"])
    assert "restroom" in why and "cafe" not in why
    assert grounding["fields"] == ["node.near_restroom"]


def test_format_minutes():
    assert format_minutes(5.0) == "5"
    assert format_minutes(6.5) == "6.5"
