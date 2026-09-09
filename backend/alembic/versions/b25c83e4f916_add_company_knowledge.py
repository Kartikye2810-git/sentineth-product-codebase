"""Entities, temporal relationships, review proposals and extraction jobs."""

from alembic import op
import sqlalchemy as sa


revision = "b25c83e4f916"
down_revision = "a14b72d3e805"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "knowledge_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id", ondelete="CASCADE"), unique=True, nullable=False),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(), nullable=False),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.Column("error_code", sa.String(50), nullable=True),
        sa.Column("request_id", sa.String(128), nullable=True),
        sa.UniqueConstraint("id", "organization_id", name="uq_knowledge_job_tenant"),
    )
    op.create_index("ix_knowledge_jobs_organization_id", "knowledge_jobs", ["organization_id"])
    op.create_index("ix_knowledge_jobs_available_at", "knowledge_jobs", ["available_at"])
    op.create_table(
        "entities",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("entity_type", sa.String(30), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("normalized_name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("valid_from", sa.DateTime(), nullable=True),
        sa.Column("valid_to", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("id", "organization_id", name="uq_entity_tenant"),
        sa.CheckConstraint("entity_type IN ('person','project','decision')", name="entity_type"),
        sa.CheckConstraint("status IN ('CONFIRMED','ARCHIVED')", name="entity_status"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="entity_confidence"),
    )
    op.create_index("ix_entities_organization_id", "entities", ["organization_id"])
    op.create_index("ix_entities_normalized_name", "entities", ["normalized_name"])
    op.create_table(
        "relationships",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("predicate", sa.String(80), nullable=False),
        sa.Column("object_id", sa.Uuid(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("valid_from", sa.DateTime(), nullable=True),
        sa.Column("valid_to", sa.DateTime(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.ForeignKeyConstraint(["subject_id", "organization_id"], ["entities.id", "entities.organization_id"], name="fk_relationship_subject_tenant"),
        sa.ForeignKeyConstraint(["object_id", "organization_id"], ["entities.id", "entities.organization_id"], name="fk_relationship_object_tenant"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="relationship_confidence"),
        sa.CheckConstraint("status IN ('CONFIRMED','ARCHIVED','WITHDRAWN')", name="relationship_status"),
        sa.CheckConstraint("subject_id <> object_id", name="relationship_distinct_ends"),
    )
    for column in ("organization_id", "subject_id", "predicate", "object_id"):
        op.create_index(f"ix_relationships_{column}", "relationships", [column])
    op.create_table(
        "entity_mentions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("chunk_id", sa.Uuid(), sa.ForeignKey("document_chunks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("knowledge_job_id", sa.Uuid(), sa.ForeignKey("knowledge_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("mention_text", sa.String(255), nullable=False),
        sa.Column("proposed_name", sa.String(255), nullable=False),
        sa.Column("proposed_type", sa.String(30), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["entity_id", "organization_id"], ["entities.id", "entities.organization_id"], name="fk_mention_entity_tenant"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="mention_confidence"),
        sa.CheckConstraint("status IN ('PROPOSED','LINKED','REJECTED','WITHDRAWN')", name="mention_status"),
    )
    for column in ("organization_id", "entity_id", "chunk_id", "knowledge_job_id"):
        op.create_index(f"ix_entity_mentions_{column}", "entity_mentions", [column])
    op.create_table(
        "entity_evidence",
        sa.Column("entity_id", sa.Uuid(), sa.ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("chunk_id", sa.Uuid(), sa.ForeignKey("document_chunks.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_table(
        "extraction_proposals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_job_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), sa.ForeignKey("document_chunks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("resolved_entity_id", sa.Uuid(), sa.ForeignKey("entities.id"), nullable=True),
        sa.Column("resolved_relationship_id", sa.Uuid(), sa.ForeignKey("relationships.id"), nullable=True),
        sa.Column("reviewed_by_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["knowledge_job_id", "organization_id"], ["knowledge_jobs.id", "knowledge_jobs.organization_id"], name="fk_proposal_job_tenant"),
        sa.CheckConstraint("kind IN ('entity','relationship')", name="proposal_kind"),
        sa.CheckConstraint("status IN ('PROPOSED','ACCEPTED','REJECTED','WITHDRAWN')", name="proposal_status"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="proposal_confidence"),
    )
    for column in ("organization_id", "knowledge_job_id", "chunk_id"):
        op.create_index(f"ix_extraction_proposals_{column}", "extraction_proposals", [column])
    op.create_table(
        "relationship_evidence",
        sa.Column("relationship_id", sa.Uuid(), sa.ForeignKey("relationships.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("chunk_id", sa.Uuid(), sa.ForeignKey("document_chunks.id", ondelete="CASCADE"), primary_key=True),
    )


def downgrade():
    for table in (
        "relationship_evidence",
        "extraction_proposals",
        "entity_evidence",
        "entity_mentions",
        "relationships",
        "entities",
        "knowledge_jobs",
    ):
        op.drop_table(table)
