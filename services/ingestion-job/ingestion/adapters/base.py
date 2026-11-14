import json
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Collection
from typing import Any, Protocol

from ingestion.http_client import HttpClient
from ingestion.models import NormalizedObject
from ingestion.normalize import Skip

# Live adapters stop after this many requests per MAX_OBJECTS (searches and retries included).
REQUEST_BUDGET_FACTOR = 4


@dataclass(frozen=True)
class MuseumInfo:
    name: str
    city: str
    api_name: str
    api_base_url: str
    license: str


class Adapter(Protocol):
    museum_id: str
    info: MuseumInfo
    fixture_file: str
    # True if the API cuts a client off after a few dozen requests, so the museum has to be
    # collected a batch at a time across runs (see contracts/README.md).
    incremental: bool

    def fetch_live(
        self, http: HttpClient, max_objects: int, skip_ids: Collection[str] = ()
    ) -> list[dict[str, Any]]:
        """Up to max_objects raw records straight from the museum API, leaving out objects whose
        source id is in skip_ids. An empty list means the API has nothing more to give; raise
        UpstreamError if it refused before giving anything."""
        ...

    def normalize(self, record: dict[str, Any]) -> NormalizedObject | Skip:
        """One raw record to a plannable object, or the reason it is dropped."""
        ...

    def interest_tags(self, record: dict[str, Any]) -> list[str]:
        """The record's tags that name a subject, style, or collection area (cleaned like
        object tags). Only these become interest chips; materials and techniques do not."""
        ...


def read_fixture(fixtures_dir: Path, adapter: Adapter) -> list[dict[str, Any]]:
    path = fixtures_dir / adapter.museum_id / adapter.fixture_file
    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError(f"{path} must hold a JSON array")
    return records
