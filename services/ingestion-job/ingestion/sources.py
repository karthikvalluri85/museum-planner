"""Where raw museum records come from: the live API or the payloads bundled in fixtures/."""

from pathlib import Path
from typing import Any, Protocol

from ingestion.adapters.base import Adapter, read_fixture
from ingestion.http_client import HttpClient


class RecordSource(Protocol):
    def load(self, adapter: Adapter, max_objects: int) -> list[dict[str, Any]]: ...


class FixtureSource:
    def __init__(self, fixtures_dir: Path) -> None:
        self._dir = fixtures_dir

    def load(self, adapter: Adapter, max_objects: int) -> list[dict[str, Any]]:
        return read_fixture(self._dir, adapter)


class LiveSource:
    def __init__(self, http: HttpClient) -> None:
        self._http = http

    def load(self, adapter: Adapter, max_objects: int) -> list[dict[str, Any]]:
        return adapter.fetch_live(self._http, max_objects)
