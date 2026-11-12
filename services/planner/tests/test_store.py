from __future__ import annotations

import pytest

from planner.store import FileStore, MuseumNotFound, MuseumRepository, MuseumUnavailable, StoreError

from .helpers import FIXTURE_STORE, MUSEUM_ID, STALE_OBJECT_ID


class CountingStore(FileStore):
    def __init__(self, root):
        super().__init__(root)
        self.museum_reads = 0

    def get_museum(self, museum_id):
        self.museum_reads += 1
        return super().get_museum(museum_id)


class FailingStore(FileStore):
    def get_museum(self, museum_id):
        raise StoreError("simulated outage")


def test_read_protocol_ignores_stale_chunks(data):
    ids = {o.id for o in data.objects}
    assert len(ids) == 31
    assert STALE_OBJECT_ID not in ids


def test_cache_serves_until_ttl_expires():
    now = [1000.0]
    store = CountingStore(FIXTURE_STORE)
    repo = MuseumRepository(store, ttl_seconds=60, clock=lambda: now[0])
    first = repo.get(MUSEUM_ID)
    assert repo.get(MUSEUM_ID) is first
    assert store.museum_reads == 1
    now[0] += 61
    repo.get(MUSEUM_ID)
    assert store.museum_reads == 2


def test_missing_documents_and_bad_ids_are_not_found():
    repo = MuseumRepository(FileStore(FIXTURE_STORE), ttl_seconds=60)
    for museum_id in ("nosuchmuseum", "orphanmuseum", "../museums/testmuseum", "UPPER"):
        with pytest.raises(MuseumNotFound):
            repo.get(museum_id)


def test_invalid_documents_make_museum_unavailable():
    repo = MuseumRepository(FileStore(FIXTURE_STORE), ttl_seconds=60)
    with pytest.raises(MuseumUnavailable):
        repo.get("brokenmuseum")
    assert [d.museum.id for d in repo.list_available()] == [MUSEUM_ID]


def test_store_outage_is_503():
    from fastapi.testclient import TestClient

    from planner.main import create_app

    outage = TestClient(create_app(store=FailingStore(FIXTURE_STORE)))
    for response in (outage.get(f"/api/museums/{MUSEUM_ID}"), outage.get("/api/museums")):
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "store_unavailable"


def test_file_store_returns_none_for_missing_files():
    store = FileStore(FIXTURE_STORE)
    assert store.get_museum("nosuchmuseum") is None
    assert store.get_chunk(MUSEUM_ID, 99) is None
    assert store.get_floor_graph("orphanmuseum") is None
