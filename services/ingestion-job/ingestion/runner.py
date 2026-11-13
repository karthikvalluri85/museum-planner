"""One execution of the job: build the work list, ingest each museum, ack what succeeded."""

import logging
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ingestion.adapters.base import Adapter
from ingestion.adapters.registry import ADAPTERS
from ingestion.config import Config
from ingestion.log import log, log_exception
from ingestion.models import FloorGraph, Museum, ObjectChunk, NormalizedObject, parse_ingest_message, to_doc
from ingestion.normalize import CHUNK_SIZE, Skip, build_facets, chunk_objects, order_objects
from ingestion.queue import MessageQueue
from ingestion.sources import RecordSource
from ingestion.store import Store


@dataclass
class WorkItem:
    museum_id: str
    max_objects: int | None = None
    ack_ids: list[str] = field(default_factory=list)


@dataclass
class MuseumResult:
    museum_id: str
    status: str  # ok | failed | unsupported
    objects_written: int = 0
    skipped_no_gallery: int = 0
    galleries_not_in_floor_graph: int = 0
    chunk_count: int = 0
    error: str | None = None


@dataclass
class RunReport:
    results: list[MuseumResult]

    @property
    def exit_code(self) -> int:
        return 1 if any(r.status == "failed" for r in self.results) else 0

    def as_dict(self) -> dict[str, Any]:
        return {r.museum_id: asdict(r) for r in self.results}


class Runner:
    def __init__(
        self,
        *,
        config: Config,
        store: Store,
        queue: MessageQueue,
        source: RecordSource,
        seed_dir: Path,
        adapters: Mapping[str, Adapter] | None = None,
        chunk_size: int = CHUNK_SIZE,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._config = config
        self._store = store
        self._queue = queue
        self._source = source
        self._seed_dir = seed_dir
        self._adapters = ADAPTERS if adapters is None else adapters
        self._chunk_size = chunk_size
        self._clock = clock

    def run(self) -> RunReport:
        work = self._build_work()
        return RunReport([self._process(item) for item in work.values()])

    # ---- work list ----

    def _build_work(self) -> dict[str, WorkItem]:
        if self._config.museum_id:
            return {self._config.museum_id: WorkItem(self._config.museum_id)}
        work = self._drain_queue()
        if self._config.refresh_after_hours > 0:
            for museum_id, last in self._store.last_ingested_times().items():
                if museum_id not in work and self._is_stale(last):
                    work[museum_id] = WorkItem(museum_id)
        return work

    def _drain_queue(self) -> dict[str, WorkItem]:
        work: dict[str, WorkItem] = {}
        for message in self._queue.pull_all():
            try:
                parsed = parse_ingest_message(message.data)
            except ValueError as exc:
                log(
                    logging.ERROR,
                    "invalid ingest-museum message, acknowledging it so it cannot poison the queue",
                    error=str(exc),
                    body=message.data[:200].decode("utf-8", errors="replace"),
                )
                self._ack([message.ack_id])
                continue
            item = work.setdefault(parsed.museum_id, WorkItem(parsed.museum_id))
            item.ack_ids.append(message.ack_id)
            if parsed.max_objects is not None:
                item.max_objects = max(item.max_objects or 0, parsed.max_objects)
        return work

    def _is_stale(self, last_ingested_at: str | None) -> bool:
        if not last_ingested_at:
            return True
        try:
            last = datetime.fromisoformat(last_ingested_at.replace("Z", "+00:00"))
        except ValueError:
            return True
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return self._clock() - last > timedelta(hours=self._config.refresh_after_hours)

    # ---- one museum ----

    def _process(self, item: WorkItem) -> MuseumResult:
        try:
            result = self._ingest(item)
        except Exception as exc:  # any failure of one museum must not stop the others
            log_exception("museum failed, messages left unacknowledged", museum_id=item.museum_id)
            return MuseumResult(item.museum_id, "failed", error=f"{type(exc).__name__}: {exc}")
        if result.status != "failed":
            self._ack(item.ack_ids)
        return result

    def _ingest(self, item: WorkItem) -> MuseumResult:
        museum_id = item.museum_id
        adapter = self._adapters.get(museum_id)
        graph_path = self._seed_dir / f"{museum_id}.json"
        if adapter is None or not graph_path.is_file():
            reason = "no adapter" if adapter is None else f"no seed floor graph at {graph_path.name}"
            log(logging.ERROR, "unsupported museum, it can never succeed", museum_id=museum_id, reason=reason)
            return MuseumResult(museum_id, "unsupported", error=reason)

        graph = FloorGraph.model_validate_json(graph_path.read_text(encoding="utf-8"))
        if graph.museum_id != museum_id:
            raise ValueError(f"{graph_path.name} describes museum {graph.museum_id!r}")

        max_objects = item.max_objects or self._config.max_objects
        records = self._source.load(adapter, max_objects)
        objects, interest_tags, skipped_no_gallery, skipped_invalid = self._normalize(adapter, records)
        if skipped_invalid:
            log(logging.WARNING, "records skipped as invalid", museum_id=museum_id, count=skipped_invalid)
        objects = order_objects(objects)[:max_objects]
        if not objects:
            raise ValueError("no plannable objects (with a gallery) in the source data; nothing written")

        now = self._clock().astimezone(timezone.utc)
        run_id = f"{now:%Y%m%dT%H%M%SZ}-{museum_id}"
        chunks = chunk_objects(objects, self._chunk_size)
        known_galleries = graph.gallery_numbers()
        info = adapter.info

        # Write protocol (contracts/README.md): graph, chunks, delete stale chunks, museum document last.
        self._store.write_floor_graph(museum_id, to_doc(graph))
        self._store.write_chunks(
            museum_id,
            [
                to_doc(ObjectChunk(museum_id=museum_id, chunk_index=i, ingest_run_id=run_id, objects=chunk))
                for i, chunk in enumerate(chunks)
            ],
        )
        self._store.delete_chunks_from(museum_id, len(chunks))
        museum = Museum(
            id=museum_id,
            name=info.name,
            city=info.city,
            source={"api_name": info.api_name, "api_base_url": info.api_base_url, "license": info.license},
            data_notice=(
                f"Built from public data published by {info.name}. "
                f"{info.name} has not reviewed or endorsed this plan."
            ),
            object_count=len(objects),
            skipped_no_gallery_count=skipped_no_gallery,
            chunk_count=len(chunks),
            facets=build_facets(objects, interest_tags),
            last_ingested_at=f"{now:%Y-%m-%dT%H:%M:%SZ}",
            ingest_run_id=run_id,
        )
        self._store.write_museum(to_doc(museum))

        result = MuseumResult(
            museum_id,
            "ok",
            objects_written=len(objects),
            skipped_no_gallery=skipped_no_gallery,
            galleries_not_in_floor_graph=sum(1 for o in objects if o.gallery not in known_galleries),
            chunk_count=len(chunks),
        )
        log(logging.INFO, "museum ingested", **asdict(result))
        return result

    @staticmethod
    def _normalize(
        adapter: Adapter, records: list[dict[str, Any]]
    ) -> tuple[list[NormalizedObject], dict[str, frozenset[str]], int, int]:
        """Returns (objects, interest tags per object id, skipped for no gallery, skipped as invalid)."""
        objects: dict[str, NormalizedObject] = {}
        interest_tags: dict[str, frozenset[str]] = {}
        no_gallery = invalid = 0
        for record in records:
            outcome = adapter.normalize(record)
            if outcome is Skip.NO_GALLERY:
                no_gallery += 1
            elif outcome is Skip.INVALID:
                invalid += 1
            elif outcome.id not in objects:
                objects[outcome.id] = outcome
                interest_tags[outcome.id] = frozenset(adapter.interest_tags(record))
        return list(objects.values()), interest_tags, no_gallery, invalid

    def _ack(self, ack_ids: list[str]) -> None:
        if not ack_ids:
            return
        try:
            self._queue.ack(ack_ids)
        except Exception as exc:  # the data is written; a lost ack only means one redundant refresh
            log(logging.WARNING, "acknowledging messages failed", error=f"{type(exc).__name__}: {exc}")
