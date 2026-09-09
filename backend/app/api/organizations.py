"""Tenant administration. Human owners control membership; scoped keys automate work."""

import secrets
from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import record
from app.clock import utcnow
from app.db.database import get_db
from app.db.models import (
    AuditEvent,
    Invitation,
    Membership,
    Organization,
    OrganizationApiKey,
    UsageRecord,
    User,
)
from app.identity_schemas import (
    AuditResponse,
    InviteRequest,
    KeyCreate,
    MembershipChange,
    MembershipResponse,
)
from app.schemas import (
    ApiKeyIssued,
    ApiKeyResponse,
    ApiKeyRotateRequest,
    OrganizationCreate,
    OrganizationResponse,
)
from app.security import (
    Principal,
    hash_api_key,
    new_api_key,
    require_human_owner,
    require_organization_access,
    require_owner,
    require_user,
)
from app.services.document_service import lock_organization
from app.settings import get_settings


router = APIRouter(prefix="/organizations", tags=["Organizations"])


def issued(key, token):
    return ApiKeyIssued(**ApiKeyResponse.model_validate(key).model_dump(), api_key=token)


@router.post("", response_model=OrganizationResponse)
def create_organization(
    payload: OrganizationCreate,
    identity: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    db.scalar(select(User).where(User.id == identity.user_id).with_for_update())
    count = db.scalar(
        select(func.count())
        .select_from(Membership)
        .where(Membership.user_id == identity.user_id, Membership.role == "owner")
    )
    if count >= get_settings().max_organizations_per_user:
        raise HTTPException(429, "Organization limit reached")
    org = Organization(name=payload.name)
    db.add(org)
    db.flush()
    db.add(Membership(user_id=identity.user_id, organization_id=org.id, role="owner"))
    token = new_api_key()
    db.add(
        OrganizationApiKey(
            organization_id=org.id,
            token_hash=hash_api_key(token),
            role="owner",
            label="Initial key",
        )
    )
    record(db, "organization.created", org.id, org.id)
    db.commit()
    return OrganizationResponse.model_validate(org).model_copy(update={"api_key": token})


@router.get("", response_model=list[OrganizationResponse])
def list_organizations(identity: Principal = Depends(require_user), db: Session = Depends(get_db)):
    return list(
        db.scalars(
            select(Organization)
            .join(Membership)
            .where(Membership.user_id == identity.user_id)
            .order_by(Organization.created_at)
        )
    )


@router.get("/{organization_id}/api-keys", response_model=list[ApiKeyResponse])
def list_keys(
    organization_id: UUID,
    identity: Principal = Depends(require_owner),
    db: Session = Depends(get_db),
):
    return list(
        db.scalars(
            select(OrganizationApiKey)
            .where(OrganizationApiKey.organization_id == organization_id)
            .order_by(OrganizationApiKey.created_at)
        )
    )


@router.post("/{organization_id}/api-keys", response_model=ApiKeyIssued, status_code=201)
def create_key(
    organization_id: UUID,
    payload: KeyCreate,
    identity: Principal = Depends(require_owner),
    db: Session = Depends(get_db),
):
    token = new_api_key()
    key = OrganizationApiKey(
        organization_id=organization_id, token_hash=hash_api_key(token), **payload.model_dump()
    )
    db.add(key)
    db.flush()
    record(db, "api_key.created", organization_id, key.id, role=key.role)
    db.commit()
    return issued(key, token)


@router.post("/{organization_id}/api-keys/rotate", response_model=ApiKeyIssued)
def rotate_key(
    organization_id: UUID,
    payload: ApiKeyRotateRequest | None = None,
    identity: Principal = Depends(require_organization_access),
    db: Session = Depends(get_db),
):
    if identity.actor_type != "api_key":
        raise HTTPException(
            400, "Authenticate with the API key being rotated; accounts can create a new key"
        )
    key = db.scalar(
        select(OrganizationApiKey)
        .where(OrganizationApiKey.id == identity.actor_id)
        .with_for_update()
    )
    if not key.active:
        raise HTTPException(403, "API key is inactive")
    token = new_api_key()
    # Rotation cannot increase role or extend a credential's existing lifetime.
    expiry = payload.expires_at if payload else key.expires_at
    if key.expires_at and (expiry is None or expiry > key.expires_at):
        raise HTTPException(400, "An owner must create a key to extend its expiry")
    replacement = OrganizationApiKey(
        organization_id=organization_id,
        token_hash=hash_api_key(token),
        role=key.role,
        label=key.label,
        expires_at=expiry,
    )
    db.add(replacement)
    db.flush()
    key.revoked_at = utcnow()
    record(
        db,
        "api_key.rotated",
        organization_id,
        key.id,
        replacement_id=str(replacement.id),
        role=key.role,
    )
    db.commit()
    return issued(replacement, token)


@router.delete("/{organization_id}/api-keys/{key_id}", status_code=204)
def revoke_key(
    organization_id: UUID,
    key_id: UUID,
    identity: Principal = Depends(require_owner),
    db: Session = Depends(get_db),
):
    key = db.scalar(
        select(OrganizationApiKey)
        .where(
            OrganizationApiKey.id == key_id, OrganizationApiKey.organization_id == organization_id
        )
        .with_for_update()
    )
    if key is None:
        raise HTTPException(404, "API key not found for this organization")
    if key.revoked_at is None:
        key.revoked_at = utcnow()
        record(db, "api_key.revoked", organization_id, key.id)
        db.commit()


@router.post("/{organization_id}/invitations", status_code=201)
def invite(
    organization_id: UUID,
    payload: InviteRequest,
    identity: Principal = Depends(require_human_owner),
    db: Session = Depends(get_db),
):
    lock_organization(db, organization_id)
    # Recheck after the lock: another owner may have removed this owner.
    ensure_owner(db, identity, organization_id)
    pending = db.scalar(
        select(func.count())
        .select_from(Invitation)
        .where(
            Invitation.organization_id == organization_id,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
            Invitation.expires_at > utcnow(),
        )
    )
    if pending >= 100:
        raise HTTPException(429, "Too many pending invitations")
    token = secrets.token_urlsafe(32)
    invitation = Invitation(
        organization_id=organization_id,
        email=payload.email,
        role=payload.role,
        token_hash=hash_api_key(token),
        created_by=identity.user_id,
        expires_at=utcnow() + timedelta(hours=get_settings().invitation_hours),
    )
    db.add(invitation)
    db.flush()
    record(db, "invitation.created", organization_id, invitation.id, role=payload.role)
    db.commit()
    return {
        "id": invitation.id,
        "email": invitation.email,
        "role": invitation.role,
        "token": token,
        "expires_at": invitation.expires_at,
    }


@router.get("/{organization_id}/invitations")
def invitations(
    organization_id: UUID,
    identity: Principal = Depends(require_human_owner),
    db: Session = Depends(get_db),
):
    values = db.scalars(
        select(Invitation)
        .where(Invitation.organization_id == organization_id)
        .order_by(Invitation.expires_at.desc())
        .limit(100)
    )
    return [
        {
            "id": i.id,
            "email": i.email,
            "role": i.role,
            "expires_at": i.expires_at,
            "accepted_at": i.accepted_at,
            "revoked_at": i.revoked_at,
        }
        for i in values
    ]


@router.delete("/{organization_id}/invitations/{invitation_id}", status_code=204)
def revoke_invitation(
    organization_id: UUID,
    invitation_id: UUID,
    identity: Principal = Depends(require_human_owner),
    db: Session = Depends(get_db),
):
    lock_organization(db, organization_id)
    ensure_owner(db, identity, organization_id)
    invitation = db.scalar(
        select(Invitation)
        .where(Invitation.id == invitation_id, Invitation.organization_id == organization_id)
        .with_for_update()
    )
    if invitation is None:
        raise HTTPException(404, "Invitation not found")
    if not invitation.revoked_at:
        invitation.revoked_at = utcnow()
        record(db, "invitation.revoked", organization_id, invitation.id)
        db.commit()


def ensure_owner(db, identity, organization_id):
    member = db.scalar(
        select(Membership)
        .where(
            Membership.user_id == identity.user_id, Membership.organization_id == organization_id
        )
        .execution_options(populate_existing=True)
    )
    if member is None or member.role != "owner":
        raise HTTPException(403, "Owner membership required")


@router.get("/{organization_id}/members", response_model=list[MembershipResponse])
def members(
    organization_id: UUID,
    identity: Principal = Depends(require_human_owner),
    db: Session = Depends(get_db),
):
    return [
        MembershipResponse(user_id=m.user_id, email=u.email, role=m.role)
        for m, u in db.execute(
            select(Membership, User).join(User).where(Membership.organization_id == organization_id)
        )
    ]


def edit_membership(db, organization_id, user_id, identity, role):
    lock_organization(db, organization_id)
    ensure_owner(db, identity, organization_id)
    target = db.get(Membership, (user_id, organization_id), populate_existing=True)
    if target is None:
        raise HTTPException(404, "Member not found")
    owners = db.scalar(
        select(func.count())
        .select_from(Membership)
        .where(Membership.organization_id == organization_id, Membership.role == "owner")
    )
    if target.role == "owner" and role != "owner" and owners <= 1:
        raise HTTPException(409, "An organization must retain at least one human owner")
    previous = target.role
    if role is None:
        db.delete(target)
    else:
        target.role = role
    # Pending invitations issued by a removed/demoted owner cannot restore access.
    if previous == "owner" and role != "owner":
        for invitation in db.scalars(
            select(Invitation).where(
                Invitation.organization_id == organization_id,
                Invitation.created_by == user_id,
                Invitation.accepted_at.is_(None),
                Invitation.revoked_at.is_(None),
            )
        ):
            invitation.revoked_at = utcnow()
    record(
        db,
        "membership.removed" if role is None else "membership.role_changed",
        organization_id,
        user_id,
        previous_role=previous,
        role=role,
    )
    db.commit()


@router.patch("/{organization_id}/members/{user_id}", status_code=204)
def change_member(
    organization_id: UUID,
    user_id: UUID,
    payload: MembershipChange,
    identity: Principal = Depends(require_human_owner),
    db: Session = Depends(get_db),
):
    edit_membership(db, organization_id, user_id, identity, payload.role)


@router.delete("/{organization_id}/members/{user_id}", status_code=204)
def remove_member(
    organization_id: UUID,
    user_id: UUID,
    identity: Principal = Depends(require_human_owner),
    db: Session = Depends(get_db),
):
    edit_membership(db, organization_id, user_id, identity, None)


@router.get("/{organization_id}/audit-events", response_model=list[AuditResponse])
def audit_events(
    organization_id: UUID,
    identity: Principal = Depends(require_owner),
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    return list(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.organization_id == organization_id)
            .order_by(AuditEvent.created_at.desc(), AuditEvent.id)
            .limit(limit)
            .offset(offset)
        )
    )


@router.get("/{organization_id}/usage")
def usage(
    organization_id: UUID,
    identity: Principal = Depends(require_owner),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(
            UsageRecord.model,
            func.count(),
            func.sum(UsageRecord.prompt_tokens),
            func.sum(UsageRecord.completion_tokens),
            func.sum(UsageRecord.reported_cost_usd),
            func.count(UsageRecord.reported_cost_usd),
        )
        .where(UsageRecord.organization_id == organization_id)
        .group_by(UsageRecord.model)
    )
    return [
        {
            "model": model,
            "requests": count,
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "reported_cost_usd": cost,
            "requests_with_reported_cost": cost_count,
        }
        for model, count, prompt, completion, cost, cost_count in rows
    ]
