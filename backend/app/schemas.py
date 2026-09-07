from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class OrganizationCreate(BaseModel):
    name: str


class OrganizationResponse(BaseModel):
    id: UUID
    name: str
    created_at: datetime
    updated_at: datetime
    api_key: str | None = None

    model_config = ConfigDict(from_attributes=True)


class ApiKeyResponse(BaseModel):
    """API-key metadata. Deliberately carries no token or token hash."""

    id: UUID
    created_at: datetime
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    active: bool

    model_config = ConfigDict(from_attributes=True)


class ApiKeyIssued(ApiKeyResponse):
    """The one response that contains a token, because it is the only
    moment the plaintext exists. Only the hash is stored."""

    api_key: str


class ApiKeyRotateRequest(BaseModel):
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def _as_naive_utc(cls, value: datetime | None) -> datetime | None:
        # The column is naive UTC; an aware value would raise TypeError on
        # comparison in OrganizationApiKey.active.
        if value is None or value.tzinfo is None:
            return value
        return value.astimezone(UTC).replace(tzinfo=None)


class DocumentResponse(BaseModel):
    """Public shape of a document.

    storage_path and content_hash are internal, and error_message is free
    text with no contract, so none of them appear here. Callers get the
    coarse status plus the categorised error_code.
    """

    id: UUID
    filename: str
    content_type: str
    file_size: int
    status: str
    error_code: str | None = None
    chunk_count: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DocumentUploadResponse(DocumentResponse):
    message: str


class DocumentListResponse(BaseModel):
    items: list[DocumentResponse]
    total: int
    limit: int
    offset: int


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    limit: int = Field(default=5, ge=1, le=20)


class SearchResult(BaseModel):
    id: str | None = None
    score: float | None = None
    document_id: str | None = None
    chunk_id: str | None = None
    chunk_index: int | None = None
    filename: str | None = None
    content: str | None = None


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResult] = []


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    limit: int = Field(default=5, ge=1, le=20)


class QuerySource(BaseModel):
    document_id: str | None = None
    chunk_id: str | None = None
    filename: str | None = None
    chunk_index: int | None = None
    score: float | None = None


class QueryResponse(BaseModel):
    query: str
    answer: str
    sources: list[QuerySource] = []
