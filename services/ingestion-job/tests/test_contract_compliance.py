"""Everything the job writes in file mode must validate against the JSON Schemas in /contracts."""

import json

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource


def validator(contracts_dir, schema_file):
    registry = Registry()
    for path in (contracts_dir / "firestore").glob("*.schema.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    schema = json.loads((contracts_dir / "firestore" / schema_file).read_text(encoding="utf-8"))
    return Draft202012Validator(schema, registry=registry)


def assert_valid(check, doc, label):
    errors = sorted(check.iter_errors(doc), key=lambda e: list(e.path))
    assert not errors, f"{label}: {[f'{list(e.path)}: {e.message}' for e in errors[:3]]}"


@pytest.mark.parametrize("chunk_size", [250, 10])
def test_every_written_document_matches_its_schema(make_runner, base_config, contracts_dir, chunk_size):
    runner, _, _ = make_runner([{"museum_id": "met"}, {"museum_id": "aic"}], chunk_size=chunk_size)
    assert runner.run().exit_code == 0

    store_dir = base_config.file_store_dir
    museum_check = validator(contracts_dir, "museum.schema.json")
    chunk_check = validator(contracts_dir, "object-chunk.schema.json")
    graph_check = validator(contracts_dir, "floor-graph.schema.json")
    for museum_id in ("met", "aic"):
        museum = json.loads((store_dir / "museums" / f"{museum_id}.json").read_text(encoding="utf-8"))
        assert_valid(museum_check, museum, f"museum {museum_id}")
        chunk_files = sorted((store_dir / "museums" / museum_id / "object_chunks").glob("*.json"))
        assert len(chunk_files) == museum["chunk_count"] > 0
        total = 0
        for index, path in enumerate(chunk_files):
            assert path.name == f"{index:04d}.json"
            chunk = json.loads(path.read_text(encoding="utf-8"))
            assert chunk["chunk_index"] == index and chunk["ingest_run_id"] == museum["ingest_run_id"]
            assert_valid(chunk_check, chunk, f"chunk {museum_id}/{path.name}")
            total += len(chunk["objects"])
        assert total == museum["object_count"]
        graph = json.loads((store_dir / "floor_graphs" / f"{museum_id}.json").read_text(encoding="utf-8"))
        assert_valid(graph_check, graph, f"floor graph {museum_id}")
