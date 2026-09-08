"""add chunk page number

Revision ID: d5a2e8b91c34
Revises: b3f6c1d47e20
"""
from alembic import op
import sqlalchemy as sa

revision = "d5a2e8b91c34"
down_revision = "b3f6c1d47e20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable, and left null for existing rows rather than backfilled:
    # the page a chunk came from cannot be recovered from the chunk, only
    # by re-ingesting the document it belongs to.
    op.add_column(
        "document_chunks",
        sa.Column("page_number", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("document_chunks", "page_number")
