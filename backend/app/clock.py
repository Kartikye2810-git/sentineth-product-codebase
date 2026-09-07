"""Single time source for the application.

datetime.utcnow() is deprecated and scheduled for removal, but the DateTime
columns in app.db.models are naive. Returning naive UTC keeps stored values
identical to what utcnow() produced, so this is a drop-in replacement rather
than a semantic change. Moving the columns to timezone-aware DateTime is a
separate piece of work.
"""

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
