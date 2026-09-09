"""Public knowledge shapes. Raw model output is validated before persistence."""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


EntityType = Literal["person", "project", "decision"]
ReviewDecision = Literal["accept", "reject"]


def naive_utc(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


class EntityCreate(BaseModel):
    entity_type: EntityType
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(None, max_length=4000)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    evidence_chunk_ids: list[UUID] = Field(min_length=1, max_length=20)

    _times = field_validator("valid_from", "valid_to")(naive_utc)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        return " ".join(value.split())

    @model_validator(mode="after")
    def valid_period(self):
        if self.valid_from and self.valid_to and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be after valid_from")
        if len(set(self.evidence_chunk_ids)) != len(self.evidence_chunk_ids):
            raise ValueError("Evidence chunks must be unique")
        return self


class EntityUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=4000)
    status: Literal["CONFIRMED", "ARCHIVED"] | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    _times = field_validator("valid_from", "valid_to")(naive_utc)


class EvidenceResponse(BaseModel):
    chunk_id: UUID
    document_id: UUID
    source_id: UUID
    source_origin: str
    source_created_at: datetime
    filename: str
    page_number: int | None
    excerpt: str


class EntityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    entity_type: EntityType
    name: str
    description: str | None
    status: str
    confidence: float
    valid_from: datetime | None
    valid_to: datetime | None
    created_at: datetime
    updated_at: datetime
    evidence: list[EvidenceResponse] = Field(default_factory=list)


class RelationshipCreate(BaseModel):
    subject_id: UUID
    predicate: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z][A-Za-z0-9 _-]*$")
    object_id: UUID
    description: str | None = Field(None, max_length=4000)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    evidence_chunk_ids: list[UUID] = Field(min_length=1, max_length=20)

    _times = field_validator("valid_from", "valid_to")(naive_utc)

    @field_validator("predicate")
    @classmethod
    def normalize_predicate(cls, value):
        return "_".join(value.upper().replace("-", " ").split())

    @model_validator(mode="after")
    def validate_edge(self):
        if self.subject_id == self.object_id:
            raise ValueError("A relationship must connect two different entities")
        if self.valid_from and self.valid_to and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be after valid_from")
        if len(set(self.evidence_chunk_ids)) != len(self.evidence_chunk_ids):
            raise ValueError("Evidence chunks must be unique")
        return self


class RelationshipUpdate(BaseModel):
    description: str | None = Field(None, max_length=4000)
    status: Literal["CONFIRMED", "ARCHIVED", "WITHDRAWN"] | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    _times = field_validator("valid_from", "valid_to")(naive_utc)


class RelationshipResponse(BaseModel):
    id: UUID
    subject: EntityResponse
    predicate: str
    object: EntityResponse
    description: str | None
    confidence: float
    status: str
    valid_from: datetime | None
    valid_to: datetime | None
    recorded_at: datetime
    evidence: list[EvidenceResponse]


class ExtractedEntity(BaseModel):
    chunk_id: UUID
    mention: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    type: EntityType
    confidence: float = Field(ge=0, le=1)
    description: str | None = Field(None, max_length=1000)
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    _times = field_validator("valid_from", "valid_to")(naive_utc)


class ExtractedRelationship(BaseModel):
    chunk_id: UUID
    subject_name: str = Field(min_length=1, max_length=255)
    subject_type: EntityType
    predicate: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z][A-Za-z0-9 _-]*$")
    object_name: str = Field(min_length=1, max_length=255)
    object_type: EntityType
    confidence: float = Field(ge=0, le=1)
    description: str | None = Field(None, max_length=1000)
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    _times = field_validator("valid_from", "valid_to")(naive_utc)
    _predicate = field_validator("predicate")(RelationshipCreate.normalize_predicate.__func__)

    @model_validator(mode="after")
    def valid_period(self):
        if self.valid_from and self.valid_to and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be after valid_from")
        return self


class ExtractionResult(BaseModel):
    entities: list[ExtractedEntity] = Field(default_factory=list, max_length=200)
    relationships: list[ExtractedRelationship] = Field(default_factory=list, max_length=200)


class ProposalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    kind: str
    payload: dict
    confidence: float
    status: str
    chunk_id: UUID
    resolved_entity_id: UUID | None
    resolved_relationship_id: UUID | None
    created_at: datetime


class ProposalReview(BaseModel):
    decision: ReviewDecision
    entity_id: UUID | None = None
    subject_id: UUID | None = None
    object_id: UUID | None = None


class KnowledgeJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    document_id: UUID
    generation: int
    status: str
    attempts: int
    error_code: str | None
