"""One JSON object per line on stdout, with the `severity` field Cloud Run structured logging reads."""

import json
import logging
import sys

LOGGER_NAME = "ingestion"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            **getattr(record, "fields", {}),
            "severity": record.levelname,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger(LOGGER_NAME)
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False


def log(level: int, message: str, **fields: object) -> None:
    logging.getLogger(LOGGER_NAME).log(level, message, extra={"fields": fields})


def log_exception(message: str, **fields: object) -> None:
    """ERROR with the active exception's traceback attached."""
    logging.getLogger(LOGGER_NAME).error(message, exc_info=True, extra={"fields": fields})
