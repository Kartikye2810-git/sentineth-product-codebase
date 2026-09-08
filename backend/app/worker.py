"""Durable Postgres worker: python -m app.worker (or --once for one job).

A committed claim exposes PROCESSING. During execution a row lock fences the
worker from concurrent deletion/reindex and lease recovery. A crashed process
releases that lock; another worker can reclaim its expired lease. SQLite is
supported for serial unit tests only, not for running concurrent workers.
"""
import argparse
import asyncio
import logging
from datetime import timedelta
from uuid import UUID

from sqlalchemy import and_, delete, or_, select

from app.clock import utcnow
from app.db.database import SessionLocal
from app.db.models import Document, DocumentChunk, IngestionJob
from app.dependencies import get_embedding_provider, get_storage_provider, get_vector_store
from app.errors import DocumentProcessingError, ProviderUnavailable
from app.logging_config import configure_logging
from app.services.ingestion_service import ingest_document
from app.settings import get_settings


logger = logging.getLogger(__name__)


def claim(session_factory=SessionLocal) -> tuple[UUID, int] | None:
    settings = get_settings()
    with session_factory() as db:
        now = utcnow()
        # Lock BOTH rows, in document-before-job order, to match HTTP lifecycle
        # operations and to prevent reclaiming a slow but still active worker.
        candidate = db.execute(select(Document, IngestionJob).join(
            IngestionJob, IngestionJob.document_id == Document.id).where(or_(
                and_(IngestionJob.status == "QUEUED", IngestionJob.available_at <= now),
                and_(IngestionJob.status == "PROCESSING", IngestionJob.lease_until <= now),
            )).order_by(IngestionJob.available_at, IngestionJob.id).limit(1)
            .with_for_update(skip_locked=True)).first()
        if candidate is None:
            return None
        document, job = candidate
        if job.attempts >= settings.job_max_attempts:
            job.status, job.error_code = "FAILED", "RETRIES_EXHAUSTED"
            document.status, document.error_code = "FAILED", "RETRIES_EXHAUSTED"
            job.lease_until = None
            db.commit()
            return None
        job.attempts += 1
        job.status = "PROCESSING"
        job.lease_until = now + timedelta(seconds=settings.job_lease_seconds)
        document.status = "DELETING" if job.operation == "DELETE" else "PROCESSING"
        result = (job.id, job.attempts)
        db.commit()
        return result


async def run_once(session_factory=SessionLocal, embedding_provider=None,
                   vector_store=None, storage_provider=None) -> bool:
    selected = claim(session_factory)
    if selected is None:
        return False
    job_id, attempt = selected
    try:
        with session_factory() as db:
            # Lock the document first, consistent with queue_existing.
            document_id = db.scalar(select(IngestionJob.document_id).where(IngestionJob.id == job_id))
            document = db.scalar(select(Document).where(Document.id == document_id).with_for_update())
            job = db.scalar(select(IngestionJob).where(IngestionJob.id == job_id).with_for_update())
            if document is None or job is None or job.status != "PROCESSING" or job.attempts != attempt:
                return True
            store = vector_store or get_vector_store()
            storage = storage_provider or get_storage_provider()
            # Repeated cleanup is safe, including after a partial upsert/crash.
            await store.delete_document(str(document.organization_id), str(document.id))
            db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
            if job.operation == "DELETE":
                await storage.delete(document.storage_path)
                db.delete(job)
                db.flush()
                db.delete(document)
            else:
                provider = embedding_provider or get_embedding_provider()
                await ingest_document(db, document, provider, store)
                job.status, job.error_code, job.lease_until = "SUCCEEDED", None, None
            db.commit()
    except Exception as exc:
        code = exc.code if isinstance(exc, DocumentProcessingError) else "PROVIDER_UNAVAILABLE"
        retryable = isinstance(exc, ProviderUnavailable) or not isinstance(exc, DocumentProcessingError)
        logger.exception("Document job failed", extra={"job_id": str(job_id), "attempt": attempt, "error_code": code})
        with session_factory() as db:
            document_id = db.scalar(select(IngestionJob.document_id).where(IngestionJob.id == job_id))
            document = db.scalar(select(Document).where(Document.id == document_id).with_for_update())
            job = db.scalar(select(IngestionJob).where(IngestionJob.id == job_id).with_for_update())
            if job is None or job.status != "PROCESSING" or job.attempts != attempt:
                return True
            retry = retryable and attempt < get_settings().job_max_attempts
            job.status = "QUEUED" if retry else "FAILED"
            job.error_code, job.lease_until = code, None
            job.available_at = utcnow() + timedelta(seconds=get_settings().job_retry_seconds * 2 ** (attempt - 1))
            if document:
                document.status = ("DELETING" if job.operation == "DELETE" else "QUEUED") if retry else "FAILED"
                document.error_code = code
                document.error_message = "Processing failed; retry scheduled." if retry else "Processing failed. Reindex or delete this document."
            db.commit()
    return True


async def serve(once=False):
    while True:
        try:
            worked = await run_once()
        except Exception:
            logger.exception("Worker database unavailable")
            if once:
                raise
            worked = False
        if once:
            return
        if not worked:
            await asyncio.sleep(get_settings().worker_poll_seconds)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    configure_logging()
    asyncio.run(serve(parser.parse_args().once))
