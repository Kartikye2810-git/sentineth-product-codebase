import hashlib
import logging
from pathlib import Path
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.models import Document, DocumentChunk
from app.errors import DocumentProcessingError, ExtractionFailed
from app.providers.embeddings.base import EmbeddingProvider
from app.providers.storage.base import StorageProvider
from app.providers.vector.base import VectorStore
from app.services.ingestion_service import ingest_document


logger = logging.getLogger(__name__)


async def delete_document(db: Session, organization_id: UUID, document_id: UUID, storage_provider: StorageProvider, vector_store: VectorStore) -> None:
    document = db.get(Document, document_id)
    if document is None or document.organization_id != organization_id:
        raise LookupError("Document not found.")
    await vector_store.delete_document(str(organization_id), str(document_id))
    await storage_provider.delete(document.storage_path)
    db.delete(document)
    db.commit()


async def reindex_document(db: Session, organization_id: UUID, document_id: UUID, embedding_provider: EmbeddingProvider, vector_store: VectorStore) -> Document:
    document = db.get(Document, document_id)
    if document is None or document.organization_id != organization_id:
        raise LookupError("Document not found.")
    await vector_store.delete_document(str(organization_id), str(document_id))
    db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
    db.flush()
    try:
        await ingest_document(db, document, embedding_provider, vector_store)
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(document)
    return document


async def _discard_stored_file(storage_provider: StorageProvider, path: str) -> None:
    """Best effort: a cleanup failure must not mask the original one."""
    if not path:
        return

    try:
        await storage_provider.delete(path)
    except Exception:
        logger.exception("Could not delete the stored file at %s", path)


def _compute_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


async def save_document(
    db: Session,
    organization_id: UUID,
    file: UploadFile,
    storage_provider: StorageProvider,
    embedding_provider: EmbeddingProvider,
    vector_store: VectorStore,
) -> Document:

    file_content = await file.read()

    if not file_content:
        raise ExtractionFailed("Uploaded document is empty.")

    content_hash = _compute_sha256(file_content)

    filename = Path(
        file.filename or "unnamed_file"
    ).name

    document = Document(
        organization_id=organization_id,
        filename=filename,
        content_type=(
            file.content_type
            or "application/octet-stream"
        ),
        file_size=len(file_content),
        # Placeholder. The real path needs document.id, which only exists
        # after the flush below, and is set before anything commits.
        storage_path="",
        content_hash=content_hash,
        status="UPLOADED",
        error_message=None,
        error_code=None,
    )

    # Flush before writing any bytes, so the row and its id exist first. The
    # id is what makes the storage path unique, and a row without a file is
    # recoverable in a way a file without a row is not.
    db.add(document)
    db.flush()

    stored_path = ""

    try:
        stored_path = await storage_provider.save(
            organization_id=str(organization_id),
            document_id=str(document.id),
            filename=filename,
            content=file_content,
        )
        document.storage_path = stored_path
        db.flush()
    except Exception as exc:
        logger.exception(
            "Storing the uploaded file failed for document %s",
            document.id,
            extra={"document_id": str(document.id)},
        )

        db.rollback()

        raise DocumentProcessingError(
            "Failed to store the uploaded document."
        ) from exc

    try:
        await ingest_document(
            db=db,
            document=document,
            embedding_provider=embedding_provider,
            vector_store=vector_store,
        )

        db.commit()
        db.refresh(document)

    except DocumentProcessingError as exc:
        logger.exception(
            "Document processing failed for document %s",
            document.id,
            extra={"document_id": str(document.id), "error_code": exc.code},
        )

        db.rollback()

        # Nothing readable will ever point at these bytes again: the row
        # is about to say FAILED and no read path serves a FAILED
        # document. Left alone they would accumulate forever.
        await _discard_stored_file(storage_provider, stored_path)

        document.status = "FAILED"
        document.storage_path = ""
        document.error_message = str(exc)
        document.error_code = exc.code

        try:
            db.add(document)
            db.commit()
        except Exception:
            db.rollback()

        raise

    return document
