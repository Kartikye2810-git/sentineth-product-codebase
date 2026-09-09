"""Disposable PostgreSQL/Qdrant recovery rehearsal, never touches the active corpus.

Run from backend: python scripts/rehearse_restore.py --postgres-container sentineth-postgres
Add --live-provider to exercise NVIDIA using only the synthetic policy below.
Optionally --image sentineth:phase3 verifies the built image against the restored DB.
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path
from uuid import uuid4


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def stage(name):
    from io import BytesIO

    import reindex
    from sqlalchemy import select, text
    from starlette.datastructures import Headers, UploadFile

    from app.db.database import SessionLocal, engine
    from app.db.models import Document, DocumentChunk, Membership, OrganizationApiKey, User
    from app.dependencies import get_embedding_provider, get_storage_provider
    from app.providers.vector.qdrant import QdrantVectorStore
    from app.security import Principal, hash_password
    from app.services.document_service import queue_document, queue_existing
    from app.settings import get_settings
    from app.worker import run_once
    from tests.fakes import FakeEmbeddingProvider
    from tests.pdf_builder import build_pdf

    settings = get_settings()
    provider = (
        get_embedding_provider()
        if os.environ.get("REHEARSE_LIVE") == "1"
        else FakeEmbeddingProvider(dimension=384)
    )
    store = QdrantVectorStore(
        collection_name=settings.collection_name, vector_size=provider.dimension, hybrid=False
    )
    storage = get_storage_provider()
    if name == "seed":
        with SessionLocal() as db:
            key = db.scalar(select(OrganizationApiKey))
            assert key.role == "owner" and key.label == "Legacy API key"
            org_id = key.organization_id
            user = User(
                email="rehearsal@example.com",
                password_hash=hash_password("synthetic-rehearsal-password"),
            )
            db.add(user)
            db.flush()
            db.add(Membership(user_id=user.id, organization_id=org_id, role="owner"))
            db.commit()
            user_id = user.id
            db.info["actor"] = Principal("user", user_id, user_id=user_id)
            queue_document(
                db,
                org_id,
                UploadFile(
                    BytesIO(
                        build_pdf(
                            [
                                "Synthetic recovery policy: Morgan owns deployment. Rollbacks require approval."
                            ]
                        )
                    ),
                    filename="recovery.pdf",
                    headers=Headers({"content-type": "application/pdf"}),
                ),
                storage,
            )
        asyncio.run(run_once(SessionLocal, provider, store, storage))
        with SessionLocal() as db:
            document = db.scalar(select(Document))
            assert document.status == "READY"
            assert document.created_by_user_id == user_id
        # Prove the migration's DB guard also covers raw SQL and TRUNCATE.
        from sqlalchemy.exc import DBAPIError

        for sql in (
            "UPDATE audit_events SET action='tampered'",
            "DELETE FROM audit_events",
            "TRUNCATE audit_events",
        ):
            try:
                with engine.begin() as connection:
                    connection.execute(text(sql))
            except DBAPIError as exc:
                assert "append-only" in str(exc.orig)
            else:
                raise AssertionError("Audit mutation was accepted")
    else:
        with SessionLocal() as db:
            rows = reindex.load_chunks(db)
            assert rows and Path(rows[0][1].storage_path).is_file()
            count = asyncio.run(reindex.reindex(provider, store, rows, False))
            assert reindex.verify(store, count)
            org_id = rows[0][1].organization_id
            document_id = rows[0][1].id
        # Revoke restored credentials, then explicitly recover this synthetic account.
        with SessionLocal() as db:
            from app.db.models import UserSession

            assert all(key.revoked_at for key in db.scalars(select(OrganizationApiKey)))
            assert all(session.revoked_at for session in db.scalars(select(UserSession)))
            user = db.scalar(select(User))
            assert user.disabled
            user.disabled = False
            user.password_hash = hash_password("synthetic-rehearsal-password")
            from app.audit import record

            record(db, "operator.password_reset", resource_id=user.id)
            db.commit()
        # Then simulate loss of extracted chunks too: reconstruct from the PDF.
        with SessionLocal() as db:
            for chunk in db.scalars(select(DocumentChunk)):
                db.delete(chunk)
            db.commit()
            queue_existing(db, org_id, document_id, "INGEST")
        asyncio.run(run_once(SessionLocal, provider, store, storage))
        with SessionLocal() as db:
            assert db.get(Document, document_id).status == "READY"
            assert list(db.scalars(select(DocumentChunk)))
    with SessionLocal() as db:
        document = db.scalar(select(Document))
        query = asyncio.run(provider.embed(["Who owns deployment?"], input_type="query"))[0]
        hits = asyncio.run(
            store.search(str(document.organization_id), query, document_ids=[str(document.id)])
        )
        assert hits and "Morgan" in hits[0]["payload"]["content"]
    store._client.close()
    print(name + ": document, actor attribution, source file and retrieval verified")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--postgres-container")
    parser.add_argument("--live-provider", action="store_true")
    parser.add_argument("--image")
    parser.add_argument("--stage", choices=["seed", "recover"])
    args = parser.parse_args()
    if args.stage:
        return stage(args.stage)
    from qdrant_client import QdrantClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    from app.settings import get_settings

    settings = get_settings()
    url = make_url(settings.database_url.get_secret_value())
    if url.get_backend_name() != "postgresql":
        parser.error("Configure the local PostgreSQL DATABASE_URL")
    unique = uuid4().hex[:12]
    names = ["codex_restore_source_" + unique, "codex_restore_target_" + unique]
    collections = ["codex_restore_source_" + unique, "codex_restore_target_" + unique]
    admin = create_engine(
        url.set(database="postgres"), isolation_level="AUTOCOMMIT", hide_parameters=True
    )
    root = Path(tempfile.mkdtemp(prefix="sentineth-recovery-"))
    env = os.environ.copy()
    env.update(
        DATABASE_URL=url.set(database=names[0]).render_as_string(hide_password=False),
        SENTINETH_STORAGE_DIR=str(root / "source"),
        QDRANT_COLLECTION=collections[0],
        EMBEDDING_PROVIDER="nvidia" if args.live_provider else "local",
        QDRANT_HYBRID="false",
        REHEARSE_LIVE="1" if args.live_provider else "0",
    )
    created = []
    container_name = "sentineth-recovery-" + unique
    started = time.perf_counter()

    def run(*command):
        subprocess.run([sys.executable, *command], env=env, check=True)

    try:
        with admin.connect() as connection:
            for name in names:
                connection.execute(text(f"CREATE DATABASE {name}"))
                created.append(name)
        run("-m", "alembic", "upgrade", "e81d4a72c603")
        source = create_engine(env["DATABASE_URL"], hide_parameters=True)
        with source.begin() as connection:
            from app.clock import utcnow
            from app.security import hash_api_key

            org_id = uuid4()
            connection.execute(
                text(
                    "INSERT INTO organizations(id,name,created_at,updated_at) VALUES (:id,:name,:now,:now)"
                ),
                {"id": org_id, "name": "Disposable recovery rehearsal", "now": utcnow()},
            )
            connection.execute(
                text(
                    "INSERT INTO organization_api_keys(id,organization_id,token_hash,created_at) VALUES (:id,:org,:hash,:now)"
                ),
                {
                    "id": uuid4(),
                    "org": org_id,
                    "hash": hash_api_key("rehearsal-only-" + unique),
                    "now": utcnow(),
                },
            )
        source.dispose()
        run("-m", "alembic", "upgrade", "head")
        run("-m", "alembic", "check")
        run("scripts/rehearse_restore.py", "--stage", "seed")
        extra = ["--postgres-container", args.postgres_container] if args.postgres_container else []
        run("-m", "app.backup", "backup", str(root / "backup"), "--quiesced", "--snapshot", *extra)
        run("-m", "app.backup", "verify", str(root / "backup"))
        env.update(
            DATABASE_URL=url.set(database=names[1]).render_as_string(hide_password=False),
            SENTINETH_STORAGE_DIR=str(root / "restored"),
            QDRANT_COLLECTION=collections[1],
        )
        run("-m", "app.backup", "restore", str(root / "backup"), "--quiesced", *extra)
        run("-m", "alembic", "check")
        run("scripts/rehearse_restore.py", "--stage", "recover")
        if args.image:
            import httpx

            # Hosted image uses real model dimensions; fake-only rehearsals do not prove it.
            if not args.live_provider:
                parser.error("--image requires --live-provider")
            runtime = env.copy()
            runtime.update(
                DATABASE_URL=url.set(
                    database=names[1], host="host.docker.internal"
                ).render_as_string(hide_password=False),
                QDRANT_URL="http://host.docker.internal:6333",
                NVIDIA_API_KEY=settings.nvidia_api_key.get_secret_value(),
                OPENROUTER_API_KEY=settings.openrouter_api_key.get_secret_value(),
                SENTINETH_STORAGE_DIR="/data/documents",
                SENTINETH_METRICS_TOKEN=uuid4().hex,
                SENTINETH_ENVIRONMENT="production",
                SENTINETH_ALLOWED_HOSTS='["localhost","127.0.0.1"]',
            )
            command = [
                "docker",
                "run",
                "-d",
                "--rm",
                "--name",
                container_name,
                "-p",
                "127.0.0.1:18003:8000",
            ]
            for key in (
                "DATABASE_URL",
                "QDRANT_URL",
                "QDRANT_COLLECTION",
                "EMBEDDING_PROVIDER",
                "QDRANT_HYBRID",
                "NVIDIA_API_KEY",
                "OPENROUTER_API_KEY",
                "SENTINETH_STORAGE_DIR",
                "SENTINETH_METRICS_TOKEN",
                "SENTINETH_ENVIRONMENT",
                "SENTINETH_ALLOWED_HOSTS",
            ):
                command += ["-e", key]
            subprocess.run(
                [*command, args.image], env=runtime, check=True, stdout=subprocess.DEVNULL
            )
            with httpx.Client(base_url="http://127.0.0.1:18003", timeout=60) as client:
                for _ in range(30):
                    try:
                        response = client.get("/ready")
                        if response.status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(1)
                else:
                    raise AssertionError("Container readiness never succeeded")
                login = client.post(
                    "/auth/login",
                    json={
                        "email": "rehearsal@example.com",
                        "password": "synthetic-rehearsal-password",
                    },
                )
                assert login.status_code == 200, login.status_code
                headers = {"Authorization": "Bearer " + login.json()["access_token"]}
                org = client.get("/organizations", headers=headers).json()[0]
                response = client.post(
                    "/organizations/" + org["id"] + "/query",
                    headers=headers,
                    json={"query": "Who owns deployment?"},
                )
                assert response.status_code == 200 and response.json()["sources"], (
                    response.status_code
                )
                assert "Morgan" in response.json()["answer"]
                print(
                    "Container verified: readiness, login, organization listing, NVIDIA retrieval and cited LLM answer"
                )
        report = {
            "passed": True,
            "provider": "nvidia" if args.live_provider else "deterministic test double",
            "postgres": True,
            "qdrant": True,
            "snapshot_saved": True,
            "source_rebuild": True,
            "container_verified": bool(args.image),
            "elapsed_seconds": round(time.perf_counter() - started, 2),
        }
        (root / "result.json").write_text(json.dumps(report, indent=2) + "\n")
        print("Recovery rehearsal passed. Report: " + str(root / "result.json"))
    finally:
        if args.image:
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        with closing(
            QdrantClient(
                url=settings.qdrant_url, api_key=settings.qdrant_api_key.get_secret_value() or None
            )
        ) as client:
            for collection in collections:
                if client.collection_exists(collection):
                    client.delete_collection(collection)
        with admin.connect() as connection:
            for name in created:
                connection.execute(text(f"DROP DATABASE {name} WITH (FORCE)"))
        admin.dispose()


if __name__ == "__main__":
    main()
