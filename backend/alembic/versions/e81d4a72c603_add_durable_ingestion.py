"""Durable document jobs and organization resource accounting."""
from alembic import op
import sqlalchemy as sa

revision = "e81d4a72c603"
down_revision = "d5a2e8b91c34"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("index_generation", sa.Integer(), nullable=False, server_default="0"))
    op.alter_column("documents", "index_generation", server_default=None)
    op.create_table("ingestion_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id", ondelete="CASCADE"), unique=True, nullable=False),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("operation", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(), nullable=False),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.Column("error_code", sa.String(50), nullable=True),
    )
    op.create_index("ix_ingestion_jobs_organization_id", "ingestion_jobs", ["organization_id"])
    op.create_index("ix_ingestion_jobs_available_at", "ingestion_jobs", ["available_at"])
    op.create_table("organization_rate_limits",
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id"), primary_key=True),
        sa.Column("operation", sa.String(20), primary_key=True),
        sa.Column("window_start", sa.DateTime(), nullable=False),
        sa.Column("requests", sa.Integer(), nullable=False),
    )


def downgrade():
    op.drop_table("organization_rate_limits")
    op.drop_table("ingestion_jobs")
    op.drop_column("documents", "index_generation")
