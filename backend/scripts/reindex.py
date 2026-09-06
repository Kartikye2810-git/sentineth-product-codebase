"""Rebuild every vector into a new Qdrant collection, without downtime.

Changing embedding model changes vector length, and a Qdrant collection's
vector size is fixed when it is created. So a model swap cannot be done in
place: 2048-dimension vectors have nowhere to go in a 384-dimension
collection. This builds the replacement alongside the original instead.

Why this is safe to run against production:

  * It only ever writes to the target collection. The collection the
    application is currently reading is never touched, so a failure
    half-way leaves serving traffic exactly as it was.
  * Chunk text lives in Postgres (`document_chunks`), not in Qdrant, so
    vectors are derived data and can be rebuilt at any time. Nothing here
    is a one-way door.
  * Cutover is `EMBEDDING_PROVIDER` and `QDRANT_COLLECTION` in the
    environment plus a restart. Rollback is the previous values and
    another restart - the old collection is still there and still correct,
    which is what makes the rollback a rollback rather than a second
    migration.

Usage:

    # 1. Build the new collection while the old one keeps serving.
    python scripts/reindex.py --provider nvidia \\
        --collection sentineth_documents_nemotron

    # 2. Check it before sending traffic to it.
    python scripts/reindex.py --provider nvidia \\
        --collection sentineth_documents_nemotron --verify-only

    # 3. Cut over: set both env vars, restart.
    # 4. Roll back: set them back, restart. Nothing to rebuild.
"""

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.db.database import SessionLocal  # noqa: E402
from app.db.models import Document, DocumentChunk  # noqa: E402
from app.dependencies import EMBEDDING_PROVIDERS  # noqa: E402
from app.providers.embeddings.base import EmbeddingProvider  # noqa: E402
from app.providers.vector.qdrant import QdrantVectorStore  # noqa: E402


# Matched to the NVIDIA endpoint's maximum batch, so a database page turns
# into exactly one embedding request rather than two half-full ones.
BATCH = 256


def load_chunks(session: Any) -> list[tuple[DocumentChunk, Document]]:
    """Every indexable chunk, with the document it belongs to.

    Ordered so a run is reproducible and a partial run is resumable by
    eye. Only READY documents: anything else has no vectors in the old
    collection either, and inventing some would make the two disagree.
    """
    return (
        session.query(DocumentChunk, Document)
        .join(Document, DocumentChunk.document_id == Document.id)
        .filter(Document.status == "READY")
        .order_by(Document.id, DocumentChunk.chunk_index)
        .all()
    )


def payload_for(chunk: DocumentChunk, document: Document) -> dict[str, Any]:
    # Must match ingestion_service exactly. A payload that differs between
    # the two write paths would make search results change shape depending
    # on whether a document had been migrated, which is the kind of bug
    # that only shows up for the customers who were already here.
    return {
        "organization_id": str(document.organization_id),
        "document_id": str(document.id),
        "chunk_id": str(chunk.id),
        "chunk_index": chunk.chunk_index,
        "page_number": chunk.page_number,
        "content": chunk.content,
        "filename": document.filename,
    }


async def reindex(
    provider: EmbeddingProvider,
    store: QdrantVectorStore,
    rows: list[tuple[DocumentChunk, Document]],
    dry_run: bool,
) -> int:
    written = 0
    started = time.perf_counter()

    for start in range(0, len(rows), BATCH):
        batch = rows[start : start + BATCH]

        vectors = await provider.embed(
            [chunk.content for chunk, _ in batch],
            # Passages, not queries. On an asymmetric model this is the
            # difference between a working index and a quietly bad one.
            input_type="passage",
        )

        if len(vectors) != len(batch):
            raise RuntimeError(
                f"Provider returned {len(vectors)} vectors for "
                f"{len(batch)} chunks."
            )

        if not dry_run:
            # Grouped by organization because upsert is scoped to one, and
            # point ids are derived from it.
            by_org: dict[str, list[int]] = {}

            for offset, (_, document) in enumerate(batch):
                by_org.setdefault(str(document.organization_id), []).append(offset)

            for organization_id, offsets in by_org.items():
                await store.upsert(
                    organization_id=organization_id,
                    vectors=[vectors[offset] for offset in offsets],
                    payloads=[payload_for(*batch[offset]) for offset in offsets],
                )

        written += len(batch)
        elapsed = time.perf_counter() - started
        rate = written / elapsed if elapsed else 0.0

        print(
            f"  {written:>7,} / {len(rows):,} chunks  "
            f"{elapsed:>6.1f}s  {rate:>6.1f} chunks/s",
            flush=True,
        )

    return written


def verify(store: QdrantVectorStore, expected: int) -> bool:
    """Compare what Postgres says should exist against what Qdrant holds."""
    actual = store._client.count(store.collection_name, exact=True).count

    print(f"\nPostgres chunks (READY documents): {expected:,}")
    print(f"Qdrant points in {store.collection_name}: {actual:,}")

    if actual == expected:
        print("Counts match.")
        return True

    print(f"MISMATCH: {expected - actual:+,} missing from Qdrant.")
    return False


async def main_async(args: argparse.Namespace) -> int:
    if args.provider not in EMBEDDING_PROVIDERS:
        raise SystemExit(
            f"Unknown provider {args.provider!r}. "
            f"Expected one of: {', '.join(sorted(EMBEDDING_PROVIDERS))}."
        )

    provider = EMBEDDING_PROVIDERS[args.provider]()

    store = QdrantVectorStore(
        collection_name=args.collection,
        vector_size=provider.dimension,
    )

    print(f"provider    {type(provider).__name__} ({provider.dimension} dims)")
    print(f"collection  {args.collection}")
    print(f"qdrant      {store.url}")

    session = SessionLocal()

    try:
        rows = load_chunks(session)
        print(f"chunks      {len(rows):,} across READY documents\n")

        if args.verify_only:
            return 0 if verify(store, len(rows)) else 1

        if not rows:
            print("Nothing to do.")
            return 0

        written = await reindex(provider, store, rows, args.dry_run)

        if args.dry_run:
            print(f"\nDry run: embedded {written:,} chunks, wrote nothing.")
            return 0

        print(f"\nWrote {written:,} chunks.")

        return 0 if verify(store, len(rows)) else 1
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--provider",
        default=os.getenv("EMBEDDING_PROVIDER", "local"),
        help="embedding provider to build the new index with",
    )
    parser.add_argument(
        "--collection",
        required=True,
        help="target Qdrant collection; must not be the one being served",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="embed everything but write nothing, to price and time a run",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="compare Postgres chunk count against Qdrant point count",
    )

    raise SystemExit(asyncio.run(main_async(parser.parse_args())))


if __name__ == "__main__":
    main()
