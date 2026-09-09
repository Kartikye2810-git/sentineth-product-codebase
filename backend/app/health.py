"""Readiness checks dependencies without creating or mutating collections."""

import asyncio
import os
from contextlib import closing
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from qdrant_client import QdrantClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.settings import get_settings


router = APIRouter(tags=["Operations"])
SCHEMA_REVISION = "b25c83e4f916"


def database_ready(db):
    try:
        if db.bind.dialect.name == "postgresql":
            db.execute(text("SET LOCAL statement_timeout = '2000ms'"))
        return db.scalar(text("SELECT version_num FROM alembic_version")) == SCHEMA_REVISION
    except Exception:
        db.rollback()
        return False


def vectors_ready():
    settings = get_settings()
    try:
        with closing(
            QdrantClient(
                url=settings.qdrant_url,
                api_key=settings.qdrant_api_key.get_secret_value() or None,
                timeout=2,
            )
        ) as client:
            params = client.get_collection(settings.collection_name).config.params
            expected = 2048 if settings.embedding_provider == "nvidia" else 384
            return (
                getattr(params.vectors, "size", None) == expected
                and bool(params.sparse_vectors and "lexical" in params.sparse_vectors)
                == settings.qdrant_hybrid
            )
    except Exception:
        return False


@router.get("/live")
def live():
    return {"status": "alive"}


@router.get("/health")
@router.get("/ready")
async def ready(db: Session = Depends(get_db)):
    database, vectors = await asyncio.gather(
        asyncio.to_thread(database_ready, db), asyncio.to_thread(vectors_ready)
    )
    path = Path(get_settings().storage_dir)
    storage = path.is_dir() and os.access(path, os.R_OK | os.W_OK | os.X_OK)
    checks = {"database": database, "vectors": vectors, "storage": storage}
    return JSONResponse(
        {"status": "ready" if all(checks.values()) else "unavailable", "checks": checks},
        status_code=200 if all(checks.values()) else 503,
    )
