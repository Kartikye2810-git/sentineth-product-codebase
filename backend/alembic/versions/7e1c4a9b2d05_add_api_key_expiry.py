"""add api key expiry

Revision ID: 7e1c4a9b2d05
Revises: 4bc9d8e2f3a1
"""
from alembic import op
import sqlalchemy as sa

revision = "7e1c4a9b2d05"
down_revision = "4bc9d8e2f3a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organization_api_keys",
        sa.Column("expires_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("organization_api_keys", "expires_at")
