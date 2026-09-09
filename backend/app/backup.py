"""Quiesced PostgreSQL + source-file backup and fail-closed restore.

Run with the API and workers stopped. Restores require a NEW empty database
and an empty storage directory. Supply PostgreSQL 17 client tools on PATH,
or --postgres-container to use a local PostgreSQL 17 container's tools.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from contextlib import closing
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

import httpx
from qdrant_client import QdrantClient
from sqlalchemy import inspect, select, text, update
from sqlalchemy.engine import make_url

from app.audit import record
from app.clock import utcnow
from app.db.database import SessionLocal, engine
from app.db.models import Document, IngestionJob, Invitation, OrganizationApiKey, User, UserSession
from app.settings import get_settings


def digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def postgres_tool(tool, arguments, container=None, *, source=None, destination=None):
    url = make_url(get_settings().database_url.get_secret_value())
    if url.get_backend_name() != "postgresql":
        raise ValueError("Backup and restore require PostgreSQL")
    env = os.environ.copy()
    connection = {
        "PGHOST": url.host or "localhost",
        "PGPORT": str(url.port or 5432),
        "PGUSER": url.username or "",
        "PGPASSWORD": url.password or "",
        "PGDATABASE": url.database or "",
    }
    if "sslmode" in url.query:
        connection["PGSSLMODE"] = url.query["sslmode"]
    env.update(connection)
    command = []
    if container:
        command = ["docker", "exec", "-i"]
        for key in connection:
            command += ["-e", key]
        command += [container]
    command += [tool, *arguments]
    result = subprocess.run(
        command,
        env=env,
        stdin=source,
        stdout=destination or subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        # pg_restore errors may contain whole data rows. Keep them out of logs.
        raise RuntimeError(
            f"{tool} failed (exit {result.returncode}); inspect the database and client versions"
        )


def snapshot(directory):
    settings = get_settings()
    with closing(
        QdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key.get_secret_value() or None,
            timeout=120,
        )
    ) as client:
        result = client.create_snapshot(settings.collection_name)
        name = result.name
    url = (
        settings.qdrant_url.rstrip("/")
        + "/collections/"
        + quote(settings.collection_name, safe="")
        + "/snapshots/"
        + quote(name, safe="")
    )
    path = directory / "vectors.snapshot"
    headers = (
        {"api-key": settings.qdrant_api_key.get_secret_value()}
        if settings.qdrant_api_key.get_secret_value()
        else {}
    )
    with httpx.stream("GET", url, headers=headers, timeout=120) as response:
        response.raise_for_status()
        with path.open("xb") as out:
            for chunk in response.iter_bytes():
                out.write(chunk)
    return {"filename": path.name, "sha256": digest(path), "server_snapshot": name}


def backup(directory, container=None, include_snapshot=False):
    settings = get_settings()
    directory = Path(directory).resolve()
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    source_root = settings.storage_dir.resolve()
    if directory.is_relative_to(source_root):
        raise ValueError("Backups must be stored outside document storage")
    with SessionLocal() as db:
        if db.scalar(select(IngestionJob.id).where(IngestionJob.status == "PROCESSING").limit(1)):
            raise ValueError("A worker is still processing; stop workers before backing up")
        revision = db.scalar(text("SELECT version_num FROM alembic_version"))
        documents = list(db.scalars(select(Document)))
        files = []
        for document in documents:
            source = Path(document.storage_path).resolve()
            if not source.is_relative_to(source_root) or not source.is_file():
                raise ValueError(
                    "A document source is missing or outside storage; backup is incomplete"
                )
            relative = source.relative_to(source_root)
            target = directory / "documents" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            checksum = digest(target)
            if document.content_hash and document.content_hash != checksum:
                raise ValueError("Source checksum differs from its document record")
            files.append(
                {"document_id": str(document.id), "path": relative.as_posix(), "sha256": checksum}
            )
        with (directory / "postgres.dump").open("xb") as out:
            postgres_tool(
                "pg_dump",
                ["--format=custom", "--no-owner", "--no-acl", "--schema=public"],
                container,
                destination=out,
            )
        manifest = {
            "format": 1,
            "created_at": utcnow().isoformat(),
            "revision": revision,
            "database_sha256": digest(directory / "postgres.dump"),
            "embedding_provider": settings.embedding_provider,
            "collection": settings.collection_name,
            "hybrid": settings.qdrant_hybrid,
            "documents": files,
        }
        if include_snapshot:
            manifest["snapshot"] = snapshot(directory)
        # Written last: missing manifest means the backup did not complete.
        (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def checked_manifest(directory):
    directory = Path(directory).resolve()
    manifest = json.loads((directory / "manifest.json").read_text())
    if (
        manifest.get("format") != 1
        or digest(directory / "postgres.dump") != manifest["database_sha256"]
    ):
        raise ValueError("Invalid or corrupt database backup")
    ids, paths = set(), set()
    for item in manifest["documents"]:
        identifier = str(UUID(item["document_id"]))
        relative = Path(item["path"])
        source = (directory / "documents" / relative).resolve()
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not source.is_relative_to(directory / "documents")
        ):
            raise ValueError("Unsafe source path in backup")
        if identifier in ids or str(relative) in paths or digest(source) != item["sha256"]:
            raise ValueError("Duplicate or corrupt source file")
        ids.add(identifier)
        paths.add(str(relative))
    if (
        manifest.get("snapshot")
        and digest(directory / "vectors.snapshot") != manifest["snapshot"]["sha256"]
    ):
        raise ValueError("Corrupt vector snapshot")
    return manifest


def restore(directory, container=None):
    directory = Path(directory).resolve()
    manifest = checked_manifest(directory)
    storage = get_settings().storage_dir.resolve()
    if inspect(engine).get_table_names(schema="public") or (
        storage.exists() and any(storage.iterdir())
    ):
        raise ValueError("Restore requires an empty database and empty storage directory")
    storage.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / "postgres.dump").open("rb") as source:
        postgres_tool(
            "pg_restore",
            [
                "--dbname",
                make_url(get_settings().database_url.get_secret_value()).database,
                "--no-owner",
                "--no-acl",
                "--clean",
                "--if-exists",
                "--single-transaction",
                "--exit-on-error",
            ],
            container,
            source=source,
        )
    with SessionLocal() as db:
        stored_ids = {str(i) for i in db.scalars(select(Document.id))}
        if stored_ids != {item["document_id"] for item in manifest["documents"]}:
            raise ValueError("Manifest and database document sets differ; do not start the service")
        for item in manifest["documents"]:
            relative = Path(item["path"])
            target = storage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(directory / "documents" / relative, target)
            document = db.get(Document, UUID(item["document_id"]))
            if document.content_hash and document.content_hash != item["sha256"]:
                raise ValueError("Restored database and source checksum differ")
            document.storage_path = str(target)
        now = utcnow()
        db.execute(
            update(UserSession).where(UserSession.revoked_at.is_(None)).values(revoked_at=now)
        )
        db.execute(
            update(OrganizationApiKey)
            .where(OrganizationApiKey.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        db.execute(update(Invitation).where(Invitation.revoked_at.is_(None)).values(revoked_at=now))
        db.execute(update(User).values(disabled=True))
        record(
            db,
            "operator.backup_restored",
            backup_created_at=manifest["created_at"],
            credentials_invalidated=True,
        )
        db.commit()
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["backup", "verify", "restore"])
    parser.add_argument("directory", type=Path)
    parser.add_argument("--postgres-container")
    parser.add_argument("--snapshot", action="store_true")
    parser.add_argument(
        "--quiesced", action="store_true", help="Confirm all API and worker writers have stopped"
    )
    args = parser.parse_args()
    if args.action != "verify" and not args.quiesced:
        parser.error("Stop API and worker processes, then supply --quiesced")
    if args.action == "backup":
        manifest = backup(args.directory, args.postgres_container, args.snapshot)
    elif args.action == "restore":
        manifest = restore(args.directory, args.postgres_container)
    else:
        manifest = checked_manifest(args.directory)
    print(
        f"{args.action} complete: {len(manifest['documents'])} document sources verified; schema {manifest['revision']}"
    )


if __name__ == "__main__":
    main()
