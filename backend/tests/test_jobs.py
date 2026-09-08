import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.clock import utcnow
from app.db.models import IngestionJob
from app.errors import ProviderUnavailable
from app.settings import get_settings
from app.worker import claim
from tests.pdf_builder import build_pdf, build_pdf_pages


PDF = build_pdf(["The deployment owner is Morgan."])


def enqueue(client, org, content=PDF):
    return client.post(f"/organizations/{org}/documents", files={"file": ("notes.pdf", content, "application/pdf")})


def test_upload_is_durable_before_embedding_and_pollable(client, organization, embedding_provider, db_session_factory):
    org = organization()
    response = enqueue(client, org)
    assert response.status_code == 202
    assert response.json()["status"] == "QUEUED"
    assert not embedding_provider.input_types
    with db_session_factory() as db:
        assert db.scalar(select(IngestionJob)).status == "QUEUED"
    assert client.get(response.headers["Location"]).json()["status"] == "QUEUED"
    assert client.process_one()
    assert client.get(response.headers["Location"]).json()["status"] == "READY"
    assert not client.process_one()


def test_transient_failure_retries_without_duplicate_vectors(client, organization, vector_store, db_session_factory, monkeypatch):
    response = enqueue(client, organization())
    original = vector_store.upsert
    async def partial(*args, **kwargs):
        await original(*args, **kwargs)
        raise ProviderUnavailable("temporary")
    monkeypatch.setattr(vector_store, "upsert", partial)
    client.process_one()
    ids = set(vector_store.points)
    assert ids
    assert client.get(response.headers["Location"]).json()["status"] == "QUEUED"
    with db_session_factory() as db:
        job = db.scalar(select(IngestionJob))
        assert job.attempts == 1
        job.available_at = utcnow() - timedelta(seconds=1)
        db.commit()
    monkeypatch.setattr(vector_store, "upsert", original)
    client.process_one()
    assert set(vector_store.points) == ids
    assert client.get(response.headers["Location"]).json()["status"] == "READY"


def test_crashed_claim_is_recovered_only_after_lease(client, organization, db_session_factory):
    response = enqueue(client, organization())
    assert claim(db_session_factory)
    assert claim(db_session_factory) is None
    with db_session_factory() as db:
        db.scalar(select(IngestionJob)).lease_until = utcnow() - timedelta(seconds=1)
        db.commit()
    assert client.process_one()
    assert client.get(response.headers["Location"]).json()["status"] == "READY"


def test_poison_document_fails_without_retry_and_can_be_deleted(client, organization):
    response = enqueue(client, organization(), b"broken PDF")
    client.process_one()
    state = client.get(response.headers["Location"]).json()
    assert state["status"] == "FAILED"
    assert state["error_code"] == "EXTRACTION_FAILED"
    assert not client.process_one()
    assert client.delete(response.headers["Location"]).status_code == 202
    client.process_one()
    assert client.get(response.headers["Location"]).status_code == 404


def test_delete_queued_document_cancels_ingestion(client, organization, embedding_provider):
    response = enqueue(client, organization())
    assert client.delete(response.headers["Location"]).json()["status"] == "DELETING"
    client.process_one()
    assert not embedding_provider.input_types
    assert client.get(response.headers["Location"]).status_code == 404


def test_claimed_document_rejects_conflicting_operations(client, organization, db_session_factory):
    response = enqueue(client, organization())
    claim(db_session_factory)
    assert client.delete(response.headers["Location"]).status_code == 409
    assert client.post(response.headers["Location"] + '/reindex').status_code == 409


def test_failed_or_orphaned_vectors_are_not_retrieved(client, organization, vector_store, embedding_provider):
    org = organization()
    response = enqueue(client, org)
    vectors = asyncio.run(embedding_provider.embed(["deployment Morgan"], input_type="passage"))
    asyncio.run(vector_store.upsert(org, vectors, [{"document_id": response.json()["id"], "content": "deployment Morgan"}]))
    assert client.post(f"/organizations/{org}/search", json={"query": "deployment"}).json()["results"] == []


@pytest.mark.parametrize('setting,value', [('MAX_PENDING_JOBS_PER_ORG',1), ('MAX_DOCUMENTS_PER_ORG',1), ('UPLOADS_PER_MINUTE',1)])
def test_admission_quotas(client, organization, monkeypatch, setting, value):
    monkeypatch.setenv('SENTINETH_' + setting, str(value))
    get_settings.cache_clear()
    org = organization()
    assert enqueue(client, org).status_code == 202
    assert enqueue(client, org).status_code == 429


def test_upload_size_is_bounded_and_partial_file_removed(client, organization, monkeypatch, tmp_path):
    monkeypatch.setenv('SENTINETH_MAX_UPLOAD_BYTES', '128')
    get_settings.cache_clear()
    org = organization()
    assert enqueue(client, org).status_code == 413
    assert not [p for p in (tmp_path/'documents').rglob('*') if p.is_file()]
    assert client.get(f'/organizations/{org}/documents').json()['total'] == 0


def test_page_limit_fails_worker_without_calling_embedding(client, organization, monkeypatch, embedding_provider):
    monkeypatch.setenv('SENTINETH_MAX_PAGES', '1')
    get_settings.cache_clear()
    response = enqueue(client, organization(), build_pdf_pages([["one"],["two"]]))
    client.process_one()
    assert client.get(response.headers['Location']).json()['error_code'] == 'INPUT_TOO_LARGE'
    assert not embedding_provider.input_types


def test_query_rate_is_shared_by_search_and_query(client, organization, monkeypatch):
    monkeypatch.setenv('SENTINETH_QUERIES_PER_MINUTE', '1')
    get_settings.cache_clear()
    org = organization()
    assert client.post(f'/organizations/{org}/search', json={'query':'test'}).status_code == 200
    assert client.post(f'/organizations/{org}/query', json={'query':'test'}).status_code == 429


def test_all_document_routes_reject_foreign_credentials(api_key_client):
    client = api_key_client
    org = client.post('/organizations',json={'name':'A'}).json()
    other = client.post('/organizations',json={'name':'B'}).json()
    own = {'Authorization':'Bearer '+org['api_key']}
    foreign = {'Authorization':'Bearer '+other['api_key']}
    root = f"/organizations/{org['id']}"
    doc = client.post(root+'/documents',headers=own,files={'file':('a.pdf',PDF,'application/pdf')}).json()['id']
    cases = [('get','/documents',{}),('get',f'/documents/{doc}',{}),('delete',f'/documents/{doc}',{}),
             ('post',f'/documents/{doc}/reindex',{}),('post','/documents',{'files':{'file':('a.pdf',PDF,'application/pdf')}}),
             ('post','/query',{'json':{'query':'test'}}),('post','/search',{'json':{'query':'test'}})]
    for method,path,kwargs in cases:
        assert client.request(method,root+path,headers=foreign,**kwargs).status_code == 403
        assert client.request(method,root+path,**kwargs).status_code == 401


def test_provider_timeout_returns_sanitized_503(client, organization, monkeypatch, vector_store):
    org = organization()
    enqueue(client, org)
    client.process_one()
    monkeypatch.setenv('SENTINETH_PROVIDER_TIMEOUT_SECONDS','0.01')
    get_settings.cache_clear()
    async def stalled(*args,**kwargs):
        await asyncio.sleep(10)
    monkeypatch.setattr(vector_store,'search',stalled)
    response = client.post(f'/organizations/{org}/search',json={'query':'deployment'})
    assert response.status_code == 503
    assert response.json()['detail']['error_code'] == 'PROVIDER_UNAVAILABLE'


def test_retry_budget_is_finite(client, organization, db_session_factory, monkeypatch, vector_store):
    response = enqueue(client,organization())
    async def unavailable(*args,**kwargs):
        raise ConnectionError('private-provider-detail')
    monkeypatch.setattr(vector_store,'delete_document',unavailable)
    for _ in range(3):
        assert client.process_one()
        with db_session_factory() as db:
            db.scalar(select(IngestionJob)).available_at = utcnow() - timedelta(seconds=1)
            db.commit()
    state = client.get(response.headers['Location'])
    assert state.json()['status'] == 'FAILED'
    assert 'private-provider-detail' not in state.text
    assert not client.process_one()


LONG_PAGES = build_pdf_pages([
    [f"Paragraph {n} of the migration plan describes the rollout." for n in range(40)],
    [f"Section {n} records the owner and the agreed rollback window." for n in range(40)],
])


def test_reindex_after_a_chunking_change_leaves_no_stale_vectors(
    client, organization, embedding_provider, vector_store
):
    """Deterministic point ids are not, on their own, retry safety.

    A point id is derived from a chunk id, and chunk ids are regenerated
    whenever chunking changes - a different chunk budget, a different
    tokenizer, a different extractor. Re-ingesting then writes a *different*
    set of ids while the previous set is still there, still carrying the
    same organisation and document filter, and still answering searches.
    Deleting the document's vectors before writing is what makes a reindex a
    replacement rather than a union.
    """
    response = enqueue(client, organization(), LONG_PAGES)
    client.process_one()

    before = set(vector_store.points)
    assert len(before) > 1, "corpus must actually span several chunks"

    # A chunker change, in miniature: same document, larger budget, so the
    # same text lands in one chunk instead of several and every chunk id is
    # new. This is the shape of every Phase 1 chunking change.
    embedding_provider._max_input_tokens = 8192

    assert client.post(response.headers["Location"] + "/reindex").status_code == 202
    client.process_one()

    after = set(vector_store.points)
    assert len(after) == 1
    assert not (before & after), "the reindex must not reuse the old ids"
    assert set(vector_store.points) == after, "the old vectors must be gone"
    assert client.get(response.headers["Location"]).json()["chunk_count"] == 1


def test_lost_source_file_fails_distinguishably_and_without_retrying(
    client, organization, db_session_factory
):
    """Postgres and object storage can drift. Say which one lost the file.

    Reported as EXTRACTION_FAILED this is indistinguishable from a scanned
    PDF, and the two need opposite responses: one is unfixable, the other is
    fixed by uploading the file again.
    """
    from pathlib import Path

    from app.db.models import Document

    response = enqueue(client, organization())
    client.process_one()
    assert client.get(response.headers["Location"]).json()["status"] == "READY"

    with db_session_factory() as db:
        Path(db.scalar(select(Document)).storage_path).unlink()

    assert client.post(response.headers["Location"] + "/reindex").status_code == 202
    client.process_one()

    state = client.get(response.headers["Location"]).json()
    assert state["status"] == "FAILED"
    assert state["error_code"] == "SOURCE_MISSING"
    # Not retryable: the bytes will not come back on their own.
    assert not client.process_one()


def test_a_deleted_document_leaves_nothing_behind_in_any_store(
    client, organization, vector_store, db_session_factory, tmp_path
):
    """Deletion has to land in Postgres, object storage and Qdrant.

    Anything left in one of the three is state nothing will ever collect:
    the row that would have pointed at it is gone.
    """
    from pathlib import Path

    from app.db.models import Document, DocumentChunk, IngestionJob

    response = enqueue(client, organization(), LONG_PAGES)
    client.process_one()

    with db_session_factory() as db:
        stored = Path(db.scalar(select(Document)).storage_path)

    assert stored.exists()
    assert vector_store.points

    assert client.delete(response.headers["Location"]).status_code == 202
    client.process_one()

    assert not stored.exists()
    assert vector_store.points == {}
    with db_session_factory() as db:
        assert db.scalars(select(Document)).all() == []
        assert db.scalars(select(DocumentChunk)).all() == []
        assert db.scalars(select(IngestionJob)).all() == []
