"""Where documents go. Layout is in contracts/README.md; the runner decides the write order."""

import json
import os
from pathlib import Path
from typing import Any, Protocol

from ingestion.config import Config

Doc = dict[str, Any]
FIRESTORE_BATCH_CHUNKS = 10


def chunk_name(index: int) -> str:
    return f"{index:04d}"


class Store(Protocol):
    def write_floor_graph(self, museum_id: str, doc: Doc) -> None: ...

    def write_chunks(self, museum_id: str, chunks: list[Doc]) -> None:
        """Each doc carries its own `chunk_index`."""
        ...

    def delete_chunks_from(self, museum_id: str, first_stale_index: int) -> None:
        """Deletes chunks whose index is >= first_stale_index."""
        ...

    def write_museum(self, doc: Doc) -> None: ...

    def last_ingested_times(self) -> dict[str, str | None]:
        """museum id -> last_ingested_at, for every museum in the store."""
        ...


class FileStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def write_floor_graph(self, museum_id: str, doc: Doc) -> None:
        self._write(self._root / "floor_graphs" / f"{museum_id}.json", doc)

    def write_chunks(self, museum_id: str, chunks: list[Doc]) -> None:
        for doc in chunks:
            self._write(self._chunk_dir(museum_id) / f"{chunk_name(doc['chunk_index'])}.json", doc)

    def delete_chunks_from(self, museum_id: str, first_stale_index: int) -> None:
        directory = self._chunk_dir(museum_id)
        if not directory.is_dir():
            return
        for path in directory.glob("*.json"):
            if int(path.stem) >= first_stale_index:
                path.unlink()

    def write_museum(self, doc: Doc) -> None:
        self._write(self._root / "museums" / f"{doc['id']}.json", doc)

    def last_ingested_times(self) -> dict[str, str | None]:
        times: dict[str, str | None] = {}
        for path in sorted((self._root / "museums").glob("*.json")):
            times[path.stem] = json.loads(path.read_text(encoding="utf-8")).get("last_ingested_at")
        return times

    def _chunk_dir(self, museum_id: str) -> Path:
        return self._root / "museums" / museum_id / "object_chunks"

    @staticmethod
    def _write(path: Path, doc: Doc) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)


class FirestoreStore:
    def __init__(self, project: str, database: str) -> None:
        from google.cloud import firestore  # imported here so file mode and tests need no GCP libraries

        self._client = firestore.Client(project=project, database=database)

    def write_floor_graph(self, museum_id: str, doc: Doc) -> None:
        self._client.collection("floor_graphs").document(museum_id).set(doc)

    def write_chunks(self, museum_id: str, chunks: list[Doc]) -> None:
        collection = self._chunks(museum_id)
        for start in range(0, len(chunks), FIRESTORE_BATCH_CHUNKS):
            batch = self._client.batch()
            for doc in chunks[start : start + FIRESTORE_BATCH_CHUNKS]:
                batch.set(collection.document(chunk_name(doc["chunk_index"])), doc)
            batch.commit()

    def delete_chunks_from(self, museum_id: str, first_stale_index: int) -> None:
        stale = [ref for ref in self._chunks(museum_id).list_documents() if int(ref.id) >= first_stale_index]
        for start in range(0, len(stale), 400):
            batch = self._client.batch()
            for ref in stale[start : start + 400]:
                batch.delete(ref)
            batch.commit()

    def write_museum(self, doc: Doc) -> None:
        self._client.collection("museums").document(doc["id"]).set(doc)

    def last_ingested_times(self) -> dict[str, str | None]:
        snapshots = self._client.collection("museums").select(["last_ingested_at"]).stream()
        return {snap.id: (snap.to_dict() or {}).get("last_ingested_at") for snap in snapshots}

    def _chunks(self, museum_id: str) -> Any:
        return self._client.collection("museums").document(museum_id).collection("object_chunks")


def make_store(config: Config) -> Store:
    if config.store_backend == "file":
        assert config.file_store_dir is not None  # enforced by load_config
        return FileStore(config.file_store_dir)
    assert config.project is not None  # enforced by load_config
    return FirestoreStore(config.project, config.firestore_database)
