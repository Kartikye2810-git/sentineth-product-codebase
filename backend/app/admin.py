"""Operator-only onboarding and account recovery. Passwords are prompted, never arguments.

python -m app.admin create-user --email owner@example.com [--organization UUID]
python -m app.admin reset-password --email owner@example.com
python -m app.admin attach-owner --email owner@example.com --organization UUID
python -m app.admin initialize-vectors
"""

import argparse
from getpass import getpass
from uuid import UUID

from sqlalchemy import select, update

from app.audit import record
from app.clock import utcnow
from app.db.database import SessionLocal
from app.db.models import Membership, Organization, User, UserSession
from app.identity_schemas import Login
from app.security import hash_password


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["create-user", "reset-password", "initialize-vectors", "attach-owner"]
    )
    parser.add_argument("--email")
    parser.add_argument(
        "--organization",
        type=UUID,
        help="Attach the account as an owner of an existing organization",
    )
    args = parser.parse_args()
    if args.action == "initialize-vectors":
        from app.dependencies import get_storage_provider, get_vector_store

        get_storage_provider()
        get_vector_store()
        print("Storage and vector collection initialized.")
        return
    if not args.email:
        parser.error("--email is required")
    if args.action == "attach-owner":
        if not args.organization:
            parser.error("--organization is required")
        from app.services.document_service import lock_organization

        with SessionLocal() as db:
            lock_organization(db, args.organization)
            user = db.scalar(
                select(User).where(
                    User.email == args.email.strip().lower(), User.disabled.is_(False)
                )
            )
            if user is None:
                parser.error("Create or recover the user account first")
            if db.scalar(
                select(Membership).where(
                    Membership.organization_id == args.organization, Membership.role == "owner"
                )
            ):
                parser.error("This organization already has a human owner; use an invitation")
            membership = db.get(Membership, (user.id, args.organization))
            if membership:
                membership.role = "owner"
            else:
                db.add(Membership(user_id=user.id, organization_id=args.organization, role="owner"))
            record(db, "operator.owner_attached", args.organization, user.id)
            db.commit()
        print("Human owner attached to the legacy organization.")
        return
    password = getpass("Password (at least 12 characters): ")
    if len(password) < 12 or len(password) > 128 or password != getpass("Confirm password: "):
        parser.error("Passwords must match and contain 12–128 characters")
    email = Login(email=args.email, password=password).email
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email).with_for_update())
        if args.action == "create-user":
            if user:
                parser.error("Account already exists; use an invitation to add membership")
            user = User(email=email, password_hash=hash_password(password))
            db.add(user)
            db.flush()
            if args.organization:
                if db.get(Organization, args.organization) is None:
                    parser.error("Organization not found")
                db.add(Membership(user_id=user.id, organization_id=args.organization, role="owner"))
            record(db, "operator.account_created", args.organization, user.id)
        else:
            if not user:
                parser.error("Account not found")
            user.password_hash = hash_password(password)
            user.disabled = False
            db.execute(
                update(UserSession)
                .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
                .values(revoked_at=utcnow())
            )
            record(db, "operator.password_reset", resource_id=user.id)
        db.commit()
    print("Account updated. The user can log in at /auth/login.")


if __name__ == "__main__":
    main()
