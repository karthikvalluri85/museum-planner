"""Environment parsing. Names and defaults follow the `ingestion-job` entry of contracts/services.json."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SEED_DIR = PACKAGE_ROOT / "seed" / "floor_graphs"
DEFAULT_FIXTURES_DIR = PACKAGE_ROOT / "fixtures"

_MUSEUM_ID = re.compile(r"[a-z][a-z0-9-]{1,30}")


class ConfigError(Exception):
    """Raised for a missing or malformed environment variable."""


@dataclass(frozen=True)
class Config:
    store_backend: str
    project: str | None
    firestore_database: str
    subscription: str
    museum_id: str | None
    source_mode: str
    max_objects: int
    refresh_after_hours: int
    file_store_dir: Path | None


def load_config(env: Mapping[str, str]) -> Config:
    def get(name: str, default: str | None = None) -> str | None:
        value = env.get(name, "").strip()
        return value or default

    def get_int(name: str, default: int, minimum: int) -> int:
        raw = get(name, str(default))
        try:
            value = int(raw)  # type: ignore[arg-type]
        except ValueError:
            raise ConfigError(f"{name} must be an integer, got {raw!r}") from None
        if value < minimum:
            raise ConfigError(f"{name} must be >= {minimum}, got {value}")
        return value

    store_backend = get("STORE_BACKEND", "firestore")
    if store_backend not in ("firestore", "file"):
        raise ConfigError(f"STORE_BACKEND must be 'firestore' or 'file', got {store_backend!r}")
    source_mode = get("SOURCE_MODE", "live")
    if source_mode not in ("live", "fixture"):
        raise ConfigError(f"SOURCE_MODE must be 'live' or 'fixture', got {source_mode!r}")

    museum_id = get("MUSEUM_ID")
    if museum_id is not None and not _MUSEUM_ID.fullmatch(museum_id):
        raise ConfigError(f"MUSEUM_ID {museum_id!r} does not match [a-z][a-z0-9-]{{1,30}}")

    project = get("GOOGLE_CLOUD_PROJECT")
    if project is None and not (store_backend == "file" and museum_id is not None):
        raise ConfigError(
            "GOOGLE_CLOUD_PROJECT is required unless STORE_BACKEND=file and MUSEUM_ID is set"
        )

    file_store_dir = get("FILE_STORE_DIR")
    if store_backend == "file" and file_store_dir is None:
        raise ConfigError("FILE_STORE_DIR is required when STORE_BACKEND=file")

    return Config(
        store_backend=store_backend,
        project=project,
        firestore_database=get("FIRESTORE_DATABASE", "(default)"),  # type: ignore[arg-type]
        subscription=get("PUBSUB_SUBSCRIPTION", "ingest-museum-job"),  # type: ignore[arg-type]
        museum_id=museum_id,
        source_mode=source_mode,
        max_objects=get_int("MAX_OBJECTS", 1500, 1),
        refresh_after_hours=get_int("REFRESH_AFTER_HOURS", 168, 0),
        file_store_dir=Path(file_store_dir) if file_store_dir else None,
    )
