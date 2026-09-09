"""Read source provenance using the same tenant boundary as document APIs."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import Document, Source
from app.security import require_organization_access


class SourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    origin: str
    namespace: str
    external_id: str
    uri: str | None
    sync_state: str
    error_code: str | None
    created_by_user_id: UUID | None
    created_at: datetime
    updated_at: datetime
    last_synced_at: datetime | None
    deleted_at: datetime | None
    document_id: UUID | None = None


class SourceListResponse(BaseModel):
    items: list[SourceResponse]
    total: int
    limit: int
    offset: int


SyncState = Literal["PENDING", "PROCESSING", "SYNCED", "FAILED", "DELETING", "DELETED"]
router = APIRouter(
    prefix="/organizations/{organization_id}/sources",
    tags=["Sources"],
    dependencies=[Depends(require_organization_access)],
)


def source_rows(organization_id):
    return (
        select(Source, Document.id)
        .outerjoin(
            Document,
            (Document.source_id == Source.id)
            & (Document.organization_id == Source.organization_id),
        )
        .where(Source.organization_id == organization_id)
    )


def response(row):
    source, document_id = row
    return SourceResponse.model_validate(source).model_copy(update={"document_id": document_id})


@router.get("", response_model=SourceListResponse)
def list_sources(
    organization_id: UUID,
    db: Session = Depends(get_db),
    origin: str | None = Query(None, max_length=50),
    sync_state: SyncState | None = None,
    include_deleted: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    statement = source_rows(organization_id)
    if origin is not None:
        statement = statement.where(Source.origin == origin)
    if sync_state is not None:
        statement = statement.where(Source.sync_state == sync_state)
    # An explicit DELETED filter is sufficient to request tombstones.
    if not include_deleted and sync_state != "DELETED":
        statement = statement.where(Source.deleted_at.is_(None))
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = db.execute(
        statement.order_by(Source.created_at.desc(), Source.id).limit(limit).offset(offset)
    )
    return SourceListResponse(
        items=[response(row) for row in rows], total=total, limit=limit, offset=offset
    )


@router.get("/{source_id}", response_model=SourceResponse)
def get_source(organization_id: UUID, source_id: UUID, db: Session = Depends(get_db)):
    row = db.execute(source_rows(organization_id).where(Source.id == source_id)).first()
    if row is None:
        raise HTTPException(404, "Source not found")
    return response(row)
