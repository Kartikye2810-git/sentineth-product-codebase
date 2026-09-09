"""Validated, process-scoped resource limits (environment overrides)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SENTINETH_",
        extra="ignore",
        secrets_dir=None,
        env_file=Path(__file__).resolve().parents[2] / ".env",
        hide_input_in_errors=True,
    )
    environment: Literal["development", "test", "production"] = "development"
    database_url: SecretStr = Field(validation_alias="DATABASE_URL")
    sql_echo: bool = Field(default=False, validation_alias="SQL_ECHO")
    embedding_provider: Literal["nvidia", "local"] = Field(
        default="nvidia", validation_alias="EMBEDDING_PROVIDER"
    )
    nvidia_api_key: SecretStr = Field(default=SecretStr(""), validation_alias="NVIDIA_API_KEY")
    nvidia_base_url: str = Field(
        default="https://integrate.api.nvidia.com/v1", validation_alias="NVIDIA_BASE_URL"
    )
    openrouter_api_key: SecretStr = Field(
        default=SecretStr(""), validation_alias="OPENROUTER_API_KEY"
    )
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1", validation_alias="OPENROUTER_BASE_URL"
    )
    openrouter_llm_model: str = Field(
        default="openrouter/free", validation_alias="OPENROUTER_LLM_MODEL"
    )
    openai_api_key: SecretStr = Field(default=SecretStr(""), validation_alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", validation_alias="OPENAI_MODEL")
    qdrant_url: str = Field(default="http://localhost:6333", validation_alias="QDRANT_URL")
    qdrant_api_key: SecretStr = Field(default=SecretStr(""), validation_alias="QDRANT_API_KEY")
    qdrant_collection: str = Field(default="", validation_alias="QDRANT_COLLECTION")
    qdrant_hybrid: bool = Field(default=False, validation_alias="QDRANT_HYBRID")
    rerank: bool = Field(default=False, validation_alias="RERANK")
    storage_dir: Path = Path(__file__).resolve().parents[1] / "storage" / "documents"
    session_hours: int = Field(default=12, ge=1, le=168)
    invitation_hours: int = Field(default=48, ge=1, le=168)
    auth_attempts_per_minute: int = Field(default=10, ge=1)
    auth_ip_attempts_per_minute: int = Field(default=60, ge=1)
    max_organizations_per_user: int = Field(default=5, ge=1)
    metrics_token: SecretStr = SecretStr("")
    sentry_dsn: SecretStr = SecretStr("")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO", validation_alias="LOG_LEVEL"
    )
    allowed_origins: list[str] = []
    allowed_hosts: list[str] = ["localhost", "127.0.0.1", "testserver"]

    @field_validator("embedding_provider", mode="before")
    @classmethod
    def normalize_provider(cls, value):
        return value.strip().lower() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_urls(self):
        for value in (self.qdrant_url, self.nvidia_base_url, self.openrouter_base_url):
            u = urlsplit(value)
            if u.scheme not in {"http", "https"} or not u.hostname or u.username or u.password:
                raise ValueError("Provider URLs must be HTTP(S) URLs without embedded credentials")
        return self

    @property
    def collection_name(self):
        return self.qdrant_collection or (
            "sentineth_documents_nemotron"
            if self.embedding_provider == "nvidia"
            else "sentineth_documents"
        )

    def validate_runtime(self, role="api"):
        if self.environment == "test":
            return
        if (role in {"api", "worker"} and self.embedding_provider == "nvidia"
                and not self.nvidia_api_key.get_secret_value()):
            raise ValueError("NVIDIA_API_KEY is required for the selected embedding provider")
        if self.embedding_provider == "local" or self.rerank:
            from importlib.util import find_spec

            if find_spec("sentence_transformers") is None:
                raise ValueError("Local models require the image built with WITH_LOCAL_MODELS=true")
        if role in {"api", "knowledge-worker"} and not self.openrouter_api_key.get_secret_value():
            raise ValueError("OPENROUTER_API_KEY is required for the answer provider")
        if self.environment == "production":
            if not self.database_url.get_secret_value().startswith("postgresql"):
                raise ValueError("Production requires PostgreSQL")
            if len(self.metrics_token.get_secret_value()) < 32:
                raise ValueError("Production requires a metrics token of at least 32 characters")
            if "*" in self.allowed_hosts or "*" in self.allowed_origins:
                raise ValueError("Production hosts and origins must be explicit")
            if not self.nvidia_base_url.startswith(
                "https://"
            ) or not self.openrouter_base_url.startswith("https://"):
                raise ValueError("Production external provider URLs must use HTTPS")
            if self.log_level == "DEBUG":
                raise ValueError("Production logging must use INFO or above")
            if self.sql_echo:
                raise ValueError("SQL_ECHO must be disabled in production")

    max_upload_bytes: int = Field(default=25 * 1024 * 1024, gt=0)
    max_pages: int = Field(default=500, gt=0)
    max_extracted_chars: int = Field(default=2_000_000, gt=0)
    max_chunks: int = Field(default=5000, gt=0)
    knowledge_batch_chars: int = Field(default=12_000, ge=1000, le=100_000)
    max_knowledge_proposals: int = Field(default=1000, ge=1, le=10_000)
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
    import os

    directory = os.environ.get("SENTINETH_SECRETS_DIR")
    return Settings(_secrets_dir=directory) if directory else Settings()
