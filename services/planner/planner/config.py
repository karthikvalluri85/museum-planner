"""Environment configuration (contracts/services.json, entry "planner") and JSON logging.

PORT is consumed by the uvicorn command line in the Dockerfile, not here.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    store_backend: str
    google_cloud_project: str | None
    firestore_database: str
    file_store_dir: str | None
    cache_ttl_seconds: float

    @classmethod
    def from_env(cls) -> Settings:
        backend = os.environ.get("STORE_BACKEND", "firestore").strip().lower()
        if backend not in ("firestore", "file"):
            raise ConfigError(f"STORE_BACKEND must be firestore or file, got {backend!r}")
        try:
            ttl = float(os.environ.get("CACHE_TTL_SECONDS", "600"))
        except ValueError as exc:
            raise ConfigError(f"CACHE_TTL_SECONDS must be a number: {exc}") from exc
        return cls(
            store_backend=backend,
            google_cloud_project=os.environ.get("GOOGLE_CLOUD_PROJECT") or None,
            firestore_database=os.environ.get("FIRESTORE_DATABASE") or "(default)",
            file_store_dir=os.environ.get("FILE_STORE_DIR") or None,
            cache_ttl_seconds=ttl,
        )


class JsonFormatter(logging.Formatter):
    """One JSON object per line; Cloud Logging reads the severity field."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry)


def configure_logging() -> None:
    root = logging.getLogger()
    if any(isinstance(h.formatter, JsonFormatter) for h in root.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(logging.INFO)
