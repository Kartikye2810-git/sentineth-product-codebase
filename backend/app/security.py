"""Opaque human sessions and scoped machine keys; membership is checked per request."""

import hashlib
import secrets
from dataclasses import dataclass
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.db.database import get_db
from app.db.models import Membership, OrganizationApiKey, User, UserSession


bearer_scheme = HTTPBearer(auto_error=False)
password_hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
_dummy_hash = password_hasher.hash(secrets.token_urlsafe(32))
ROLES = {"viewer": 0, "member": 1, "owner": 2}


def new_api_key() -> str:
    return f"sentineth_{secrets.token_urlsafe(32)}"


def hash_api_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(encoded: str | None, password: str) -> bool:
    try:
        valid = password_hasher.verify(encoded or _dummy_hash, password)
        return bool(encoded) and valid
    except (VerificationError, InvalidHashError):
        return False


@dataclass(frozen=True)
class Principal:
    actor_type: str
    actor_id: UUID
    user_id: UUID | None = None
    session_id: UUID | None = None
    organization_id: UUID | None = None
    role: str | None = None


def authenticate(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Principal:
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or len(credentials.credentials) > 256
    ):
        raise HTTPException(
            401, "Bearer credential required", headers={"WWW-Authenticate": "Bearer"}
        )
    token = credentials.credentials
    digest = hash_api_key(token)
    if token.startswith("sentineth_session_"):
        row = db.scalar(
            select(UserSession).where(
                UserSession.token_hash == digest,
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > utcnow(),
            )
        )
        user = db.get(User, row.user_id) if row else None
        if user is None or user.disabled:
            raise HTTPException(403, "Credential is not authorized")
        identity = Principal("user", user.id, user_id=user.id, session_id=row.id)
    else:
        key = db.scalar(select(OrganizationApiKey).where(OrganizationApiKey.token_hash == digest))
        if key is None or not key.active:
            raise HTTPException(403, "Credential is not authorized")
        identity = Principal("api_key", key.id, organization_id=key.organization_id, role=key.role)
    db.info["actor"] = identity
    request.state.actor = identity
    return identity


def require_user(identity: Principal = Depends(authenticate)) -> Principal:
    if identity.user_id is None:
        raise HTTPException(403, "A user session is required")
    return identity


def require_organization_access(
    organization_id: UUID,
    request: Request,
    identity: Principal = Depends(authenticate),
    db: Session = Depends(get_db),
) -> Principal:
    if identity.user_id:
        membership = db.get(Membership, (identity.user_id, organization_id))
        if membership is None:
            raise HTTPException(403, "Credential is not authorized for this organization")
        identity = Principal(
            "user",
            identity.user_id,
            user_id=identity.user_id,
            session_id=identity.session_id,
            organization_id=organization_id,
            role=membership.role,
        )
    elif identity.organization_id != organization_id:
        raise HTTPException(403, "Credential is not authorized for this organization")
    db.info["actor"] = identity
    request.state.actor = identity
    return identity


def require_organization_role(role: str):
    def check(identity: Principal = Depends(require_organization_access)):
        if ROLES.get(identity.role, -1) < ROLES[role]:
            raise HTTPException(403, f"{role.title()} role required")
        return identity

    return check


require_member = require_organization_role("member")
require_owner = require_organization_role("owner")


def require_human_member(identity: Principal = Depends(require_member)):
    if not identity.user_id:
        raise HTTPException(403, "A user member session is required")
    return identity


def require_human_owner(identity: Principal = Depends(require_owner)):
    if not identity.user_id:
        raise HTTPException(403, "A user owner session is required")
    return identity
