"""Entrypoint. Exit codes: 0 success, 1 a museum or the run failed (Cloud Run retries), 2 bad configuration."""

import json
import logging
import os
import sys

from ingestion.config import DEFAULT_FIXTURES_DIR, DEFAULT_SEED_DIR, ConfigError, load_config
from ingestion.http_client import HttpClient
from ingestion.log import configure_logging, log, log_exception
from ingestion.queue import NullQueue, PubSubPullQueue
from ingestion.runner import Runner
from ingestion.sources import FixtureSource, LiveSource
from ingestion.store import make_store


def main() -> int:
    configure_logging()
    try:
        config = load_config(os.environ)
    except ConfigError as exc:
        log(logging.ERROR, f"invalid configuration: {exc}")
        return 2

    try:
        if config.museum_id or config.project is None:
            queue = NullQueue()
        else:
            queue = PubSubPullQueue(config.project, config.subscription)
        source = FixtureSource(DEFAULT_FIXTURES_DIR) if config.source_mode == "fixture" else LiveSource(HttpClient())
        runner = Runner(
            config=config, store=make_store(config), queue=queue, source=source, seed_dir=DEFAULT_SEED_DIR
        )
        report = runner.run()
    except Exception:  # e.g. Pub/Sub or Firestore unreachable: nothing was acknowledged, so a retry is safe
        log_exception("run aborted")
        return 1

    print(json.dumps({"run": report.as_dict()}), flush=True)
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
