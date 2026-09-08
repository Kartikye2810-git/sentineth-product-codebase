"""Real concurrency checks. Set TEST_POSTGRES_URL to a disposable Postgres.

Each test uses a fresh schema and drops only that schema. SQLite cannot prove
row locking, SKIP LOCKED, or cross-process quota behavior.
"""
import asyncio
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.clock import utcnow
from app.db.database import Base, get_db
from app.db.models import IngestionJob, Organization
from app.dependencies import (
    get_embedding_provider,
    get_llm_provider,
    get_storage_provider,
    get_vector_store,
)
from app.main import app
from app.providers.storage.local import LocalStorageProvider
from app.services.document_service import queue_document
from app.worker import claim, run_once
from tests.fakes import FakeEmbeddingProvider, FakeLLMProvider, FakeVectorStore
from tests.pdf_builder import build_pdf


@pytest.fixture
def postgres():
    url = os.getenv('TEST_POSTGRES_URL')
    if not url:
        pytest.skip('Set TEST_POSTGRES_URL to test real PostgreSQL locking')
    schema = 'test_jobs_' + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as c:
        c.execute(text(f'CREATE SCHEMA {schema}'))
    engine = create_engine(url, connect_args={'options':f'-c search_path={schema}'})
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine)
    engine.dispose()
    with admin.begin() as c:
        c.execute(text(f'DROP SCHEMA {schema} CASCADE'))
    admin.dispose()


def test_workers_claim_distinct_jobs_and_do_not_steal_locked_leases(postgres, tmp_path):
    from io import BytesIO

    from starlette.datastructures import Headers, UploadFile

    from app.db.models import Document
    storage = LocalStorageProvider(tmp_path)
    with postgres() as db:
        org = Organization(name='Queue test')
        db.add(org)
        db.commit()
        org_id = org.id
        for i in range(6):
            queue_document(db,org_id,UploadFile(BytesIO(build_pdf([f'Document {i}'])),filename=f'{i}.pdf',
                headers=Headers({'content-type':'application/pdf'})),storage)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _:claim(postgres), range(6)))
    assert len({r[0] for r in results if r}) == 6
    with postgres() as db:
        jobs = list(db.scalars(select(IngestionJob)))
        for job in jobs:
            job.status='SUCCEEDED'
        target=jobs[0]
        target.status='PROCESSING'
        target.lease_until=utcnow()-timedelta(seconds=1)
        doc_id=target.document_id
        db.commit()
    with postgres() as held:
        held.scalar(select(Document).where(Document.id==doc_id).with_for_update())
        assert claim(postgres) is None
    assert claim(postgres) is not None


def test_api_queries_remain_responsive_during_worker_ingestion(postgres, tmp_path):
    entered, release = threading.Event(), threading.Event()
    class SlowPassageProvider(FakeEmbeddingProvider):
        slow=False
        async def embed(self,texts,*,input_type):
            if self.slow and input_type=='passage':
                entered.set()
                assert release.wait(timeout=15)
            return await super().embed(texts,input_type=input_type)
    provider=SlowPassageProvider()
    vectors=FakeVectorStore()
    storage=LocalStorageProvider(tmp_path)
    def database():
        with postgres() as db:
            yield db
    app.dependency_overrides.update({get_db:database,get_embedding_provider:lambda:provider,
        get_vector_store:lambda:vectors,get_storage_provider:lambda:storage,get_llm_provider:lambda:FakeLLMProvider()})
    try:
        with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as pool:
            org=client.post('/organizations',json={'name':'Concurrent test'}).json()
            headers={'Authorization':'Bearer '+org['api_key']}
            root=f"/organizations/{org['id']}"
            def upload():
                response=client.post(root+'/documents',headers=headers,
                    files={'file':('report.pdf',build_pdf(['The deployment owner is Morgan.']),'application/pdf')})
                assert response.status_code==202,response.text
                return response
            first=upload()
            asyncio.run(run_once(postgres,provider,vectors,storage))
            assert client.get(first.headers['Location'],headers=headers).json()['status']=='READY'
            def search_latency():
                start=time.perf_counter()
                response=client.post(root+'/query',headers=headers,json={'query':'deployment owner'})
                assert response.status_code==200,response.text
                assert response.json()['sources']
                return time.perf_counter()-start
            baseline=[search_latency() for _ in range(5)]
            queued=upload()
            provider.slow=True
            future=pool.submit(lambda:asyncio.run(run_once(postgres,provider,vectors,storage)))
            assert entered.wait(timeout=5)
            try:
                assert client.get('/health').status_code==200
                assert client.get(queued.headers['Location'],headers=headers).json()['status']=='PROCESSING'
                assert client.delete(queued.headers['Location'],headers=headers).status_code==409
                measured=[search_latency() for _ in range(10)]
                # No query waits for the deliberately blocked ingestion. Allow
                # scheduling noise; the worker cannot finish before release.
                assert max(measured)<max(0.5,max(baseline)*5)
                assert not future.done()
                print(f'query latency: baseline max={max(baseline):.4f}s, ingesting max={max(measured):.4f}s')
            finally:
                release.set()
                future.result(timeout=5)
    finally:
        app.dependency_overrides.clear()
