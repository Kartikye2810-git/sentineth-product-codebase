import logging
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db.database import get_db
from app.db.models import Document
from app.dependencies import (
    get_embedding_provider,
    get_llm_provider,
    get_storage_provider,
    get_vector_store,
)
from app.errors import DocumentProcessingError
from app.providers.embeddings.base import EmbeddingProvider
from app.providers.llm.base import LLMProvider
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
from app.services.document_service import delete_document, reindex_document, save_document
from app.services.query_service import answer_query
from app.services.retrieval_service import retrieve


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/organizations",
    tags=["Documents"],
)

@router.get(
    "/{organization_id}/documents",
    response_model=DocumentListResponse,
)
def list_documents(
    organization_id: UUID,
    _: object = Depends(require_organization_access),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    total = db.scalar(
        select(func.count(Document.id)).where(
            Document.organization_id == organization_id
        )
    )

    documents = db.scalars(
        select(Document)
        .where(Document.organization_id == organization_id)
        # chunk_count reads document.chunks; without this the listing is
        # one lazy load per document.
        .options(selectinload(Document.chunks))
        .order_by(Document.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()

    return DocumentListResponse(
        items=[DocumentResponse.model_validate(document) for document in documents],
        total=total or 0,
        limit=limit,
        offset=offset,
    )

@router.delete("/{organization_id}/documents/{document_id}", status_code=204)
async def remove_document(organization_id: UUID, document_id: UUID, _: object = Depends(require_organization_access), db: Session = Depends(get_db), storage_provider: StorageProvider = Depends(get_storage_provider), vector_store: VectorStore = Depends(get_vector_store)):
    try:
        await delete_document(db, organization_id, document_id, storage_provider, vector_store)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@router.post(
    "/{organization_id}/documents/{document_id}/reindex",
    response_model=DocumentResponse,
)
async def reindex_one_document(organization_id: UUID, document_id: UUID, _: object = Depends(require_organization_access), db: Session = Depends(get_db), embedding_provider: EmbeddingProvider = Depends(get_embedding_provider), vector_store: VectorStore = Depends(get_vector_store)):
    try:
        document = await reindex_document(db, organization_id, document_id, embedding_provider, vector_store)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DocumentProcessingError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error_code": exc.code, "message": str(exc)},
        ) from exc

    return DocumentResponse.model_validate(document)


@router.post(
    "/{organization_id}/documents",
    response_model=DocumentUploadResponse,
)
async def upload_document(
    organization_id: UUID,
    _: object = Depends(require_organization_access),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    storage_provider: StorageProvider = Depends(get_storage_provider),
    embedding_provider: EmbeddingProvider = Depends(get_embedding_provider),
    vector_store: VectorStore = Depends(get_vector_store),
):
    try:
        document = await save_document(
            db=db,
            organization_id=organization_id,
            file=file,
            storage_provider=storage_provider,
            embedding_provider=embedding_provider,
            vector_store=vector_store,
        )
    except DocumentProcessingError as exc:
        # exc carries its own status and code, so the route does not
        # re-derive the mapping and every failure answers consistently.
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error_code": exc.code, "message": str(exc)},
        ) from exc

    if document.status == "READY":
        message = "Document uploaded and processed successfully."
    elif document.status == "FAILED":
        message = "Document uploaded but processing failed."
    else:
        message = "Document uploaded and is being processed."

    return DocumentUploadResponse(
        **DocumentResponse.model_validate(document).model_dump(),
        message=message,
    )


@router.post(
    "/{organization_id}/search",
    response_model=SearchResponse,
)
async def search_documents(
    organization_id: UUID,
    payload: SearchRequest,
    _: object = Depends(require_organization_access),
    embedding_provider: EmbeddingProvider = Depends(
        get_embedding_provider
    ),
    vector_store: VectorStore = Depends(
        get_vector_store
    ),
):
    try:
        results = await retrieve(
            organization_id=organization_id,
            query=payload.query,
            embedding_provider=embedding_provider,
            vector_store=vector_store,
            limit=payload.limit,
        )

    except ValueError as exc:
        logger.exception("Search validation error")

        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        logger.exception(
            "Search failed for organization %s",
            organization_id,
        )

        raise HTTPException(
            status_code=500,
            detail=f"Search failed: {exc}",
        ) from exc

    return {
        "query": payload.query,
        "results": results,
    }


@router.post(
    "/{organization_id}/query",
    response_model=QueryResponse,
)
async def query_documents(
    organization_id: UUID,
    payload: QueryRequest,
    _: object = Depends(require_organization_access),
    embedding_provider: EmbeddingProvider = Depends(
        get_embedding_provider
    ),
    vector_store: VectorStore = Depends(
        get_vector_store
    ),
    llm_provider: LLMProvider = Depends(
        get_llm_provider
    ),
):
    try:
        result = await answer_query(
            organization_id=organization_id,
            query=payload.query,
            embedding_provider=embedding_provider,
            vector_store=vector_store,
            llm_provider=llm_provider,
            limit=payload.limit,
        )

        return result

    except ValueError as exc:
        logger.exception("Query validation error")

        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        logger.exception(
            "Query processing failed for organization %s",
            organization_id,
        )

        raise HTTPException(
            status_code=500,
            detail=f"Query processing failed: {exc}",
        ) from exc
