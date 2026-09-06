import os
import uuid
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from app.providers.vector.base import VectorStore


# The dense vector stays unnamed, so collections written before hybrid
# existed are still read the same way. The sparse one has to be named.
SPARSE_VECTOR_NAME = "lexical"

# How deep each retriever goes before the two lists are fused. Wider than
# the number of results asked for, because fusion can only reorder what it
# was given: a chunk only one of the two retrievers found has to be inside
# that retriever's list to survive the merge at all.
PREFETCH_LIMIT = 50


class QdrantVectorStore(VectorStore):
    def __init__(
        self,
        url: str | None = None,
        api_key: str | None = None,
        collection_name: str = "sentineth_documents",
        vector_size: int = 384,
        timeout: int = 30,
        hybrid: bool = False,
    ) -> None:
        self.url = url or os.getenv(
            "QDRANT_URL",
            "http://localhost:6333",
        )
        self.api_key = api_key or os.getenv("QDRANT_API_KEY")
        self.collection_name = collection_name

        if vector_size <= 0:
            raise ValueError(
                "vector_size must be greater than zero."
            )

        self.vector_size = int(vector_size)

        # Fixed at creation, like the dense vector size: Qdrant rejects
        # update_collection with a sparse config it was not built with
        # ("Wrong input: Not existing vector name"). So turning hybrid on
        # for an existing corpus is a rebuild into a new collection, which
        # is the procedure scripts/reindex.py already performs.
        self.hybrid = bool(hybrid)

        self._client = QdrantClient(
            url=self.url,
            api_key=self.api_key,
            timeout=timeout,
        )

        self._ensure_collection()

    def _ensure_collection(self) -> None:
        if not self._client.collection_exists(
            self.collection_name
        ):
            self._client.create_collection(
                self.collection_name,
                vectors_config=qmodels.VectorParams(
                    size=self.vector_size,
                    distance=qmodels.Distance.COSINE,
                ),
                # IDF is computed by Qdrant, over the collection, at query
                # time. Keeping the corpus statistics on the side that owns
                # the corpus is what stops them going stale as documents
                # are added and deleted.
                sparse_vectors_config=(
                    {
                        SPARSE_VECTOR_NAME: qmodels.SparseVectorParams(
                            modifier=qmodels.Modifier.IDF,
                        )
                    }
                    if self.hybrid
                    else None
                ),
            )

        # Outside the create branch on purpose: a collection that predates
        # this index needs it too, and re-issuing an identical index is a
        # no-op. Without it, every tenant-scoped search filters by scanning,
        # so one organization's latency grows with every other organization's
        # data.
        self._client.create_payload_index(
            self.collection_name,
            field_name="organization_id",
            field_schema=qmodels.PayloadSchemaType.KEYWORD,
        )

    def _build_point_id(
        self,
        organization_id: str,
        payload: dict[str, Any],
        index: int,
    ) -> str:
        document_id = str(
            payload.get("document_id", "unknown")
        )

        chunk_id = str(
            payload.get(
                "chunk_id",
                f"{organization_id}:{index}",
            )
        )

        chunk_index = payload.get(
            "chunk_index",
            index,
        )

        raw = (
            f"{organization_id}:"
            f"{document_id}:"
            f"{chunk_id}:"
            f"{chunk_index}"
        )

        return str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                raw,
            )
        )

    def _normalize_payload(
        self,
        organization_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError(
                "Each payload must be a dictionary."
            )

        normalized = dict(payload)
        normalized["organization_id"] = str(
            organization_id
        )

        return normalized

    async def upsert(
        self,
        organization_id: str,
        vectors: list[list[float]],
        payloads: list[dict[str, Any]],
        sparse_vectors: list[tuple[list[int], list[float]]] | None = None,
    ) -> None:
        if len(vectors) != len(payloads):
            raise ValueError(
                "vectors and payloads must have matching lengths."
            )

        if sparse_vectors is not None:
            if len(sparse_vectors) != len(vectors):
                raise ValueError(
                    "sparse_vectors and vectors must have matching lengths."
                )

            if not self.hybrid:
                raise ValueError(
                    "This collection has no sparse vector configured. "
                    "Rebuild it with hybrid=True to index lexically."
                )

        org_id = str(organization_id)
        points: list[qmodels.PointStruct] = []

        for index, (vector, payload) in enumerate(
            zip(vectors, payloads, strict=True)
        ):
            if len(vector) != self.vector_size:
                raise ValueError(
                    f"Vector dimension {len(vector)} does not "
                    f"match Qdrant dimension {self.vector_size}."
                )

            normalized_payload = self._normalize_payload(
                org_id,
                payload,
            )

            payload_org_id = str(
                normalized_payload.get(
                    "organization_id",
                    "",
                )
            )

            if payload_org_id != org_id:
                raise ValueError(
                    "Vector payload organization_id does not "
                    "match the supplied organization_id."
                )

            # A bare list keeps writing to the unnamed dense vector. Once
            # there is a second vector it has to be a mapping, and the
            # dense one is keyed by the empty string - the name Qdrant
            # gives the vector that never had one.
            point_vector: Any = vector

            if sparse_vectors is not None:
                indices, values = sparse_vectors[index]
                point_vector = {
                    "": vector,
                    SPARSE_VECTOR_NAME: qmodels.SparseVector(
                        indices=indices,
                        values=values,
                    ),
                }

            # Qdrant's typed object rather than a dictionary: the HTTP
            # backend accepts both, the in-process one only PointStruct.
            points.append(
                qmodels.PointStruct(
                    id=self._build_point_id(
                        org_id,
                        normalized_payload,
                        index,
                    ),
                    vector=point_vector,
                    payload=normalized_payload,
                )
            )

        if not points:
            return

        self._client.upsert(
            collection_name=self.collection_name,
            points=points,
            wait=True,
        )

    async def search(
        self,
        organization_id: str,
        query_vector: list[float],
        limit: int = 5,
        sparse_query: tuple[list[int], list[float]] | None = None,
    ) -> list[dict[str, Any]]:
        if len(query_vector) != self.vector_size:
            raise ValueError(
                f"Query vector dimension {len(query_vector)} "
                f"does not match Qdrant dimension "
                f"{self.vector_size}."
            )

        if limit <= 0:
            return []

        org_id = str(organization_id)

        filter_clause = qmodels.Filter(
            must=[
                qmodels.FieldCondition(
                    key="organization_id",
                    match=qmodels.MatchValue(
                        value=org_id,
                    ),
                )
            ]
        )

        if sparse_query is not None and self.hybrid:
            indices, values = sparse_query

            # Reciprocal rank fusion rather than a weighted sum of scores.
            # Cosine similarity and a BM25 score are not on the same scale
            # and neither one is calibrated, so any weighting between them
            # would be a constant picked by hand. RRF only reads positions,
            # which both lists genuinely have, and so needs no such
            # constant.
            response = self._client.query_points(
                collection_name=self.collection_name,
                prefetch=[
                    qmodels.Prefetch(
                        query=query_vector,
                        filter=filter_clause,
                        limit=PREFETCH_LIMIT,
                    ),
                    qmodels.Prefetch(
                        query=qmodels.SparseVector(
                            indices=indices,
                            values=values,
                        ),
                        using=SPARSE_VECTOR_NAME,
                        filter=filter_clause,
                        limit=PREFETCH_LIMIT,
                    ),
                ],
                query=qmodels.FusionQuery(fusion=qmodels.Fusion.RRF),
                limit=limit,
                with_payload=True,
            )
        else:
            response = self._client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                query_filter=filter_clause,
                limit=limit,
                with_payload=True,
            )

        results = response.points

        return [
            {
                "id": str(item.id),
                "score": float(
                    getattr(item, "score", 0.0)
                ),
                "payload": dict(
                    getattr(item, "payload", {})
                    or {}
                ),
            }
            for item in results
        ]

    async def delete_document(self, organization_id: str, document_id: str) -> None:
        self._client.delete(
            collection_name=self.collection_name,
            points_selector=qmodels.FilterSelector(filter=qmodels.Filter(must=[
                qmodels.FieldCondition(key="organization_id", match=qmodels.MatchValue(value=str(organization_id))),
                qmodels.FieldCondition(key="document_id", match=qmodels.MatchValue(value=str(document_id))),
            ])),
            wait=True,
        )
