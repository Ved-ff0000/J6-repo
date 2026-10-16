import json
import logging
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    """Allowlisted log fields: never serialize request bodies, headers or exception strings."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key in (
            "request_id",
            "method",
            "status_code",
            "duration_ms",
            "error_type",
            "organization_id",
            "user_id",
            "document_id",
            "version_id",
            "job_id",
        ):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload)


def configure_logging(level: str) -> None:
    logger = logging.getLogger("leaseguard")
    logger.setLevel(level)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
