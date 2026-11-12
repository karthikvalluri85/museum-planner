import json
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ingestion.config import DEFAULT_FIXTURES_DIR, Config
from ingestion.queue import Received
from ingestion.runner import Runner
from ingestion.sources import FixtureSource
from ingestion.store import FileStore

JOB_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_DIR = JOB_ROOT.parent.parent / "contracts"
TEST_SEED_DIR = Path(__file__).resolve().parent / "fixtures" / "seed"
FIXED_NOW = datetime(2026, 11, 13, 9, 0, 5, tzinfo=timezone.utc)


class FakeQueue:
    def __init__(self, bodies: list[bytes | dict]) -> None:
        self.pending = [
            Received(ack_id=f"ack-{i}", data=b if isinstance(b, bytes) else json.dumps(b).encode("utf-8"))
            for i, b in enumerate(bodies)
        ]
        self.acked: list[str] = []

    def pull_all(self) -> list[Received]:
        pulled, self.pending = self.pending, []
        return pulled

    def ack(self, ack_ids: list[str]) -> None:
        self.acked.extend(ack_ids)


@pytest.fixture
def contracts_dir() -> Path:
    return CONTRACTS_DIR


@pytest.fixture
def fixtures_dir() -> Path:
    return DEFAULT_FIXTURES_DIR


@pytest.fixture
def base_config(tmp_path: Path) -> Config:
    return Config(
        store_backend="file",
        project=None,
        firestore_database="(default)",
        subscription="ingest-museum-job",
        museum_id=None,
        source_mode="fixture",
        max_objects=1500,
        refresh_after_hours=0,
        file_store_dir=tmp_path / "store",
    )


@pytest.fixture
def make_runner(base_config: Config) -> Callable[..., tuple[Runner, FileStore, FakeQueue]]:
    """make_runner(bodies, store=None, queue=None, source=None, chunk_size=250, **config_overrides)"""

    def factory(bodies=(), *, store=None, queue=None, source=None, chunk_size=250, **overrides):
        config = replace(base_config, **overrides)
        store = store or FileStore(config.file_store_dir)
        queue = queue or FakeQueue(list(bodies))
        runner = Runner(
            config=config,
            store=store,
            queue=queue,
            source=source or FixtureSource(DEFAULT_FIXTURES_DIR),
            seed_dir=TEST_SEED_DIR,
            chunk_size=chunk_size,
            clock=lambda: FIXED_NOW,
        )
        return runner, store, queue

    return factory
