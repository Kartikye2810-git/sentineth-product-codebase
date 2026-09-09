import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import record
from app.clock import utcnow
from app.db.database import get_db
from app.db.models import AuthThrottle, Invitation, Membership, Organization, User, UserSession
from app.identity_schemas import (
    AcceptInvitation,
    ChangePassword,
    Login,
    SessionIssued,
    UserResponse,
)
from app.security import Principal, hash_api_key, hash_password, require_user, verify_password
from app.settings import get_settings


router = APIRouter(prefix="/auth", tags=["Accounts"])


def throttle(db, identity, limit=None):
    now = utcnow()
    minute = int(now.timestamp()) // 60
    bucket = hash_api_key(identity) + ":" + str(minute)
    if db.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    count = db.scalar(
        insert(AuthThrottle)
        .values(bucket=bucket, requests=1, expires_at=now + timedelta(minutes=2))
        .on_conflict_do_update(
            index_elements=["bucket"], set_={"requests": AuthThrottle.requests + 1}
        )
        .returning(AuthThrottle.requests)
    )
    db.execute(delete(AuthThrottle).where(AuthThrottle.expires_at < now))
    db.commit()  # Failed attempts must count too.
    if count > (limit or get_settings().auth_attempts_per_minute):
        raise HTTPException(429, "Too many authentication attempts", headers={"Retry-After": "60"})


def auth_limits(request, db, email):
    # Trust the socket peer, never caller-supplied forwarding headers.
    throttle(
        db,
        "ip:" + (request.client.host if request.client else "unknown"),
        get_settings().auth_ip_attempts_per_minute,
    )
    throttle(db, "account:" + email)


def issue_session(db, user):
    token = "sentineth_session_" + secrets.token_urlsafe(32)
    expires = utcnow() + timedelta(hours=get_settings().session_hours)
    db.add(UserSession(user_id=user.id, token_hash=hash_api_key(token), expires_at=expires))
    return SessionIssued(access_token=token, expires_at=expires)


@router.post("/login", response_model=SessionIssued)
def login(payload: Login, request: Request, db: Session = Depends(get_db)):
    auth_limits(request, db, payload.email)
    user = db.scalar(select(User).where(User.email == payload.email).with_for_update())
    if (
        not verify_password(
            user.password_hash if user else None, payload.password.get_secret_value()
        )
        or user.disabled
    ):
        record(db, "auth.login_failed")
        db.commit()
        raise HTTPException(401, "Invalid email or password")
    result = issue_session(db, user)
    record(db, "auth.login", actor=Principal("user", user.id, user_id=user.id))
    db.commit()
    return result


@router.post("/accept-invitation", response_model=SessionIssued)
def accept_invitation(payload: AcceptInvitation, request: Request, db: Session = Depends(get_db)):
    auth_limits(request, db, payload.email)
    invitation = db.scalar(
        select(Invitation).where(Invitation.token_hash == hash_api_key(payload.token))
    )
    if invitation is None:
        raise HTTPException(400, "Invalid or expired invitation")
    # Same organization lock used by membership edits. A last-owner check and
    # simultaneous invitation acceptance cannot interleave incorrectly.
    db.scalar(
        select(Organization).where(Organization.id == invitation.organization_id).with_for_update()
    )
    db.refresh(invitation, with_for_update=True)
    if (
        invitation.accepted_at
        or invitation.revoked_at
        or invitation.expires_at <= utcnow()
        or invitation.email != payload.email
    ):
        raise HTTPException(400, "Invalid or expired invitation")
    inviter = db.get(Membership, (invitation.created_by, invitation.organization_id))
    if inviter is None or inviter.role != "owner":
        raise HTTPException(400, "Invalid or expired invitation")
    user = db.scalar(select(User).where(User.email == payload.email).with_for_update())
    if user:
        if user.disabled or not verify_password(
            user.password_hash, payload.password.get_secret_value()
        ):
            raise HTTPException(401, "Invalid email or password")
    else:
        user = User(
            email=payload.email, password_hash=hash_password(payload.password.get_secret_value())
        )
        db.add(user)
        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(409, "Account changed during invitation acceptance; retry") from exc
    if db.get(Membership, (user.id, invitation.organization_id)):
        raise HTTPException(409, "User is already a member; an owner can change their role")
    db.add(
        Membership(
            user_id=user.id, organization_id=invitation.organization_id, role=invitation.role
        )
    )
    invitation.accepted_at = utcnow()
    record(
        db,
        "membership.accepted",
        invitation.organization_id,
        user.id,
        actor=Principal("user", user.id, user_id=user.id),
        role=invitation.role,
    )
    result = issue_session(db, user)
    db.commit()
    return result


@router.get("/me", response_model=UserResponse)
def me(identity: Principal = Depends(require_user), db: Session = Depends(get_db)):
    return db.get(User, identity.user_id)


@router.post("/logout", status_code=204)
def logout(identity: Principal = Depends(require_user), db: Session = Depends(get_db)):
    db.execute(
        update(UserSession).where(UserSession.id == identity.session_id).values(revoked_at=utcnow())
    )
    record(db, "auth.logout", actor=identity)
    db.commit()


@router.post("/change-password", status_code=204)
def change_password(
    payload: ChangePassword,
    request: Request,
    identity: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    throttle(db, "password:" + str(identity.user_id))
    user = db.scalar(select(User).where(User.id == identity.user_id).with_for_update())
    if not verify_password(user.password_hash, payload.current_password.get_secret_value()):
        raise HTTPException(401, "Current password is incorrect")
    user.password_hash = hash_password(payload.new_password.get_secret_value())
    db.execute(
        update(UserSession)
        .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    record(db, "auth.password_changed", actor=identity)
    db.commit()
