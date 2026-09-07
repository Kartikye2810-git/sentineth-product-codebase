"""Organization-scoped API-key authentication for the MVP API."""

import hashlib
import secrets
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import OrganizationApiKey


bearer_scheme = HTTPBearer(auto_error=False)


def new_api_key() -> str:
    return f"sentineth_{secrets.token_urlsafe(32)}"


def hash_api_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def require_organization_access(
    organization_id: UUID,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> OrganizationApiKey:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer API key required")

    key = db.scalar(select(OrganizationApiKey).where(
        OrganizationApiKey.organization_id == organization_id,
        OrganizationApiKey.token_hash == hash_api_key(credentials.credentials),
        OrganizationApiKey.revoked_at.is_(None),
    ))
    # Expiry is checked in Python so an expired key is indistinguishable from
    # an unknown one from the caller's side.
    if key is None or not key.active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="API key is not authorized for this organization")
    return key
