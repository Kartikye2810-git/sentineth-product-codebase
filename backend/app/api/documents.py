import asyncio
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db.database import get_db
from app.db.models import Document
from app.dependencies import (
    get_embedding_provider,
    get_llm_provider,
    get_rerank_provider,
    get_storage_provider,
    get_vector_store,
)
from app.errors import DocumentProcessingError, ProviderUnavailable
from app.providers.embeddings.base import EmbeddingProvider
from app.providers.llm.base import LLMProvider
from app.providers.rerank.base import RerankProvider
from app.providers.storage.base import StorageProvider
from app.providers.vector.base import VectorStore
from app.schemas import (
    DocumentListResponse,
    DocumentResponse,
    DocumentUploadResponse,
    QueryRequest,
    QueryResponse,
    SearchRequest,
    SearchResponse,
)
from app.security import require_organization_access
from app.services.document_service import (
    consume_rate,
    get_document,
    lock_organization,
    queue_document,
    queue_existing,
)
from app.services.query_service import answer_query
from app.services.retrieval_service import retrieve
from app.settings import get_settings


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/organizations", tags=["Documents"], dependencies=[Depends(require_organization_access)])


def query_admission(organization_id: UUID, db: Session = Depends(get_db)) -> list[str]:
    try:
        lock_organization(db, organization_id)
        consume_rate(db, organization_id, "query", get_settings().queries_per_minute)
        db.commit()
        # SQL is the authority on visibility. Failed/queued/deleting or orphaned
        # vectors must never enter the answer context.
        return [str(value) for value in db.scalars(select(Document.id).where(
            Document.organization_id == organization_id, Document.status == "READY"))]
    except Exception:
        db.rollback()
        raise


@router.get("/{organization_id}/documents", response_model=DocumentListResponse)
def list_documents(organization_id: UUID, db: Session = Depends(get_db),
                   limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    total = db.scalar(select(func.count(Document.id)).where(Document.organization_id == organization_id)) or 0
    documents = db.scalars(select(Document).where(Document.organization_id == organization_id)
        .options(selectinload(Document.chunks)).order_by(Document.created_at.desc(), Document.id)
        .limit(limit).offset(offset)).all()
    return DocumentListResponse(items=[DocumentResponse.model_validate(d) for d in documents],
                                total=total, limit=limit, offset=offset)


@router.get("/{organization_id}/documents/{document_id}", response_model=DocumentResponse)
def document_status(organization_id: UUID, document_id: UUID, db: Session = Depends(get_db)):
    return DocumentResponse.model_validate(get_document(db, organization_id, document_id))


@router.post("/{organization_id}/documents", status_code=202, response_model=DocumentUploadResponse)
def upload_document(organization_id: UUID, response: Response, file: UploadFile = File(...),
                    db: Session = Depends(get_db), storage: StorageProvider = Depends(get_storage_provider)):
    document = queue_document(db, organization_id, file, storage)
    response.headers["Location"] = f"/organizations/{organization_id}/documents/{document.id}"
    response.headers["Retry-After"] = "1"
    return DocumentUploadResponse(**DocumentResponse.model_validate(document).model_dump(),
                                  message="Document queued for processing.")


@router.post("/{organization_id}/documents/{document_id}/reindex", status_code=202, response_model=DocumentResponse)
def reindex_one_document(organization_id: UUID, document_id: UUID, response: Response,
                         db: Session = Depends(get_db)):
    document = queue_existing(db, organization_id, document_id, "INGEST")
    response.headers["Location"] = f"/organizations/{organization_id}/documents/{document.id}"
    return DocumentResponse.model_validate(document)


@router.delete("/{organization_id}/documents/{document_id}", status_code=202, response_model=DocumentResponse)
def remove_document(organization_id: UUID, document_id: UUID, response: Response,
                    db: Session = Depends(get_db)):
    document = queue_existing(db, organization_id, document_id, "DELETE")
    response.headers["Location"] = f"/organizations/{organization_id}/documents/{document.id}"
    return DocumentResponse.model_validate(document)


@router.post("/{organization_id}/search", response_model=SearchResponse)
async def search_documents(organization_id: UUID, payload: SearchRequest,
    ready: list[str] = Depends(query_admission),
    embedding: EmbeddingProvider = Depends(get_embedding_provider),
    vectors: VectorStore = Depends(get_vector_store),
    reranker: RerankProvider | None = Depends(get_rerank_provider)):
    try:
        results = await asyncio.wait_for(retrieve(organization_id, payload.query, embedding,
            vectors, payload.limit, reranker, document_ids=ready), get_settings().provider_timeout_seconds)
        return SearchResponse(query=payload.query, results=results)
    except DocumentProcessingError:
        raise
    except ValueError as exc:
        raise HTTPException(400, "Invalid search request.") from exc
    except Exception as exc:
        logger.exception("Search provider failed")
        raise ProviderUnavailable("Search provider is unavailable. Retry shortly.") from exc


@router.post("/{organization_id}/query", response_model=QueryResponse)
async def query_documents(organization_id: UUID, payload: QueryRequest,
    ready: list[str] = Depends(query_admission),
    embedding: EmbeddingProvider = Depends(get_embedding_provider),
    vectors: VectorStore = Depends(get_vector_store),
    llm: LLMProvider = Depends(get_llm_provider),
    reranker: RerankProvider | None = Depends(get_rerank_provider)):
    try:
        return await asyncio.wait_for(answer_query(organization_id, payload.query, embedding,
            vectors, llm, payload.limit, reranker, document_ids=ready), get_settings().provider_timeout_seconds)
    except DocumentProcessingError:
        raise
    except ValueError as exc:
        raise HTTPException(400, "Invalid query request.") from exc
    except Exception as exc:
        logger.exception("Answer provider failed")
        raise ProviderUnavailable("Answer provider is unavailable. Retry shortly.") from exc
