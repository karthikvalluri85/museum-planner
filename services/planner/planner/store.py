"""Read-only access to museum documents (Firestore or files), validated and cached.

Read protocol (contracts/README.md): museum doc, then chunks 0000..chunk_count-1, then the
floor graph. Chunks at or beyond chunk_count are stale and never read.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from .config import ConfigError, Settings
from .graph import Graph
from .models import MUSEUM_ID_PATTERN, FloorGraph, Museum, MuseumObject, ObjectChunk

log = logging.getLogger(__name__)

Doc = dict[str, Any]


class StoreError(Exception):
    """The backing store could not be read."""


class MuseumNotFound(Exception):
    """No museum document, or no floor graph: not plannable."""


class MuseumUnavailable(Exception):
    """The museum's documents exist but are missing pieces or fail validation."""


class Store(Protocol):
    def get_museum(self, museum_id: str) -> Doc | None: ...
    def get_chunk(self, museum_id: str, index: int) -> Doc | None: ...
    def get_floor_graph(self, museum_id: str) -> Doc | None: ...
    def list_museum_ids(self) -> list[str]: ...
    def list_floor_graph_ids(self) -> list[str]: ...


class FileStore:
    """Offline store: the Firestore paths mirrored as JSON files under one directory."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _read(self, path: Path) -> Doc | None:
        try:
            with path.open(encoding="utf-8") as fh:
                return json.load(fh)
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as exc:
            raise StoreError(f"cannot read {path}: {exc}") from exc

    def get_museum(self, museum_id: str) -> Doc | None:
        return self._read(self.root / "museums" / f"{museum_id}.json")

    def get_chunk(self, museum_id: str, index: int) -> Doc | None:
        return self._read(self.root / "museums" / museum_id / "object_chunks" / f"{index:04d}.json")

    def get_floor_graph(self, museum_id: str) -> Doc | None:
        return self._read(self.root / "floor_graphs" / f"{museum_id}.json")

    def _ids(self, folder: str) -> list[str]:
        directory = self.root / folder
        if not directory.is_dir():
            return []
        return sorted(p.stem for p in directory.glob("*.json"))

    def list_museum_ids(self) -> list[str]:
        return self._ids("museums")

    def list_floor_graph_ids(self) -> list[str]:
        return self._ids("floor_graphs")


class FirestoreStore:
    def __init__(self, project: str, database: str) -> None:
        # Imported lazily so file mode and tests never need the client library.
        # The client honors FIRESTORE_EMULATOR_HOST by itself.
        from google.cloud import firestore

        self._client = firestore.Client(project=project, database=database)

    def _get(self, ref: Any) -> Doc | None:
        try:
            snapshot = ref.get()
        except Exception as exc:  # any client or transport failure means the store is down
            raise StoreError(f"firestore read failed: {exc}") from exc
        return snapshot.to_dict() if snapshot.exists else None

    def _museum_ref(self, museum_id: str) -> Any:
        return self._client.collection("museums").document(museum_id)

    def get_museum(self, museum_id: str) -> Doc | None:
        return self._get(self._museum_ref(museum_id))

    def get_chunk(self, museum_id: str, index: int) -> Doc | None:
        return self._get(self._museum_ref(museum_id).collection("object_chunks").document(f"{index:04d}"))

    def get_floor_graph(self, museum_id: str) -> Doc | None:
        return self._get(self._client.collection("floor_graphs").document(museum_id))

    def _ids(self, collection: str) -> list[str]:
        try:
            return sorted(ref.id for ref in self._client.collection(collection).list_documents())
        except Exception as exc:
            raise StoreError(f"firestore list failed: {exc}") from exc

    def list_museum_ids(self) -> list[str]:
        return self._ids("museums")

    def list_floor_graph_ids(self) -> list[str]:
        return self._ids("floor_graphs")


def build_store(settings: Settings) -> Store:
    if settings.store_backend == "file":
        if not settings.file_store_dir:
            raise ConfigError("FILE_STORE_DIR is required when STORE_BACKEND=file")
        return FileStore(settings.file_store_dir)
    if not settings.google_cloud_project:
        raise ConfigError("GOOGLE_CLOUD_PROJECT is required when STORE_BACKEND=firestore")
    return FirestoreStore(settings.google_cloud_project, settings.firestore_database)


@dataclass(frozen=True)
class MuseumData:
    museum: Museum
    objects: tuple[MuseumObject, ...]
    floor_graph: FloorGraph
    graph: Graph


_MUSEUM_ID = re.compile(MUSEUM_ID_PATTERN)


class MuseumRepository:
    """Validated, per-museum TTL-cached view of the store. Thread-safe."""

    def __init__(
        self, store: Store, ttl_seconds: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._store = store
        self._ttl = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, MuseumData]] = {}

    def get(self, museum_id: str) -> MuseumData:
        # The id becomes a file name or document path, so reject anything off-pattern.
        if not _MUSEUM_ID.fullmatch(museum_id):
            raise MuseumNotFound(museum_id)
        now = self._clock()
        with self._lock:
            hit = self._cache.get(museum_id)
            if hit is not None and hit[0] > now:
                return hit[1]
        data = self._load(museum_id)
        with self._lock:
            self._cache[museum_id] = (now + self._ttl, data)
        return data

    def list_available(self) -> list[MuseumData]:
        ids = sorted(set(self._store.list_museum_ids()) & set(self._store.list_floor_graph_ids()))
        out = []
        for museum_id in ids:
            try:
                out.append(self.get(museum_id))
            except (MuseumNotFound, MuseumUnavailable):
                continue
        return out

    def _load(self, museum_id: str) -> MuseumData:
        raw_museum = self._store.get_museum(museum_id)
        if raw_museum is None:
            raise MuseumNotFound(museum_id)
        museum = self._validate(Museum, raw_museum, museum_id, "museum document")

        objects: list[MuseumObject] = []
        for index in range(museum.chunk_count):
            raw_chunk = self._store.get_chunk(museum_id, index)
            if raw_chunk is None:
                log.error("museum %s: chunk %04d is missing", museum_id, index)
                raise MuseumUnavailable(museum_id)
            chunk = self._validate(ObjectChunk, raw_chunk, museum_id, f"chunk {index:04d}")
            objects.extend(chunk.objects)

        raw_graph = self._store.get_floor_graph(museum_id)
        if raw_graph is None:
            raise MuseumNotFound(museum_id)
        floor_graph = self._validate(FloorGraph, raw_graph, museum_id, "floor graph")
        return MuseumData(museum, tuple(objects), floor_graph, Graph(floor_graph))

    @staticmethod
    def _validate(model: Any, raw: Doc, museum_id: str, what: str) -> Any:
        try:
            return model.model_validate(raw)
        except ValidationError as exc:
            log.error("museum %s: %s failed validation: %s", museum_id, what, exc)
            raise MuseumUnavailable(museum_id) from exc
