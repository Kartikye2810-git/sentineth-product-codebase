"""Test configuration.

DATABASE_URL is forced to in-memory SQLite *before* the app is imported,
because `app.db.database` reads it and builds an engine at import time.
Tests never touch Postgres, Qdrant, or a real LLM.
"""

import os


os.environ["DATABASE_URL"] = "sqlite://"
os.environ["SQL_ECHO"] = "false"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base, get_db
from app.dependencies import (
    get_embedding_provider,
    get_llm_provider,
    get_storage_provider,
    get_vector_store,
)
from app.main import app
from app.providers.storage.local import LocalStorageProvider
from app.security import require_organization_access
from tests.fakes import (
    FakeEmbeddingProvider,
    FakeLLMProvider,
    FakeVectorStore,
)


@pytest.fixture
def db_session_factory():
    # StaticPool keeps one connection alive so the in-memory schema is
    # visible across the multiple sessions a request cycle opens.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    Base.metadata.create_all(engine)

    yield sessionmaker(bind=engine, autoflush=False, autocommit=False)

    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def embedding_provider():
    return FakeEmbeddingProvider()


@pytest.fixture
def vector_store():
    return FakeVectorStore()


@pytest.fixture
def llm_provider():
    return FakeLLMProvider()


@pytest.fixture
def client(
    db_session_factory,
    embedding_provider,
    vector_store,
    llm_provider,
    tmp_path,
):
    # The real storage provider, pointed at a temp dir, so filename
    # sanitizing and path containment are exercised for real.
    storage_provider = LocalStorageProvider(tmp_path / "documents")

    def override_get_db():
        db = db_session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_embedding_provider] = (
        lambda: embedding_provider
    )
    app.dependency_overrides[get_vector_store] = lambda: vector_store
    app.dependency_overrides[get_storage_provider] = lambda: storage_provider
    app.dependency_overrides[get_llm_provider] = lambda: llm_provider
    app.dependency_overrides[require_organization_access] = lambda: None

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture
def organization(client):
    """Create an organization and return its id."""

    def _create(name: str = "Acme Inc") -> str:
        response = client.post("/organizations", json={"name": name})
        assert response.status_code == 200, response.text
        return response.json()["id"]

    return _create


@pytest.fixture
def api_key_client(client):
    """`client` with real API-key authentication restored.

    The `client` fixture overrides require_organization_access away, which is
    what the pipeline tests want. The api-key endpoints can only be tested
    with it in place.
    """
    del app.dependency_overrides[require_organization_access]
    return client
