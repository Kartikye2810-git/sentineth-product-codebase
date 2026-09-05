from uuid import UUID

from app.providers.embeddings.base import EmbeddingProvider
from app.providers.vector.base import VectorStore


async def retrieve(
    organization_id: UUID | str,
    query: str,
    embedding_provider: EmbeddingProvider,
    vector_store: VectorStore,
    limit: int = 5,
) -> list[dict]:
    if not query or not query.strip():
        raise ValueError("Query cannot be empty.")

    max_limit = 20
    if limit < 1:
        raise ValueError("Limit must be at least 1.")
    if limit > max_limit:
        raise ValueError(f"Limit cannot exceed {max_limit}.")

    query_vector = (await embedding_provider.embed([query.strip()]))[0]
    results = await vector_store.search(
        organization_id=str(organization_id),
        query_vector=query_vector,
        limit=limit,
    )

    normalized_results: list[dict] = []
    for result in results:
        payload = result.get("payload", {}) or {}
        if not isinstance(payload, dict):
            continue

        item = {
            "id": result.get("id"),
            "score": result.get("score"),
            "document_id": payload.get("document_id"),
            "chunk_id": payload.get("chunk_id"),
            "chunk_index": payload.get("chunk_index"),
            "page_number": payload.get("page_number"),
            "filename": payload.get("filename"),
            "content": payload.get("content"),
        }
        normalized_results.append(item)

    return normalized_results
