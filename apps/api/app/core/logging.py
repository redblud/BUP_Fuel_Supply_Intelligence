import json
import logging
from datetime import UTC, datetime

# Fields callers attach with `extra=`; anything else stays out of the log line.
CONTEXT_FIELDS = ("run_id", "tick", "decision_id", "allocation_id", "component", "error_code")


class JsonFormatter(logging.Formatter):
    """One JSON object per line with the standard context fields, so logs can be filtered by run, tick, decision, or allocation."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        entry.update({f: getattr(record, f) for f in CONTEXT_FIELDS if getattr(record, f, None) is not None})
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(level: str, fmt: str = "json") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if fmt == "json" else logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.basicConfig(level=level, handlers=[handler], force=True)
