"""Real PostgreSQL backfill and tenant FK check; all work stays in a temporary schema."""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import MetaData, Table, create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.clock import utcnow


def test_existing_documents_gain_sources_without_reindexing():
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set TEST_POSTGRES_URL for migration verification")
    schema = "test_sources_" + uuid4().hex
    admin = create_engine(url)
    scoped = make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_engine(scoped)
    env = os.environ | {"DATABASE_URL": scoped.render_as_string(hide_password=False)}

    def migrate(*args):
        subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            env=env,
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
        )

    with admin.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema}"))
    try:
        migrate("upgrade", "f93a61c2d704")
        metadata = MetaData()
        organizations = Table("organizations", metadata, autoload_with=engine)
        documents = Table("documents", metadata, autoload_with=engine)
        chunks = Table("document_chunks", metadata, autoload_with=engine)
        org_id, other_org, doc_id, chunk_id = uuid4(), uuid4(), uuid4(), uuid4()
        now = utcnow()
        with engine.begin() as connection:
            connection.execute(
                organizations.insert(),
                [
                    {"id": i, "name": "Migration test", "created_at": now, "updated_at": now}
                    for i in (org_id, other_org)
                ],
            )
            connection.execute(
                documents.insert().values(
                    id=doc_id,
                    organization_id=org_id,
                    filename="legacy.pdf",
                    content_type="application/pdf",
                    file_size=10,
                    storage_path="/old/legacy.pdf",
                    status="READY",
                    index_generation=2,
                    created_at=now,
                    updated_at=now,
                )
            )
            connection.execute(
                chunks.insert().values(
                    id=chunk_id,
                    document_id=doc_id,
                    chunk_index=0,
                    content="Existing evidence",
                    created_at=now,
                )
            )
        migrate("upgrade", "head")
        migrate("check")
        fresh = MetaData()
        docs = Table("documents", fresh, autoload_with=engine)
        sources = Table("sources", fresh, autoload_with=engine)
        with engine.connect() as connection:
            source = connection.execute(select(sources)).mappings().one()
            assert source["id"] == doc_id and source["sync_state"] == "SYNCED"
            assert source["external_id"] == str(doc_id) and source["created_at"] == now
            assert connection.scalar(select(docs.c.source_id)) == doc_id
            assert connection.scalar(select(chunks.c.id)) == chunk_id
        # The DB itself rejects a payload pointed at another organization's source.
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                docs.update().where(docs.c.id == doc_id).values(organization_id=other_org)
            )
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        admin.dispose()
