import json
import re

from ingestion.sources import FixtureSource
from ingestion.store import FileStore

SYNTHETIC_ID_FLOOR = 990000000
TEST_MET_GRAPH_GALLERIES = {"770", "765", "754", "099", "111"}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def written_objects(store_dir, museum_id):
    chunk_dir = store_dir / "museums" / museum_id / "object_chunks"
    return [o for path in sorted(chunk_dir.glob("*.json")) for o in read(path)["objects"]]


def chunk_names(store_dir, museum_id):
    return sorted(p.name for p in (store_dir / "museums" / museum_id / "object_chunks").glob("*.json"))


def met_source_records(fixtures_dir):
    return read(fixtures_dir / "met" / "objects.json")


class SpyStore(FileStore):
    def __init__(self, root):
        super().__init__(root)
        self.calls = []

    def write_floor_graph(self, museum_id, doc):
        self.calls.append("floor_graph")
        super().write_floor_graph(museum_id, doc)

    def write_chunks(self, museum_id, chunks):
        self.calls.append("chunks")
        super().write_chunks(museum_id, chunks)

    def delete_chunks_from(self, museum_id, first_stale_index):
        self.calls.append("delete_stale")
        super().delete_chunks_from(museum_id, first_stale_index)

    def write_museum(self, doc):
        self.calls.append("museum")
        super().write_museum(doc)


class ChunkWriteFails(SpyStore):
    def write_chunks(self, museum_id, chunks):
        self.calls.append("chunks")
        raise OSError("disk full")


class FailingSource:
    def __init__(self, inner, failing_museum):
        self._inner, self._failing = inner, failing_museum

    def load(self, adapter, max_objects):
        if adapter.museum_id == self._failing:
            raise RuntimeError("boom")
        return self._inner.load(adapter, max_objects)


class StaticSource:
    def __init__(self, records):
        self._records = records

    def load(self, adapter, max_objects):
        return self._records


class ExplodingQueue:
    def pull_all(self):
        raise AssertionError("Pub/Sub must not be touched when MUSEUM_ID is set")

    def ack(self, ack_ids):
        raise AssertionError("Pub/Sub must not be touched when MUSEUM_ID is set")


def test_full_run_writes_both_museums_and_acks_every_message(make_runner, base_config, fixtures_dir):
    runner, _, queue = make_runner([{"museum_id": "met"}, {"museum_id": "aic"}, {"museum_id": "met", "reason": "refresh"}])
    report = runner.run()

    assert report.exit_code == 0
    assert {r.museum_id: r.status for r in report.results} == {"met": "ok", "aic": "ok"}
    assert sorted(queue.acked) == ["ack-0", "ack-1", "ack-2"]

    met_records = met_source_records(fixtures_dir)
    with_gallery = [r for r in met_records if r["GalleryNumber"].strip()]
    met = report.as_dict()["met"]
    assert met["objects_written"] == len(with_gallery)
    assert met["skipped_no_gallery"] == len(met_records) - len(with_gallery)
    assert met["chunk_count"] == 1

    museum = read(base_config.file_store_dir / "museums" / "met.json")
    assert museum["object_count"] == len(with_gallery)
    assert museum["ingest_run_id"] == "20261113T090005Z-met"
    assert museum["last_ingested_at"] == "2026-11-13T09:00:05Z"
    assert museum["data_notice"] == (
        "Built from public data published by The Metropolitan Museum of Art. "
        "The Metropolitan Museum of Art has not reviewed or endorsed this plan."
    )
    assert museum["source"]["api_name"] == "The Met Collection API"


def test_objects_without_a_gallery_are_never_written_and_galleries_are_never_invented(make_runner, base_config, fixtures_dir):
    runner, _, _ = make_runner([{"museum_id": "met"}, {"museum_id": "aic"}])
    runner.run()

    gallery_by_id = {f"met:{r['objectID']}": r["GalleryNumber"].strip() for r in met_source_records(fixtures_dir)}
    written = written_objects(base_config.file_store_dir, "met")
    assert written
    for obj in written:
        assert obj["gallery"] and obj["gallery"] == gallery_by_id[obj["id"]]
        assert int(obj["source_id"]) < SYNTHETIC_ID_FLOOR  # the synthetic off-view records are all dropped

    aic_source = {f"aic:{r['id']}": r for r in read(fixtures_dir / "aic" / "artworks.json")}
    for obj in written_objects(base_config.file_store_dir, "aic"):
        source = aic_source[obj["id"]]
        assert source["is_on_view"] and re.fullmatch(r"Gallery ([A-Za-z0-9]+)", source["gallery_title"])
        assert source["gallery_title"] == f"Gallery {obj['gallery']}"


def test_galleries_missing_from_the_floor_graph_are_counted_but_still_written(make_runner, base_config):
    runner, _, _ = make_runner([{"museum_id": "met"}])
    report = runner.run()
    written = written_objects(base_config.file_store_dir, "met")
    expected = sum(1 for o in written if o["gallery"] not in TEST_MET_GRAPH_GALLERIES)
    assert expected > 0
    assert report.as_dict()["met"]["galleries_not_in_floor_graph"] == expected


def test_message_max_objects_overrides_the_cap_and_highlights_come_first(make_runner, base_config):
    runner, _, _ = make_runner([{"museum_id": "met", "max_objects": 5}])
    report = runner.run()
    written = written_objects(base_config.file_store_dir, "met")
    assert report.as_dict()["met"]["objects_written"] == 5 == len(written)
    assert all(o["is_highlight"] for o in written)
    ids = [int(o["source_id"]) for o in written]
    assert ids == sorted(ids)


def test_invalid_messages_are_acked_and_skipped(make_runner):
    runner, _, queue = make_runner([b"not json", {"museum_id": "Demo Museum"}, {"museum_id": "met"}])
    report = runner.run()
    assert [r.museum_id for r in report.results] == ["met"]
    assert sorted(queue.acked) == ["ack-0", "ack-1", "ack-2"]
    assert report.exit_code == 0


def test_unsupported_museum_is_acked_and_reported_without_failing_the_run(make_runner):
    runner, _, queue = make_runner([{"museum_id": "louvre"}])
    report = runner.run()
    assert [(r.museum_id, r.status) for r in report.results] == [("louvre", "unsupported")]
    assert queue.acked == ["ack-0"]
    assert report.exit_code == 0


def test_a_failing_museum_is_not_acked_and_the_run_exits_1(make_runner, base_config, fixtures_dir):
    source = FailingSource(FixtureSource(fixtures_dir), "aic")
    runner, _, queue = make_runner([{"museum_id": "met"}, {"museum_id": "aic"}], source=source)
    report = runner.run()

    statuses = {r.museum_id: r for r in report.results}
    assert statuses["met"].status == "ok"
    assert statuses["aic"].status == "failed" and "boom" in statuses["aic"].error
    assert queue.acked == ["ack-0"]
    assert report.exit_code == 1
    assert (base_config.file_store_dir / "museums" / "met.json").exists()
    assert not (base_config.file_store_dir / "museums" / "aic.json").exists()


def test_a_source_with_nothing_plannable_fails_instead_of_overwriting_the_museum(make_runner, base_config):
    off_view = {"objectID": 1, "title": "Off view", "GalleryNumber": ""}
    runner, _, queue = make_runner([{"museum_id": "met"}], source=StaticSource([off_view]))
    report = runner.run()
    assert report.results[0].status == "failed"
    assert queue.acked == []
    assert not (base_config.file_store_dir / "museums" / "met.json").exists()


def test_stale_chunks_are_removed_on_a_smaller_rerun(make_runner, base_config):
    first, _, _ = make_runner([{"museum_id": "met"}], chunk_size=10)
    first.run()
    assert len(chunk_names(base_config.file_store_dir, "met")) == 5

    second, _, _ = make_runner([{"museum_id": "met", "max_objects": 12}], chunk_size=10)
    report = second.run()
    assert chunk_names(base_config.file_store_dir, "met") == ["0000.json", "0001.json"]
    assert report.as_dict()["met"]["chunk_count"] == 2
    assert read(base_config.file_store_dir / "museums" / "met.json")["chunk_count"] == 2


def test_museum_document_is_written_last(make_runner, base_config):
    store = SpyStore(base_config.file_store_dir)
    runner, _, _ = make_runner([{"museum_id": "met"}], store=store)
    runner.run()
    assert store.calls == ["floor_graph", "chunks", "delete_stale", "museum"]


def test_a_failure_while_writing_chunks_never_writes_the_museum_document(make_runner, base_config):
    store = ChunkWriteFails(base_config.file_store_dir)
    runner, _, queue = make_runner([{"museum_id": "met"}], store=store)
    report = runner.run()
    assert report.exit_code == 1 and queue.acked == []
    assert store.calls == ["floor_graph", "chunks"]
    assert not (base_config.file_store_dir / "museums" / "met.json").exists()


def test_museum_id_mode_never_touches_the_queue(make_runner, base_config):
    runner, _, _ = make_runner(queue=ExplodingQueue(), museum_id="aic")
    report = runner.run()
    assert [(r.museum_id, r.status) for r in report.results] == [("aic", "ok")]
    assert (base_config.file_store_dir / "museums" / "aic.json").exists()


def test_old_museums_in_the_store_are_refreshed_and_fresh_ones_are_not(make_runner):
    runner, store, _ = make_runner([], refresh_after_hours=1)
    store.write_museum({"id": "met", "last_ingested_at": "2026-11-13T07:00:00Z"})  # 2 hours before the fixed clock
    store.write_museum({"id": "aic", "last_ingested_at": "2026-11-13T08:30:00Z"})  # 30 minutes before
    report = runner.run()
    assert [(r.museum_id, r.status) for r in report.results] == [("met", "ok")]


def test_refresh_is_disabled_when_the_interval_is_zero(make_runner):
    runner, store, _ = make_runner([], refresh_after_hours=0)
    store.write_museum({"id": "met", "last_ingested_at": "2000-01-01T00:00:00Z"})
    assert runner.run().results == []


def test_report_is_keyed_by_museum_id(make_runner):
    runner, _, _ = make_runner([{"museum_id": "aic"}])
    report = runner.run().as_dict()
    assert set(report["aic"]) == {
        "museum_id", "status", "objects_written", "skipped_no_gallery",
        "galleries_not_in_floor_graph", "chunk_count", "error",
    }
    assert report["aic"]["status"] == "ok" and report["aic"]["error"] is None
    json.dumps({"run": report})
