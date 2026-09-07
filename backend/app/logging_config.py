"""Structured JSON logging.

One JSON object per line, so log lines can be parsed without a regex per
message format. Every line emitted while handling a request carries that
request's id, which is what makes a traceback traceable back to the call
that produced it.
"""

import json
import logging
import os
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any


request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


# Attributes every LogRecord already has. Anything else on the record came
# from logger.info(..., extra={...}) and belongs in the JSON payload.
_STANDARD_RECORD_ATTRS = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", (), None))
) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        request_id = request_id_var.get()
        if request_id is not None:
            payload["request_id"] = request_id

        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_ATTRS:
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def configure_logging(level: str | None = None) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level or os.getenv("LOG_LEVEL", "INFO").upper())

    # The request middleware emits one structured line per request, so
    # uvicorn's plain-text access log would only duplicate it in a second
    # format.
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers = []
    access_logger.propagate = False
