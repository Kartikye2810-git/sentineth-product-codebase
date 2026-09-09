"""Durable second-pass extraction worker: python -m app.knowledge_worker."""

import argparse
import asyncio
import logging
from datetime import timedelta
from uuid import UUID

from sqlalchemy import and_, or_, select

from app.audit import record
from app.clock import utcnow
from app.db.database import SessionLocal
from app.db.models import Document, EntityMention, ExtractionProposal, KnowledgeJob
from app.dependencies import get_llm_provider
from app.logging_config import configure_logging, request_id_var
from app.observability import save_usage, usage_context
from app.services.knowledge_service import extract_proposals
from app.settings import get_settings


logger = logging.getLogger(__name__)


def claim(session_factory=SessionLocal) -> tuple[UUID, int] | None:
    settings = get_settings()
    with session_factory() as db:
        now = utcnow()
        candidate = db.execute(select(Document, KnowledgeJob).join(
            KnowledgeJob, KnowledgeJob.document_id == Document.id).where(or_(
                and_(KnowledgeJob.status == "QUEUED", KnowledgeJob.available_at <= now),
                and_(KnowledgeJob.status == "PROCESSING", KnowledgeJob.lease_until <= now),
            )).order_by(KnowledgeJob.available_at, KnowledgeJob.id).limit(1)
            .with_for_update(skip_locked=True)).first()
        if candidate is None:
            return None
        document, job = candidate
        if document is None or document.status != "READY" or document.index_generation != job.generation:
            job.status, job.error_code, job.lease_until = "FAILED", "STALE_DOCUMENT", None
            record(db, "knowledge.extraction_failed", job.organization_id, job.document_id,
                   error_code="STALE_DOCUMENT")
            db.commit()
            return None
        if job.attempts >= settings.job_max_attempts:
            job.status, job.error_code, job.lease_until = "FAILED", "RETRIES_EXHAUSTED", None
            record(db, "knowledge.extraction_failed", job.organization_id, job.document_id,
                   error_code="RETRIES_EXHAUSTED")
            db.commit()
            return None
        job.attempts += 1
        job.status = "PROCESSING"
        job.lease_until = now + timedelta(seconds=settings.job_lease_seconds)
        result = job.id, job.attempts
        db.commit()
        return result


async def run_once(session_factory=SessionLocal, provider=None) -> bool:
    selected = claim(session_factory)
    if selected is None:
        return False
    job_id, attempt = selected
    try:
        with session_factory() as db:
            document_id = db.scalar(select(KnowledgeJob.document_id).where(KnowledgeJob.id == job_id))
            document = db.scalar(select(Document).where(
                Document.id == document_id).with_for_update())
            job = db.scalar(select(KnowledgeJob).where(
                KnowledgeJob.id == job_id).with_for_update())
            if (job is None or document is None or job.status != "PROCESSING"
                    or job.attempts != attempt or job.generation != document.index_generation):
                return True
            token = request_id_var.set(job.request_id)
            usage = []
            usage_token = usage_context.set(usage)
            try:
                # A reclaimed lease repeats the whole transaction, never a partial proposal set.
                db.query(ExtractionProposal).filter(
                    ExtractionProposal.knowledge_job_id == job.id,
                    ExtractionProposal.status == "PROPOSED").delete(synchronize_session=False)
                db.query(EntityMention).filter(
                    EntityMention.knowledge_job_id == job.id,
                    EntityMention.status == "PROPOSED").delete(synchronize_session=False)
                count = await extract_proposals(db, job, provider or get_llm_provider())
            finally:
                usage_context.reset(usage_token)
                request_id_var.reset(token)
            save_usage(db, job.organization_id, usage)
            job.status, job.error_code, job.lease_until = "SUCCEEDED", None, None
            record(db, "knowledge.extraction_proposed", job.organization_id, job.document_id,
                   job_id=str(job.id), proposals=count)
            db.commit()
    except Exception:
        logger.exception("Knowledge extraction failed", extra={"job_id": str(job_id),
                                                                "attempt": attempt})
        with session_factory() as db:
            job = db.scalar(select(KnowledgeJob).where(
                KnowledgeJob.id == job_id).with_for_update())
            if job is None or job.status != "PROCESSING" or job.attempts != attempt:
                return True
            retry = attempt < get_settings().job_max_attempts
            job.status = "QUEUED" if retry else "FAILED"
            job.error_code = "PROVIDER_UNAVAILABLE" if retry else "EXTRACTION_FAILED"
            job.lease_until = None
            job.available_at = utcnow() + timedelta(
                seconds=get_settings().job_retry_seconds * 2 ** (attempt - 1))
            record(db, "knowledge.extraction_retry" if retry else "knowledge.extraction_failed",
                   job.organization_id, job.document_id, job_id=str(job.id),
                   error_code=job.error_code, attempt=attempt)
            db.commit()
    return True


async def serve(once=False):
    while True:
        worked = await run_once()
        if once:
            return
        if not worked:
            await asyncio.sleep(get_settings().worker_poll_seconds)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    get_settings().validate_runtime("knowledge-worker")
    configure_logging()
    asyncio.run(serve(parser.parse_args().once))
