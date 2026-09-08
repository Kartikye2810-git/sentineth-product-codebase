"""HTTP-side queue admission. No embeddings or vector calls run during upload."""
import asyncio
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.db.models import Document, IngestionJob, Organization, OrganizationRateLimit
from app.errors import DocumentBusy, QuotaExceeded, UnsupportedMediaType
from app.providers.storage.base import StorageProvider
from app.settings import get_settings


PENDING = ("QUEUED", "PROCESSING", "DELETING")


def lock_organization(db: Session, organization_id: UUID):
    # Serialize admission across API processes; counts and increments are atomic.
    org = db.scalar(select(Organization).where(Organization.id == organization_id).with_for_update())
    if org is None:
        raise LookupError("Organization not found.")


def consume_rate(db: Session, organization_id: UUID, operation: str, limit: int):
    now = utcnow()
    row = db.get(OrganizationRateLimit, (organization_id, operation))
    if row is None:
        row = OrganizationRateLimit(organization_id=organization_id, operation=operation,
                                    window_start=now, requests=0)
        db.add(row)
    if now >= row.window_start + timedelta(minutes=1):
        row.window_start, row.requests = now, 0
    if row.requests >= limit:
        raise QuotaExceeded("Organization request rate limit reached. Retry in a minute.")
    row.requests += 1


def check_job_capacity(db: Session, organization_id: UUID):
    count = db.scalar(select(func.count(Document.id)).where(
        Document.organization_id == organization_id, Document.status.in_(PENDING))) or 0
    if count >= get_settings().max_pending_jobs_per_org:
        raise QuotaExceeded("Organization has too many pending document jobs.")


def queue_document(db: Session, organization_id: UUID, file: UploadFile,
                   storage_provider: StorageProvider) -> Document:
    if file.content_type != "application/pdf":
        raise UnsupportedMediaType("Only PDF documents are supported right now.")
    settings = get_settings()
    path = ""
    try:
        lock_organization(db, organization_id)
        consume_rate(db, organization_id, "upload", settings.uploads_per_minute)
        check_job_capacity(db, organization_id)
        count, size = db.execute(select(func.count(Document.id), func.coalesce(func.sum(Document.file_size), 0))
            .where(Document.organization_id == organization_id)).one()
        if count >= settings.max_documents_per_org or size >= settings.max_storage_bytes_per_org:
            raise QuotaExceeded("Organization document storage quota reached.")
        document_id = uuid4()
        path, byte_count, digest = storage_provider.save_stream(
            str(organization_id), str(document_id), file.filename or "document.pdf", file.file,
            min(settings.max_upload_bytes, settings.max_storage_bytes_per_org - size))
        document = Document(id=document_id, organization_id=organization_id,
            filename=Path((file.filename or "document.pdf").replace("\\", "/")).name[:255],
            content_type="application/pdf", file_size=byte_count, storage_path=path,
            content_hash=digest, status="QUEUED", index_generation=1)
        db.add(document)
        db.flush()
        db.add(IngestionJob(document_id=document.id, organization_id=organization_id))
        db.commit()  # The document and its job become durable together.
        db.refresh(document)
        return document
    except BaseException:
        db.rollback()
        if path:
            asyncio.run(storage_provider.delete(path))
        raise


def get_document(db: Session, organization_id: UUID, document_id: UUID, *, lock=False) -> Document:
    statement = select(Document).where(Document.id == document_id,
                                      Document.organization_id == organization_id)
    if lock:
        statement = statement.with_for_update(nowait=True)
    try:
        document = db.scalar(statement)
    except OperationalError as exc:
        db.rollback()
        if getattr(exc.orig, "sqlstate", None) == "55P03":
            raise DocumentBusy("Document is being processed. Retry shortly.") from exc
        raise
    if document is None:
        raise LookupError("Document not found.")
    return document


def queue_existing(db: Session, organization_id: UUID, document_id: UUID, operation: str) -> Document:
    try:
        lock_organization(db, organization_id)
        document = get_document(db, organization_id, document_id, lock=True)
        job = db.scalar(select(IngestionJob).where(IngestionJob.document_id == document_id))
        # A claimed worker may be between transactions. Do not cancel its lease.
        if job and job.status == "PROCESSING":
            raise DocumentBusy("Document is being processed. Retry shortly.")
        if operation == "INGEST":
            if document.status in PENDING:
                raise DocumentBusy("Document already has pending work.")
            if not document.storage_path:
                raise DocumentBusy("Source file is unavailable; upload the document again.")
            consume_rate(db, organization_id, "upload", get_settings().uploads_per_minute)
            check_job_capacity(db, organization_id)
            document.index_generation += 1
        elif document.status not in PENDING:
            check_job_capacity(db, organization_id)
        if job is None:
            job = IngestionJob(document_id=document.id, organization_id=organization_id)
            db.add(job)
        job.operation, job.status, job.attempts = operation, "QUEUED", 0
        job.available_at, job.lease_until, job.error_code = utcnow(), None, None
        document.status = "DELETING" if operation == "DELETE" else "QUEUED"
        document.error_code = document.error_message = None
        db.commit()
        db.refresh(document)
        return document
    except BaseException:
        db.rollback()
        raise
