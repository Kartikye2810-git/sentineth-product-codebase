import logging
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Document, DocumentChunk
from app.errors import (
    DocumentProcessingError,
    ExtractionFailed,
    ProviderUnavailable,
    UnsupportedMediaType,
)
from app.providers.embeddings.base import EmbeddingProvider
from app.providers.vector.base import VectorStore
from app.services.chunking_service import chunk_text
from app.services.extraction_service import extract_text_from_pdf


logger = logging.getLogger(__name__)


async def ingest_document(
    db: Session,
    document: Document,
    embedding_provider: EmbeddingProvider,
    vector_store: VectorStore,
) -> list[DocumentChunk]:
    """Chunk, embed and index a document.

    Flushes but never commits or rolls back: the caller owns the
    transaction boundary. A failure here has to be undone together with
    the document row itself, and only the caller can see both.
    """
    document.status = "PROCESSING"
    document.error_message = None
    document.error_code = None

    try:
        if document.content_type != "application/pdf":
            raise UnsupportedMediaType(
                "Only PDF documents are supported right now."
            )

        # Extract text from the stored document.
        try:
            text = extract_text_from_pdf(
                document.storage_path
            )
        except Exception as exc:
            raise ExtractionFailed(
                "Could not read text from the document."
            ) from exc

        # Split extracted text into chunks.
        chunks = chunk_text(text)

        if not chunks:
            raise ExtractionFailed(
                "Document produced no usable chunks."
            )

        # Create database chunk records.
        document_chunks: list[DocumentChunk] = []

        for index, content in enumerate(chunks):
            chunk = DocumentChunk(
                document_id=document.id,
                chunk_index=index,
                content=content,
            )

            db.add(chunk)
            document_chunks.append(chunk)

        # Flush so chunk IDs are generated before creating
        # the Qdrant payloads.
        db.flush()

        # Generate embeddings for every chunk.
        chunk_texts = [
            chunk.content
            for chunk in document_chunks
        ]

        try:
            embeddings = await embedding_provider.embed(
                chunk_texts
            )
        except Exception as exc:
            raise ProviderUnavailable(
                "Embedding provider is unavailable."
            ) from exc

        if len(embeddings) != len(document_chunks):
            raise DocumentProcessingError(
                "Embedding count does not match chunk count."
            )

        # Build organization-scoped Qdrant payloads.
        payloads: list[dict[str, Any]] = []

        organization_id = str(
            document.organization_id
        )

        for chunk in document_chunks:
            payloads.append(
                {
                    "organization_id": organization_id,
                    "document_id": str(document.id),
                    "chunk_id": str(chunk.id),
                    "chunk_index": chunk.chunk_index,
                    "content": chunk.content,
                    "filename": document.filename,
                }
            )

        # Store embeddings in Qdrant.
        try:
            await vector_store.upsert(
                organization_id=organization_id,
                vectors=embeddings,
                payloads=payloads,
            )
        except Exception as exc:
            raise ProviderUnavailable(
                "Vector store is unavailable."
            ) from exc

        # Everything succeeded.
        document.status = "READY"
        document.error_message = None
        document.error_code = None

        return document_chunks

    except DocumentProcessingError:
        logger.exception(
            "Ingestion failed for document %s",
            document.id,
            extra={"document_id": str(document.id)},
        )

        raise

    except Exception as exc:
        logger.exception(
            "Ingestion failed for document %s",
            document.id,
            extra={"document_id": str(document.id)},
        )

        raise DocumentProcessingError(
            "Document processing failed."
        ) from exc