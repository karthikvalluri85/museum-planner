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
from ingestion.http_client import UpstreamError
from ingestion.log import log, log_exception
from ingestion.models import FloorGraph, Museum, ObjectChunk, NormalizedObject, parse_ingest_message, to_doc
from ingestion.normalize import CHUNK_SIZE, Skip, build_facets, chunk_objects, order_objects
from ingestion.queue import MessageQueue
from ingestion.sources import RecordSource
from ingestion.store import Store


# A museum being collected a batch at a time is given up on (left as it is, marked complete)
# when no run has added anything for this long.
STALL_HOURS = 24


@dataclass
class WorkItem:
    museum_id: str
    max_objects: int | None = None
    ack_ids: list[str] = field(default_factory=list)
    # A "refresh" message: throw away what is stored and collect the museum again from the start.
    restart: bool = False


@dataclass
class MuseumResult:
    museum_id: str
    status: str  # ok | failed | unsupported
    objects_written: int = 0  # plannable objects now stored
    objects_added: int = 0  # of those, new in this run
    skipped_no_gallery: int = 0
    galleries_not_in_floor_graph: int = 0
    chunk_count: int = 0
    ingest_complete: bool = True
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
        for museum_id in self._store.incomplete_museums():
            work.setdefault(museum_id, WorkItem(museum_id))
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
            item.restart = item.restart or parsed.reason == "refresh"
        return work

    def _is_stale(self, last_ingested_at: str | None) -> bool:
        return self._older_than(last_ingested_at, self._config.refresh_after_hours)

    def _older_than(self, timestamp: str | None, hours: float) -> bool:
        if not timestamp:
            return True
        try:
            then = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            return True
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        return self._clock() - then > timedelta(hours=hours)

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

        # Batch at a time (contracts/README.md): pick up where the last run stopped.
        accumulate = adapter.incremental and getattr(self._source, "resumable", False)
        previous = self._store.read_museum(museum_id) if accumulate and not item.restart else None
        existing = self._stored_objects(museum_id, previous) if previous else []
        max_objects = (
            item.max_objects
            or (previous or {}).get("ingest_target_count")
            or self._config.max_objects
        )
        wanted = max(max_objects - len(existing), 0)

        records: list[dict[str, Any]] = []
        refused = False
        if not accumulate:
            records = self._source.load(adapter, max_objects)
        elif wanted:
            try:
                records = self._source.load(adapter, wanted, skip_ids={o.source_id for o in existing})
            except UpstreamError as exc:
                if not existing:
                    raise
                refused = True
                log(logging.WARNING, "museum API gave nothing this run", museum_id=museum_id, error=str(exc))

        fetched, skipped_no_gallery, skipped_invalid = self._normalize(adapter, records)
        if skipped_invalid:
            log(logging.WARNING, "records skipped as invalid", museum_id=museum_id, count=skipped_invalid)
        known = {o.id for o in existing}
        new = [o for o in fetched if o.id not in known]
        objects = order_objects(existing + new)[:max_objects]
        if not objects:
            raise ValueError("no plannable objects (with a gallery) in the source data; nothing written")
        skipped_no_gallery += (previous or {}).get("skipped_no_gallery_count", 0)

        # Done when the target is reached, or the API answered and had nothing more to give.
        complete = not accumulate or len(objects) >= max_objects or (wanted > 0 and not refused and not records)
        if previous and not new and not complete:
            if not self._older_than(previous.get("last_ingested_at"), STALL_HOURS):
                # Nothing gained: leave the store alone so last_ingested_at keeps marking the last progress.
                log(logging.INFO, "no new objects this run", museum_id=museum_id, stored=len(existing))
                return MuseumResult(
                    museum_id,
                    "ok",
                    objects_written=len(existing),
                    chunk_count=previous.get("chunk_count", 0),
                    ingest_complete=False,
                )
            log(
                logging.WARNING,
                "no new objects for a day; keeping what is stored and no longer trying",
                museum_id=museum_id, stored=len(existing), wanted=max_objects,
            )
            complete = True

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
            facets=build_facets(objects),
            last_ingested_at=f"{now:%Y-%m-%dT%H:%M:%SZ}",
            ingest_run_id=run_id,
            ingest_complete=complete,
            ingest_target_count=max_objects,
        )
        self._store.write_museum(to_doc(museum))

        result = MuseumResult(
            museum_id,
            "ok",
            objects_written=len(objects),
            objects_added=len(new) if previous else len(objects),
            skipped_no_gallery=skipped_no_gallery,
            galleries_not_in_floor_graph=sum(1 for o in objects if o.gallery not in known_galleries),
            chunk_count=len(chunks),
            ingest_complete=complete,
        )
        log(logging.INFO, "museum ingested", **asdict(result))
        return result

    def _stored_objects(self, museum_id: str, museum: dict[str, Any]) -> list[NormalizedObject]:
        """What an earlier run stored, or nothing if it cannot be read back cleanly (start again)."""
        try:
            chunks = self._store.read_chunks(museum_id, museum.get("chunk_count", 0))
            return [NormalizedObject.model_validate(o) for chunk in chunks for o in chunk["objects"]]
        except (LookupError, KeyError, ValueError) as exc:
            log(logging.WARNING, "stored objects unreadable, collecting the museum again", museum_id=museum_id, error=str(exc)[:300])
            return []

    @staticmethod
    def _normalize(adapter: Adapter, records: list[dict[str, Any]]) -> tuple[list[NormalizedObject], int, int]:
        """Returns (objects, skipped for no gallery, skipped as invalid)."""
        objects: dict[str, NormalizedObject] = {}
        no_gallery = invalid = 0
        for record in records:
            outcome = adapter.normalize(record)
            if outcome is Skip.NO_GALLERY:
                no_gallery += 1
            elif outcome is Skip.INVALID:
                invalid += 1
            else:
                objects.setdefault(outcome.id, outcome)
        return list(objects.values()), no_gallery, invalid

    def _ack(self, ack_ids: list[str]) -> None:
        if not ack_ids:
            return
        try:
            self._queue.ack(ack_ids)
        except Exception as exc:  # the data is written; a lost ack only means one redundant refresh
            log(logging.WARNING, "acknowledging messages failed", error=f"{type(exc).__name__}: {exc}")
