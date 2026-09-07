import os
import uuid
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from app.providers.vector.base import VectorStore


class QdrantVectorStore(VectorStore):
    def __init__(
        self,
        url: str | None = None,
        api_key: str | None = None,
        collection_name: str = "sentineth_documents",
        vector_size: int = 384,
        timeout: int = 30,
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
    ) -> None:
        if len(vectors) != len(payloads):
            raise ValueError(
                "vectors and payloads must have matching lengths."
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

            # Qdrant's typed object rather than a dictionary: the HTTP
            # backend accepts both, the in-process one only PointStruct.
            points.append(
                qmodels.PointStruct(
                    id=self._build_point_id(
                        org_id,
                        normalized_payload,
                        index,
                    ),
                    vector=vector,
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
