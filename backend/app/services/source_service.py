"""Source state changes share the document/job transaction and row lock."""

from sqlalchemy.orm import Session

from app.clock import utcnow
from app.db.models import Document, Source


def set_sync_state(db: Session, document: Document, state: str, error_code: str | None = None):
    source = db.get(Source, document.source_id)
    if source is None or source.organization_id != document.organization_id:
        raise RuntimeError("Document has no matching tenant source")
    source.sync_state = state
    source.error_code = error_code
    if state == "SYNCED":
        source.last_synced_at = utcnow()
    elif state == "DELETED":
        source.deleted_at = utcnow()
