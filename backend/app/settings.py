"""Validated, process-scoped resource limits (environment overrides)."""
import os
from functools import lru_cache

from pydantic import BaseModel, Field


class Settings(BaseModel):
    max_upload_bytes: int = Field(default=25 * 1024 * 1024, gt=0)
    max_pages: int = Field(default=500, gt=0)
    max_extracted_chars: int = Field(default=2_000_000, gt=0)
    max_chunks: int = Field(default=5000, gt=0)
    max_documents_per_org: int = Field(default=1000, gt=0)
    max_storage_bytes_per_org: int = Field(default=1024 * 1024 * 1024, gt=0)
    max_pending_jobs_per_org: int = Field(default=10, gt=0)
    uploads_per_minute: int = Field(default=10, gt=0)
    queries_per_minute: int = Field(default=60, gt=0)
    provider_timeout_seconds: float = Field(default=30, gt=0)
    job_lease_seconds: int = Field(default=300, gt=0)
    job_max_attempts: int = Field(default=3, gt=0)
    job_retry_seconds: int = Field(default=5, gt=0)
    worker_poll_seconds: float = Field(default=1, gt=0)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(**{
        key: os.environ['SENTINETH_' + key.upper()]
        for key in Settings.model_fields
        if 'SENTINETH_' + key.upper() in os.environ
    })
