from __future__ import annotations

import pytest

from planner.scoring import (
    build_candidates,
    interest_matches_tag,
    interests_matched,
    match_object,
    normalize_interests,
)

from .helpers import UNMAPPED_OBJECT_IDS


def by_id(candidates):
    return {c.obj.id: c for c in candidates}


def obj(data, short_id):
    return next(o for o in data.objects if o.id == f"testmuseum:{short_id}")


def test_normalize_interests_trims_lowercases_and_dedupes():
    assert normalize_interests(["  Armor ", "armor", "   ", "Arms  And Armor"]) == ["armor", "arms and armor"]


@pytest.mark.parametrize(
    ("interest", "tag", "expected"),
    [
        ("armor", "arms and armor", True),
        ("arms", "arms and armor", True),
        ("arms and armor", "armor", True),
        ("cat", "cats", True),
        ("cats", "cat", True),
        ("art", "egyptian art", True),
        ("art", "martial", False),
        ("music", "musical instruments", False),
        ("horse", "seahorses", False),
        ("and armor", "arms and armor", True),
        ("armor arms", "arms and armor", False),
        ("!!!", "armor", False),
    ],
)
def test_interest_matches_tag(interest, tag, expected):
    assert interest_matches_tag(interest, tag) is expected


def test_match_object_keeps_object_tag_order(data):
    matched_interests, matched_tags = match_object(["animals", "armor", "dinosaurs"], obj(data, "t05"))
    assert matched_interests == ("animals", "armor")
    assert matched_tags == ("arms and armor", "animals")


def test_art_does_not_match_martial(data):
    assert match_object(["art"], obj(data, "t04")) == ((), ())
    assert interests_matched(["art"], data.objects, data.graph) == []


def test_interests_matched_counts_only_routable_objects(data):
    assert interests_matched(["armor", "astronomy", "dinosaurs"], data.objects, data.graph) == ["armor", "astronomy"]


def test_scores_with_interests(data):
    cands = by_id(build_candidates(data.objects, data.graph, ["armor"]))
    assert cands["testmuseum:t01"].score == 4.5  # 3 per interest + 1.5 highlight
    assert cands["testmuseum:t02"].score == 3.0
    assert cands["testmuseum:t07"].score == 0.5  # unmatched highlight: filler
    assert cands["testmuseum:t30"].score == 0.5  # no tags, but a department: still describable
    assert "testmuseum:t31" not in cands  # no tags, no department: no grounded why possible
    assert "testmuseum:t04" not in cands  # unmatched and not a highlight
    assert not UNMAPPED_OBJECT_IDS & cands.keys()


def test_two_interests_score_double(data):
    cands = by_id(build_candidates(data.objects, data.graph, ["animals", "armor"]))
    assert cands["testmuseum:t05"].score == 3 * 2 + 1.5


def test_no_interests_uses_highlights_only(data):
    cands = by_id(build_candidates(data.objects, data.graph, []))
    expected = {f"testmuseum:{i}" for i in ("t01", "t05", "t07", "t09", "t12", "t13", "t23", "t29", "t30")}
    assert set(cands) == expected
    assert {c.score for c in cands.values()} == {1.5}


def test_young_child_size_bonus_and_short_dwell(data):
    cands = by_id(build_candidates(data.objects, data.graph, ["armor"], child=True))
    assert cands["testmuseum:t05"].score == 4.5 + 2  # monumental
    assert cands["testmuseum:t02"].score == 3 + 1  # large
    assert cands["testmuseum:t06"].score == 3 - 1  # small
    assert cands["testmuseum:t01"].dwell == 3  # stored 6
    assert cands["testmuseum:t06"].dwell == 2  # stored 2, already short


def test_exclusions_and_skipped_wings(data):
    cands = by_id(
        build_candidates(
            data.objects,
            data.graph,
            ["animals"],
            exclude_ids=["testmuseum:t09"],
            skip_wings=["  EAST wing "],
        )
    )
    assert "testmuseum:t09" not in cands
    assert all(c.wing != "East Wing" for c in cands.values())
    assert "testmuseum:t10" in cands


def test_candidates_are_sorted_and_deterministic(data):
    first = [c.obj.id for c in build_candidates(data.objects, data.graph, ["animals"])]
    second = [c.obj.id for c in build_candidates(data.objects, data.graph, ["animals"])]
    assert first == second == sorted(first)
