from uuid import UUID

from app.providers.embeddings.base import EmbeddingProvider
from app.providers.rerank.base import RerankProvider
from app.providers.vector.base import VectorStore
from app.services.lexical_service import encode_query


# How many candidates the reranker is shown. It can only reorder what it
# is given, so this is the real recall ceiling once reranking is on: a
# chunk ranked 31st by the first stage cannot be moved to first, however
# obviously right it is. Wider costs latency roughly linearly, since every
# candidate is a forward pass through the cross-encoder.
RERANK_DEPTH = 30


async def retrieve(
    organization_id: UUID | str,
    query: str,
    embedding_provider: EmbeddingProvider,
    vector_store: VectorStore,
    limit: int = 5,
    rerank_provider: RerankProvider | None = None,
) -> list[dict]:
    if not query or not query.strip():
        raise ValueError("Query cannot be empty.")

    max_limit = 20
    if limit < 1:
        raise ValueError("Limit must be at least 1.")
    if limit > max_limit:
        raise ValueError(f"Limit cannot exceed {max_limit}.")

    cleaned = query.strip()

    query_vector = (
        await embedding_provider.embed(
            [cleaned],
            input_type="query",
        )
    )[0]

    # Fetch wider than asked for when something downstream will reorder,
    # and only then: a shortlist nothing reranks is just extra rows to
    # throw away.
    depth = RERANK_DEPTH if rerank_provider is not None else limit

    results = await vector_store.search(
        organization_id=str(organization_id),
        query_vector=query_vector,
        limit=depth,
        # Always offered, and used only by a store that has a lexical
        # index. Encoding it costs a regex over one short question, which
        # is cheaper than asking the store what it supports.
        sparse_query=encode_query(cleaned),
    )

    if rerank_provider is not None:
        results = _rerank(rerank_provider, cleaned, results, limit)

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


def _rerank(
    provider: RerankProvider,
    query: str,
    results: list[dict],
    limit: int,
) -> list[dict]:
    """Re-score a shortlist with the cross-encoder and keep the best."""
    passages = [(result.get("payload") or {}).get("content") or "" for result in results]

    scores = provider.score(query, passages)

    # The reranker's score replaces the retriever's. They measure
    # different things on different scales, and a caller comparing a
    # score against a threshold should be reading the one that decided
    # the order it is looking at.
    rescored = [
        {**result, "score": float(score)}
        for result, score in zip(results, scores, strict=True)
    ]
    rescored.sort(key=lambda result: result["score"], reverse=True)

    return rescored[:limit]
