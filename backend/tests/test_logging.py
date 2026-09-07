"""Structured logging.

These tests read the log output the way an aggregator would: one JSON
object per line. That is the contract worth protecting — a human-readable
message is not enough to find every line belonging to one failed request.
"""

import io
import json
import logging
from datetime import datetime

import pytest

from app.logging_config import JsonFormatter, request_id_var


@pytest.fixture
def log_stream():
    """Capture root log output through the real JSON formatter."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    previous_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    yield stream

    root.removeHandler(handler)
    root.setLevel(previous_level)


def read_lines(stream) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line]


def test_log_lines_are_json_objects_carrying_their_extras(log_stream):
    logging.getLogger("test.logging").info(
        "document stored",
        extra={"document_id": "abc-123"},
    )

    (line,) = read_lines(log_stream)

    assert line["level"] == "INFO"
    assert line["logger"] == "test.logging"
    assert line["message"] == "document stored"
    assert line["document_id"] == "abc-123"
    assert datetime.fromisoformat(line["timestamp"]).tzinfo is not None
    # No request in flight, so no id to attach.
    assert "request_id" not in line


def test_exceptions_are_logged_with_their_traceback(log_stream):
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("test.logging").exception("ingestion failed")

    (line,) = read_lines(log_stream)

    assert line["level"] == "ERROR"
    assert "ValueError: boom" in line["exception"]


def test_a_request_logs_one_line_tagged_with_the_returned_request_id(
    client, log_stream
):
    response = client.get("/health")

    assert response.status_code == 200

    completed = [
        line for line in read_lines(log_stream) if line["message"] == "request completed"
    ]

    assert len(completed) == 1
    assert completed[0]["request_id"] == response.headers["X-Request-ID"]
    assert completed[0]["method"] == "GET"
    assert completed[0]["path"] == "/health"
    assert completed[0]["status_code"] == 200
    assert completed[0]["duration_ms"] >= 0


def test_an_inbound_request_id_is_reused_rather_than_replaced(client, log_stream):
    response = client.get("/health", headers={"X-Request-ID": "trace-me"})

    assert response.headers["X-Request-ID"] == "trace-me"

    completed = [
        line for line in read_lines(log_stream) if line["message"] == "request completed"
    ]

    assert [line["request_id"] for line in completed] == ["trace-me"]


def test_a_failed_upload_logs_a_traceback_tagged_with_the_request_id(
    client, organization, log_stream
):
    org_id = organization()

    response = client.post(
        f"/organizations/{org_id}/documents",
        files=(("file", ("notes.txt", b"plain text notes", "text/plain")),),
    )

    assert response.status_code == 415

    tracebacks = [line for line in read_lines(log_stream) if "exception" in line]

    assert tracebacks, "a failed ingest should log a traceback"
    assert all(
        "app.errors.UnsupportedMediaType" in line["exception"] for line in tracebacks
    )
    assert all(
        line["request_id"] == response.headers["X-Request-ID"] for line in tracebacks
    )
    # Only the service that classifies the failure tags the line with a code.
    assert any(
        line.get("error_code") == "UNSUPPORTED_MEDIA_TYPE" for line in tracebacks
    )


def test_the_request_id_contextvar_is_cleared_between_requests(client, log_stream):
    client.get("/health")

    assert request_id_var.get() is None
