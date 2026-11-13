import json

import pytest

from ingestion.adapters.aic import AicAdapter
from ingestion.adapters.met import MetAdapter
from ingestion.models import NormalizedObject
from ingestion.normalize import Skip

MET = MetAdapter()
AIC = AicAdapter()


def met_record(**overrides):
    record = {
        "objectID": 436535,
        "isHighlight": True,
        "primaryImage": "https://images.example/big.jpg",
        "primaryImageSmall": "https://images.example/small.jpg",
        "department": "European Paintings",
        "objectName": "Painting",
        "title": " Wheat Field with Cypresses ",
        "culture": "",
        "period": "",
        "artistDisplayName": "Vincent van Gogh",
        "objectDate": "1889",
        "medium": "Oil on canvas",
        "dimensions": "28 7/8 x 36 3/4 in. (73 x 93.4 cm)",
        "measurements": [
            {"elementName": "Overall", "elementMeasurements": {"Height": 73.0, "Width": 93.4}},
            {"elementName": "Framed", "elementMeasurements": {"Height": 120.0, "Width": 140.0}},
        ],
        "classification": "Paintings",
        "objectURL": "https://www.metmuseum.org/art/collection/search/436535",
        "tags": [{"term": "Landscapes"}, {"term": "Cypresses"}, {"term": "landscapes"}],
        "GalleryNumber": " 822 ",
    }
    record.update(overrides)
    return record


def aic_record(**overrides):
    record = {
        "id": 27992,
        "title": "A Sunday on La Grande Jatte",
        "artist_display": "Georges Seurat\nFrench, 1859-1891",
        "artist_title": None,
        "date_display": "1884-86",
        "medium_display": "Oil on canvas",
        "dimensions": "207.5 × 308.1 cm (81 11/16 × 121 5/16 in.)",
        "is_on_view": True,
        "gallery_id": 2147484000,
        "gallery_title": "Gallery 240",
        "image_id": "abc-123",
        "department_title": "Painting and Sculpture of Europe",
        "classification_title": "painting",
        "style_title": "Pointillism",
        "subject_titles": ["Landscapes", "Parks"],
        "term_titles": ["Oil Paint", "landscapes"],
        "short_description": "<p>A <em>famous</em> park.</p>",
        "is_boosted": True,
    }
    record.update(overrides)
    return record


def test_met_normalization_of_a_full_record():
    obj = MET.normalize(met_record())
    assert isinstance(obj, NormalizedObject)
    assert obj.id == "met:436535" and obj.source_id == "436535" and obj.museum_id == "met"
    assert obj.title == "Wheat Field with Cypresses"
    assert obj.gallery == "822"
    assert obj.artist == "Vincent van Gogh" and obj.date == "1889"
    assert obj.tags == ["landscapes", "cypresses", "european paintings", "paintings", "painting"]
    assert obj.description is None
    assert obj.image_url == "https://images.example/small.jpg"
    assert obj.source_url.endswith("/436535")
    assert obj.is_highlight is True
    assert obj.size_class == "medium"  # the frame (140 cm) is ignored, 93.4 cm is the largest
    assert obj.dwell_minutes == 5


def test_met_without_gallery_is_skipped():
    assert MET.normalize(met_record(GalleryNumber="")) is Skip.NO_GALLERY
    assert MET.normalize(met_record(GalleryNumber="   ")) is Skip.NO_GALLERY


def test_met_gallery_that_breaks_the_contract_pattern_is_skipped():
    assert MET.normalize(met_record(GalleryNumber="Great Hall (north side of the building)")) is Skip.NO_GALLERY


def test_met_blank_title_and_malformed_records_are_invalid():
    assert MET.normalize(met_record(title="  ")) is Skip.INVALID
    assert MET.normalize({"objectID": 1, "title": "No gallery key"}) is Skip.INVALID


def test_met_size_falls_back_to_dimensions_string_then_medium():
    no_measurements = met_record(measurements=None, dimensions="H. 5 in. (12.7 cm)")
    assert MET.normalize(no_measurements).size_class == "small"
    nothing = met_record(measurements=None, dimensions="", isHighlight=False)
    obj = MET.normalize(nothing)
    assert obj.size_class == "medium" and obj.dwell_minutes == 3


def test_met_framed_only_measurements_are_used_when_nothing_else_exists():
    record = met_record(measurements=[{"elementName": "Framed", "elementMeasurements": {"Height": 300.0}}])
    assert MET.normalize(record).size_class == "monumental"


def test_met_image_and_optional_fields_become_none():
    obj = MET.normalize(met_record(primaryImageSmall="", primaryImage="", artistDisplayName="", objectDate="",
                                   objectURL="", tags=None))
    assert obj.image_url is None and obj.artist is None and obj.date is None and obj.source_url is None
    assert obj.tags == ["european paintings", "paintings", "painting"]


def test_aic_normalization_of_a_full_record():
    obj = AIC.normalize(aic_record())
    assert isinstance(obj, NormalizedObject)
    assert obj.id == "aic:27992" and obj.gallery == "240"
    assert obj.artist == "Georges Seurat"
    assert obj.tags == ["landscapes", "parks", "painting", "pointillism", "painting and sculpture of europe", "oil paint"]
    assert obj.description == "A famous park."
    assert obj.image_url == "https://www.artic.edu/iiif/2/abc-123/full/843,/0/default.jpg"
    assert obj.source_url == "https://www.artic.edu/artworks/27992"
    assert obj.size_class == "monumental" and obj.dwell_minutes == 8  # 6 + 2 for the highlight
    assert obj.department == "Painting and Sculpture of Europe" and obj.medium == "Oil on canvas"


def test_aic_artist_title_wins_over_artist_display():
    assert AIC.normalize(aic_record(artist_title="Seurat")).artist == "Seurat"
    assert AIC.normalize(aic_record(artist_title=None, artist_display=None)).artist is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"gallery_title": None},
        {"gallery_title": "Michigan Avenue entrance/steps"},
        {"gallery_title": "Gallery"},
        {"gallery_title": "Gallery 240 (north)"},
        {"is_on_view": False},
        {"is_on_view": None},
    ],
)
def test_aic_skip_cases(overrides):
    assert AIC.normalize(aic_record(**overrides)) is Skip.NO_GALLERY


def test_aic_alphanumeric_gallery_and_null_flags():
    obj = AIC.normalize(aic_record(gallery_title="Gallery 11B", is_boosted=None, image_id=None, subject_titles=None,
                                   term_titles=None, short_description=None, dimensions=None))
    assert obj.gallery == "11B" and obj.is_highlight is False and obj.image_url is None
    assert obj.size_class == "medium" and obj.description is None


def test_real_fixtures_normalize_and_skip_consistently(fixtures_dir):
    for adapter, rel in ((MET, "met/objects.json"), (AIC, "aic/artworks.json")):
        records = json.loads((fixtures_dir / rel).read_text(encoding="utf-8"))
        outcomes = [adapter.normalize(r) for r in records]
        assert not any(o is Skip.INVALID for o in outcomes)
        assert any(isinstance(o, NormalizedObject) for o in outcomes)
        assert any(o is Skip.NO_GALLERY for o in outcomes)


# ---- interest tags: what becomes an interest chip ----


def test_met_interest_tags_are_subjects_department_and_culture():
    record = {
        "objectID": 1, "title": "T", "GalleryNumber": "131",
        "tags": [{"term": "Horses"}, {"term": "Men"}],
        "department": "Arms and Armor", "classification": "Archery Equipment-Crossbows",
        "objectName": "Crossbow", "culture": "German", "period": "",
    }
    adapter = MetAdapter()
    assert adapter.interest_tags(record) == ["horses", "men", "arms and armor", "german"]
    obj = adapter.normalize(record)
    assert set(adapter.interest_tags(record)) <= set(obj.tags)
    assert "crossbow" in obj.tags and "crossbow" not in adapter.interest_tags(record)
    assert adapter.interest_tags({"title": "no id"}) == []


def test_aic_interest_tags_leave_out_materials_and_techniques():
    record = {
        "id": 7, "title": "T", "is_on_view": True, "gallery_title": "Gallery 240",
        "subject_titles": ["Landscapes", "Water", "red (color)", "Century of Progress"], "classification_title": "oil on canvas",
        "style_title": "Impressionism", "department_title": "Painting and Sculpture of Europe",
        "term_titles": ["oil paint (paint)", "Landscapes", "blue (color)"],
    }
    adapter = AicAdapter()
    chips = adapter.interest_tags(record)
    assert chips == ["landscapes", "water", "impressionism", "painting and sculpture of europe"]
    obj = adapter.normalize(record)
    assert set(chips) <= set(obj.tags)
    for not_a_chip in ("oil paint (paint)", "oil on canvas", "red (color)", "century of progress"):
        assert not_a_chip in obj.tags and not_a_chip not in chips


def test_fixture_facets_are_all_interest_tags(fixtures_dir):
    from ingestion.normalize import build_facets

    for adapter, fixture in ((MetAdapter(), "met/objects.json"), (AicAdapter(), "aic/artworks.json")):
        records = json.loads((fixtures_dir / fixture).read_text(encoding="utf-8"))
        objects, interest = [], {}
        for record in records:
            obj = adapter.normalize(record)
            if isinstance(obj, NormalizedObject):
                objects.append(obj)
                interest[obj.id] = set(adapter.interest_tags(record))
        facets = build_facets(objects, interest)
        assert facets, fixture
        allowed = set().union(*interest.values())
        assert all(f.tag in allowed for f in facets)
        assert not any("(" in f.tag or f.tag.startswith("oil ") for f in facets), [f.tag for f in facets]
