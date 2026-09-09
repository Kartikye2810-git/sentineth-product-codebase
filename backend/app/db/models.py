from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.clock import utcnow
from app.db.database import Base


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[UUID] = mapped_column(
        primary_key=True,
        default=uuid4,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utcnow,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    documents: Mapped[list["Document"]] = relationship(
        back_populates="organization",
        cascade="all, delete-orphan",
    )
    api_keys: Mapped[list["OrganizationApiKey"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )


class Source(Base):
    """Stable ingestion identity; payloads may disappear while provenance survives."""
    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_source_tenant"),
        UniqueConstraint("organization_id", "origin", "namespace", "external_id", name="uq_source_origin"),
        CheckConstraint("sync_state IN ('PENDING','PROCESSING','SYNCED','FAILED','DELETING','DELETED')", name="source_sync_state"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    origin: Mapped[str] = mapped_column(String(50), nullable=False)
    # Future connector account/workspace scope, not a credential or connection URL.
    namespace: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    external_id: Mapped[str] = mapped_column(String(500), nullable=False)
    uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    sync_state: Mapped[str] = mapped_column(String(20), default="PENDING", nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        ForeignKeyConstraint(["source_id", "organization_id"], ["sources.id", "sources.organization_id"], name="fk_document_source_tenant"),
        UniqueConstraint("source_id", name="uq_documents_source_id"),
    )
    source_id: Mapped[UUID] = mapped_column(nullable=False)


    id: Mapped[UUID] = mapped_column(
        primary_key=True,
        default=uuid4,
    )

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"),
        nullable=False,
        index=True,
    )

    filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    content_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    file_size: Mapped[int] = mapped_column(
        nullable=False,
    )

    storage_path: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        default="UPLOADED",
        nullable=False,
    )

    created_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    index_generation: Mapped[int] = mapped_column(default=0, nullable=False)

    content_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Stable, categorised reason a document failed. error_message is free
    # text for operators; this is what callers are allowed to branch on.
    error_code: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utcnow,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    organization: Mapped["Organization"] = relationship(
        back_populates="documents",
    )

    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
    )

    @property
    def chunk_count(self) -> int:
        return len(self.chunks)


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[UUID] = mapped_column(
        primary_key=True,
        default=uuid4,
    )

    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id"),
        nullable=False,
        index=True,
    )

    chunk_index: Mapped[int] = mapped_column(
        nullable=False,
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # Nullable because chunks written before this column existed have no
    # page to point at, and because a source without pages (a pasted note,
    # a connector payload) is a shape this table will have to hold later.
    page_number: Mapped[int | None] = mapped_column(
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utcnow,
        nullable=False,
    )

    document: Mapped["Document"] = relationship(
        back_populates="chunks",
    )


class KnowledgeJob(Base):
    """A durable, independently retryable extraction pass for one document generation."""

    __tablename__ = "knowledge_jobs"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_knowledge_job_tenant"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True, nullable=False
    )
    generation: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="QUEUED", nullable=False)
    attempts: Mapped[int] = mapped_column(default=0, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)


class Entity(Base):
    """A reviewer-confirmed company concept. Names are not globally unique."""

    __tablename__ = "entities"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_entity_tenant"),
        CheckConstraint(
            "entity_type IN ('person','project','decision')", name="entity_type"
        ),
        CheckConstraint("status IN ('CONFIRMED','ARCHIVED')", name="entity_status"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="entity_confidence"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True, nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="CONFIRMED", nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class EntityMention(Base):
    """A source occurrence, separate from the canonical entity it may resolve to."""

    __tablename__ = "entity_mentions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["entity_id", "organization_id"],
            ["entities.id", "entities.organization_id"],
            name="fk_mention_entity_tenant",
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="mention_confidence"),
        CheckConstraint("status IN ('PROPOSED','LINKED','REJECTED','WITHDRAWN')", name="mention_status"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(index=True, nullable=False)
    entity_id: Mapped[UUID | None] = mapped_column(index=True)
    chunk_id: Mapped[UUID] = mapped_column(
        ForeignKey("document_chunks.id", ondelete="CASCADE"), index=True, nullable=False
    )
    knowledge_job_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_jobs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    mention_text: Mapped[str] = mapped_column(String(255), nullable=False)
    proposed_name: Mapped[str] = mapped_column(String(255), nullable=False)
    proposed_type: Mapped[str] = mapped_column(String(30), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="PROPOSED", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class EntityEvidence(Base):
    __tablename__ = "entity_evidence"

    entity_id: Mapped[UUID] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True
    )
    chunk_id: Mapped[UUID] = mapped_column(
        ForeignKey("document_chunks.id", ondelete="CASCADE"), primary_key=True
    )


class Relationship(Base):
    """A temporal, reviewer-confirmed edge backed by one or more chunks."""

    __tablename__ = "relationships"
    __table_args__ = (
        ForeignKeyConstraint(
            ["subject_id", "organization_id"],
            ["entities.id", "entities.organization_id"],
            name="fk_relationship_subject_tenant",
        ),
        ForeignKeyConstraint(
            ["object_id", "organization_id"],
            ["entities.id", "entities.organization_id"],
            name="fk_relationship_object_tenant",
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="relationship_confidence"),
        CheckConstraint("status IN ('CONFIRMED','ARCHIVED','WITHDRAWN')", name="relationship_status"),
        CheckConstraint("subject_id <> object_id", name="relationship_distinct_ends"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True, nullable=False
    )
    subject_id: Mapped[UUID] = mapped_column(index=True)
    predicate: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    object_id: Mapped[UUID] = mapped_column(index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="CONFIRMED", nullable=False)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    created_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class RelationshipEvidence(Base):
    __tablename__ = "relationship_evidence"

    relationship_id: Mapped[UUID] = mapped_column(
        ForeignKey("relationships.id", ondelete="CASCADE"), primary_key=True
    )
    chunk_id: Mapped[UUID] = mapped_column(
        ForeignKey("document_chunks.id", ondelete="CASCADE"), primary_key=True
    )


class ExtractionProposal(Base):
    """Raw LLM output awaiting a human decision; it never enters query facts directly."""

    __tablename__ = "extraction_proposals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["knowledge_job_id", "organization_id"],
            ["knowledge_jobs.id", "knowledge_jobs.organization_id"],
            name="fk_proposal_job_tenant",
        ),
        CheckConstraint("kind IN ('entity','relationship')", name="proposal_kind"),
        CheckConstraint(
            "status IN ('PROPOSED','ACCEPTED','REJECTED','WITHDRAWN')",
            name="proposal_status",
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="proposal_confidence"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(index=True, nullable=False)
    knowledge_job_id: Mapped[UUID] = mapped_column(index=True, nullable=False)
    chunk_id: Mapped[UUID] = mapped_column(
        ForeignKey("document_chunks.id", ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="PROPOSED", nullable=False)
    resolved_entity_id: Mapped[UUID | None] = mapped_column(ForeignKey("entities.id"))
    resolved_relationship_id: Mapped[UUID | None] = mapped_column(ForeignKey("relationships.id"))
    reviewed_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)



class OrganizationApiKey(Base):
    __tablename__ = "organization_api_keys"
    __table_args__ = (
        CheckConstraint("role IN ('owner','member','viewer')", name="api_key_role"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # A key carries a role like a membership does, so an integration can be
    # given read-only access without a second authentication mechanism.
    role: Mapped[str] = mapped_column(String(20), default="owner", nullable=False)
    label: Mapped[str] = mapped_column(String(100), default="API key", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    organization: Mapped["Organization"] = relationship(back_populates="api_keys")

    @property
    def active(self) -> bool:
        """Whether this key may still authenticate a request.

        Defined once here so authentication and the api-keys listing cannot
        drift apart on what "usable" means.
        """
        return self.revoked_at is None and (
            self.expires_at is None or self.expires_at > utcnow()
        )


class IngestionJob(Base):
    """One durable work item per document; retries reuse the same generation."""
    __tablename__ = "ingestion_jobs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True, nullable=False
    )
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    operation: Mapped[str] = mapped_column(String(20), nullable=False, default="INGEST")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="QUEUED")
    attempts: Mapped[int] = mapped_column(default=0, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)


class OrganizationRateLimit(Base):
    __tablename__ = "organization_rate_limits"

    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), primary_key=True)
    operation: Mapped[str] = mapped_column(String(20), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    requests: Mapped[int] = mapped_column(nullable=False, default=0)


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    disabled: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        CheckConstraint("role IN ('owner','member','viewer')", name="membership_role"),
    )

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Invitation(Base):
    __tablename__ = "invitations"
    __table_args__ = (
        CheckConstraint("role IN ('owner','member','viewer')", name="invitation_role"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)


class AuthThrottle(Base):
    __tablename__ = "auth_throttles"

    bucket: Mapped[str] = mapped_column(String(100), primary_key=True)
    requests: Mapped[int] = mapped_column(nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID | None] = mapped_column(index=True, nullable=True)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[UUID | None] = mapped_column(nullable=True)
    action: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    resource_id: Mapped[UUID | None] = mapped_column(nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, index=True, nullable=False
    )


class UsageRecord(Base):
    __tablename__ = "usage_records"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(index=True, nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(nullable=False)
    completion_tokens: Mapped[int] = mapped_column(nullable=False)
    reported_cost_usd: Mapped[float | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, index=True, nullable=False
    )
