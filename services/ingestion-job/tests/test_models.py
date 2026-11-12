import json

import pytest
from pydantic import ValidationError

from ingestion.config import ConfigError, load_config
from ingestion.models import FloorGraph, Museum, NormalizedObject, ObjectChunk, parse_ingest_message, to_doc

VALID = [
    ("object.valid.json", NormalizedObject),
    ("object-chunk.valid.json", ObjectChunk),
    ("museum.valid.json", Museum),
    ("floor-graph.valid.json", FloorGraph),
]
INVALID = [
    ("object.invalid-no-gallery.json", NormalizedObject),
    ("floor-graph.invalid-unnamed-stairs.json", FloorGraph),
    ("floor-graph.invalid-stairs-marked-step-free.json", FloorGraph),
]


def load(contracts_dir, name):
    return json.loads((contracts_dir / "examples" / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(("name", "model"), VALID)
def test_valid_contract_examples_are_accepted(contracts_dir, name, model):
    model.model_validate(load(contracts_dir, name))


@pytest.mark.parametrize(("name", "model"), INVALID)
def test_invalid_contract_examples_are_rejected(contracts_dir, name, model):
    with pytest.raises(ValidationError):
        model.model_validate(load(contracts_dir, name))


def test_ingest_message_examples(contracts_dir):
    examples = contracts_dir / "examples"
    assert parse_ingest_message((examples / "ingest-message.valid.json").read_bytes()).max_objects == 200
    with pytest.raises(ValueError):
        parse_ingest_message((examples / "ingest-message.invalid.json").read_bytes())


def test_floor_graph_round_trips_with_the_from_alias(contracts_dir):
    doc = to_doc(FloorGraph.model_validate(load(contracts_dir, "floor-graph.valid.json")))
    assert doc["edges"][0]["from"] == "entrance" and "from_" not in doc["edges"][0]


def test_floor_graph_cross_field_rules(contracts_dir):
    base = load(contracts_dir, "floor-graph.valid.json")
    disconnected = {**base, "edges": base["edges"][:1]}
    with pytest.raises(ValidationError):
        FloorGraph.model_validate(disconnected)
    duplicate_gallery = json.loads(json.dumps(base))
    duplicate_gallery["nodes"][2]["galleries"] = ["A1"]
    with pytest.raises(ValidationError):
        FloorGraph.model_validate(duplicate_gallery)


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"\xff\xfe",
        b"[]",
        b'{"museum_id": "met", "extra": 1}',
        b'{"museum_id": "met", "max_objects": "5"}',
        b'{"museum_id": "met", "max_objects": 0}',
        b'{"museum_id": "met", "max_objects": null}',
        b'{"museum_id": "met", "reason": "delete"}',
        b"{}",
    ],
)
def test_invalid_ingest_messages_raise_value_error(body):
    with pytest.raises(ValueError):
        parse_ingest_message(body)


def test_ingest_message_defaults():
    message = parse_ingest_message(b'{"museum_id": "met"}')
    assert (message.reason, message.max_objects, message.requested_at) == ("add", None, None)


def test_config_defaults_and_validation(tmp_path):
    config = load_config({"GOOGLE_CLOUD_PROJECT": "p"})
    assert (config.store_backend, config.source_mode, config.max_objects, config.refresh_after_hours) == (
        "firestore", "live", 1500, 168,
    )
    assert config.subscription == "ingest-museum-job" and config.firestore_database == "(default)"
    for env in (
        {},  # project missing
        {"GOOGLE_CLOUD_PROJECT": "p", "MAX_OBJECTS": "many"},
        {"GOOGLE_CLOUD_PROJECT": "p", "MAX_OBJECTS": "0"},
        {"GOOGLE_CLOUD_PROJECT": "p", "SOURCE_MODE": "both"},
        {"GOOGLE_CLOUD_PROJECT": "p", "STORE_BACKEND": "file"},  # no FILE_STORE_DIR
        {"GOOGLE_CLOUD_PROJECT": "p", "MUSEUM_ID": "Not Valid"},
    ):
        with pytest.raises(ConfigError):
            load_config(env)
    local = load_config({"STORE_BACKEND": "file", "FILE_STORE_DIR": str(tmp_path), "MUSEUM_ID": "met"})
    assert local.project is None and local.museum_id == "met"
