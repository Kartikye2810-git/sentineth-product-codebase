"""add document error code

Revision ID: b3f6c1d47e20
Revises: 7e1c4a9b2d05
"""
from alembic import op
import sqlalchemy as sa

revision = "b3f6c1d47e20"
down_revision = "7e1c4a9b2d05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("error_code", sa.String(length=50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("documents", "error_code")
