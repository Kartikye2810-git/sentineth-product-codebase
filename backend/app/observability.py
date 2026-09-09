"""Bounded operational metrics; never include document text, prompts or credentials."""

import hmac
from contextvars import ContextVar

from fastapi import Depends, HTTPException, Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import record
from app.clock import utcnow
from app.db.database import get_db
from app.db.models import AuditEvent, IngestionJob, KnowledgeJob, UsageRecord
from app.logging_config import request_id_var
from app.settings import get_settings


registry = CollectorRegistry()
requests = Counter(
    "sentineth_http_requests", "HTTP responses", ["method", "route", "status"], registry=registry
)
latency = Histogram(
    "sentineth_http_duration_seconds", "HTTP latency", ["method", "route"], registry=registry
)
retrieval = Counter(
    "sentineth_retrieval_requests", "Retrieval hit rate", ["operation", "hit"], registry=registry
)
jobs = Gauge("sentineth_jobs", "Durable jobs by state", ["state"], registry=registry)
knowledge_jobs = Gauge(
    "sentineth_knowledge_jobs", "Knowledge extraction jobs by state", ["state"], registry=registry
)
processed = Gauge(
    "sentineth_document_events",
    "Durable cumulative document outcomes",
    ["action"],
    registry=registry,
)
queue_age = Gauge(
    "sentineth_queue_oldest_seconds", "Age of the oldest available queued job", registry=registry
)
usage_context: ContextVar[list | None] = ContextVar("llm_usage", default=None)


def capture_usage(response, model):
    pending = usage_context.get()
    usage = getattr(response, "usage", None)
    if pending is None or usage is None:
        return
    cost = getattr(usage, "cost", None)
    pending.append(
        dict(
            model=str(getattr(response, "model", None) or model)[:200],
            prompt_tokens=int(usage.prompt_tokens or 0),
            completion_tokens=int(usage.completion_tokens or 0),
            reported_cost_usd=float(cost) if isinstance(cost, (float, int)) and cost >= 0 else None,
        )
    )


def save_usage(db, organization_id, usage):
    for item in usage:
        db.add(
            UsageRecord(organization_id=organization_id, request_id=request_id_var.get(), **item)
        )


def finish_query(db, organization_id, operation, hits, usage, failed=False):
    save_usage(db, organization_id, usage)
    record(db, operation + (".failed" if failed else ".completed"), organization_id, hits=hits)
    db.commit()
    if not failed:
        retrieval.labels(operation, str(bool(hits)).lower()).inc()


def metrics_auth(request: Request):
    expected = get_settings().metrics_token.get_secret_value()
    supplied = request.headers.get("authorization", "")
    if not expected or not hmac.compare_digest(supplied, "Bearer " + expected):
        raise HTTPException(401, "Metrics credential required")


def metrics(db: Session = Depends(get_db), _: None = Depends(metrics_auth)):
    oldest = db.scalar(
        select(func.min(IngestionJob.available_at)).where(IngestionJob.status == "QUEUED")
    )
    queue_age.set(max(0, (utcnow() - oldest).total_seconds()) if oldest else 0)
    counts = dict(
        db.execute(select(IngestionJob.status, func.count()).group_by(IngestionJob.status)).all()
    )
    for state in ("QUEUED", "PROCESSING", "SUCCEEDED", "FAILED"):
        jobs.labels(state).set(counts.get(state, 0))
    knowledge_counts = dict(
        db.execute(select(KnowledgeJob.status, func.count()).group_by(KnowledgeJob.status)).all()
    )
    for state in ("QUEUED", "PROCESSING", "SUCCEEDED", "FAILED"):
        knowledge_jobs.labels(state).set(knowledge_counts.get(state, 0))
    counts = dict(
        db.execute(
            select(AuditEvent.action, func.count())
            .where(
                AuditEvent.action.in_(("document.indexed", "document.deleted", "document.failed"))
            )
            .group_by(AuditEvent.action)
        ).all()
    )
    for action in ("document.indexed", "document.deleted", "document.failed"):
        processed.labels(action).set(counts.get(action, 0))
    return Response(generate_latest(registry), headers={"Content-Type": CONTENT_TYPE_LATEST})


def configure_error_tracking():
    settings = get_settings()
    if not settings.sentry_dsn.get_secret_value():
        return
    import sentry_sdk

    def scrub(event, hint):
        # Keep exception type and stack locations, remove arbitrary provider text.
        event.pop("request", None)
        event.pop("breadcrumbs", None)
        event.pop("extra", None)
        event.pop("user", None)
        for value in event.get("exception", {}).get("values", []):
            value["value"] = "[redacted]"
            for frame in value.get("stacktrace", {}).get("frames", []):
                frame.pop("vars", None)
        event.setdefault("tags", {})["request_id"] = request_id_var.get()
        return event

    sentry_sdk.init(
        dsn=settings.sentry_dsn.get_secret_value(),
        environment=settings.environment,
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        traces_sample_rate=0,
        before_send=scrub,
    )
