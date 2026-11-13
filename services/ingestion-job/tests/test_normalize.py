import pytest

from ingestion.models import NormalizedObject
from ingestion.normalize import (
    Skip,
    build_facets,
    build_object,
    chunk_objects,
    clean_tags,
    dwell_minutes,
    largest_cm_in_text,
    order_objects,
    size_class,
    strip_html,
    tag_label,
)


def make(source_id: str, *, highlight: bool = False, tags: tuple[str, ...] = ("x",), gallery: str = "100") -> NormalizedObject:
    result = build_object(
        museum_id="test", source_id=source_id, title=f"Title {source_id}", gallery=gallery, tags=tags,
        is_highlight=highlight,
    )
    assert isinstance(result, NormalizedObject)
    return result


def test_clean_tags_lowercases_trims_dedupes_and_keeps_order():
    assert clean_tags(["  Oil Paint ", "", None, "oil paint", "Landscapes", "   "]) == ["oil paint", "landscapes"]


def test_clean_tags_caps_at_25():
    tags = clean_tags([f"tag{i}" for i in range(40)])
    assert len(tags) == 25
    assert tags[0] == "tag0" and tags[-1] == "tag24"


@pytest.mark.parametrize(
    ("max_cm", "expected"),
    [(None, "medium"), (0.5, "small"), (29.9, "small"), (30, "medium"), (99.9, "medium"),
     (100, "large"), (249.9, "large"), (250, "monumental"), (900, "monumental")],
)
def test_size_class_boundaries(max_cm, expected):
    assert size_class(max_cm) == expected


@pytest.mark.parametrize(
    ("size", "highlight", "expected"),
    [("small", False, 2), ("medium", False, 3), ("large", False, 4), ("monumental", False, 6),
     ("small", True, 4), ("monumental", True, 8)],
)
def test_dwell_minutes(size, highlight, expected):
    assert dwell_minutes(size, highlight) == expected


def test_largest_cm_in_text():
    assert largest_cm_in_text("207.5 × 308.1 cm (81 11/16 × 121 5/16 in.)") == 308.1
    assert largest_cm_in_text("52 1/4 x 63 5/8 in. (132.7 x 161.6 cm)") == 161.6
    assert largest_cm_in_text("73.6 × 92.3 cm (29 × 36 5/8 in.); Framed: 120 × 140 × 9 cm") == 92.3
    assert largest_cm_in_text("12 in.") is None
    assert largest_cm_in_text(None) is None


def test_strip_html():
    assert strip_html("<p>Hello <em>world</em> &amp; more.</p>") == "Hello world & more."
    assert strip_html("<p> </p>") is None
    assert strip_html(None) is None


def test_ordering_highlights_first_then_numeric_id():
    objs = [make("100"), make("9"), make("20", highlight=True), make("3", highlight=True), make("abc")]
    assert [o.source_id for o in order_objects(objs)] == ["3", "20", "9", "100", "abc"]


def test_chunking():
    objs = [make(str(i)) for i in range(7)]
    chunks = chunk_objects(objs, 3)
    assert [len(c) for c in chunks] == [3, 3, 1]
    assert chunk_objects([], 3) == []


def test_facets_use_min_count_two_when_ten_tags_repeat():
    objs = []
    for i in range(10):
        objs.append(make(str(i), tags=(f"common{i}", "shared", "lonely" + str(i))))
        objs.append(make(str(100 + i), tags=(f"common{i}",)))
    facets = build_facets(objs)
    assert facets[0].tag == "shared" and facets[0].count == 10
    assert all(f.count >= 2 for f in facets)
    assert not any(f.tag.startswith("lonely") for f in facets)


def test_facets_keep_singletons_when_few_tags_repeat():
    facets = build_facets([make("1", tags=("b", "a")), make("2", tags=("a", "c"))])
    assert [(f.tag, f.count) for f in facets] == [("a", 2), ("b", 1), ("c", 1)]


def test_facets_count_only_interest_tags_when_given():
    objs = [
        make("1", tags=("landscapes", "oil on canvas", "impressionism")),
        make("2", tags=("landscapes", "oil on canvas")),
        make("3", tags=("oil on canvas", "portraits")),
    ]
    interest = {"test:1": {"landscapes", "impressionism"}, "test:2": {"landscapes"}, "test:3": {"portraits", "not on the object"}}
    facets = build_facets(objs, interest)
    assert [(f.tag, f.count) for f in facets] == [("landscapes", 2), ("impressionism", 1), ("portraits", 1)]
    # Without the filter the material would have been the top chip.
    assert build_facets(objs)[0].tag == "oil on canvas"


def test_facets_capped_at_40_and_labelled():
    objs = [make("1", tags=tuple(f"tag{i:02d}" for i in range(25))), make("2", tags=tuple(f"zag{i:02d}" for i in range(25)))]
    assert len(build_facets(objs)) == 40
    assert tag_label("arms and armor") == "Arms and Armor"
    assert tag_label("men's wear") == "Men's Wear"


def test_build_object_skips_missing_gallery_and_blank_title():
    assert build_object(museum_id="test", source_id="1", title="T", gallery="") is Skip.NO_GALLERY
    assert build_object(museum_id="test", source_id="1", title="T", gallery="bad/gallery") is Skip.NO_GALLERY
    assert build_object(museum_id="test", source_id="1", title="  ", gallery="100") is Skip.INVALID


def test_build_object_normalizes_blank_strings_to_none():
    obj = build_object(museum_id="test", source_id="1", title=" T ", gallery="100", artist=" ", date="", medium="Oil")
    assert isinstance(obj, NormalizedObject)
    assert (obj.title, obj.artist, obj.date, obj.medium) == ("T", None, None, "Oil")
