from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from planner.main import create_app
from planner.store import FileStore, MuseumData, MuseumRepository

from .helpers import FIXTURE_STORE, MUSEUM_ID


@pytest.fixture(scope="session")
def client() -> TestClient:
    return TestClient(create_app(store=FileStore(FIXTURE_STORE)))


@pytest.fixture(scope="session")
def data() -> MuseumData:
    return MuseumRepository(FileStore(FIXTURE_STORE), ttl_seconds=600).get(MUSEUM_ID)
