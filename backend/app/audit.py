"""Append-only events saved in the same transaction as the change they describe."""

from sqlalchemy import event

from app.db.models import AuditEvent
from app.logging_config import request_id_var


def record(db, action, organization_id=None, resource_id=None, actor=None, **details):
    identity = actor or db.info.get("actor")
    db.add(
        AuditEvent(
            organization_id=organization_id,
            action=action,
            resource_id=resource_id,
            actor_type=identity.actor_type if identity else "system",
            actor_id=identity.actor_id if identity else None,
            request_id=request_id_var.get(),
            details=details,
        )
    )


@event.listens_for(AuditEvent, "before_update")
@event.listens_for(AuditEvent, "before_delete")
def immutable_event(*args):
    raise ValueError("Audit events are append-only")
