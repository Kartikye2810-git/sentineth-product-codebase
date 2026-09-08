"""In-memory provider doubles.

These implement the real provider interfaces, so a signature drift
between a base class and its implementations shows up as a test
failure. See AGENTS.md section 20.
"""

import math
import re
import zlib
from typing import Any

from app.providers.embeddings.base import EmbeddingProvider, InputType
from app.providers.llm.base import LLMProvider
from app.providers.vector.base import VectorStore


_WORD = re.compile(r"[a-z0-9]+")


class FakeEmbeddingProvider(EmbeddingProvider):
    """Deterministic hashed bag-of-words embeddings.

    Not semantic, but texts sharing words land closer together, which is
    enough to assert that retrieval ranks the relevant chunk first.
    """

    def __init__(
        self,
        dimension: int = 64,
        max_input_tokens: int = 256,
    ) -> None:
        self._dimension = dimension
        self._max_input_tokens = max_input_tokens
        self.input_types: list[InputType] = []

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def max_input_tokens(self) -> int:
        return self._max_input_tokens

    @property
    def chunk_tokens(self) -> int:
        # The window, so tests that shrink the window to force a document
        # across several chunks still control chunk count with one knob.
        return self._max_input_tokens

    def count_tokens(self, texts: list[str]) -> list[int]:
        # This double's tokenizer is its word splitter, the same one its
        # vectors are built from, so counts describe what it actually reads.
        return [len(_WORD.findall(text.lower())) for text in texts]

    async def embed(
        self,
        texts: list[str],
        *,
        input_type: InputType,
    ) -> list[list[float]]:
        # Recorded rather than ignored: an asymmetric provider is only
        # correct if indexing says "passage" and searching says "query",
        # and a test can assert that here without a network call.
        self.input_types.append(input_type)

        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self._dimension

        for word in _WORD.findall(text.lower()):
            # crc32 rather than hash(): str hashing is salted per process.
            bucket = zlib.crc32(word.encode("utf-8")) % self._dimension
            vector[bucket] += 1.0

        norm = math.sqrt(sum(value * value for value in vector))

        if norm == 0.0:
            return vector

        return [value / norm for value in vector]


class FakeVectorStore(VectorStore):
    """In-memory vector store that enforces organization filtering."""

    def __init__(self) -> None:
        self.points: dict[str, dict[str, Any]] = {}

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

        if sparse_vectors is not None and len(sparse_vectors) != len(vectors):
            raise ValueError(
                "sparse_vectors and vectors must have matching lengths."
            )

        org_id = str(organization_id)

        for index, (vector, payload) in enumerate(zip(vectors, payloads, strict=True)):
            stored_payload = dict(payload)
            stored_payload["organization_id"] = org_id

            point_id = (
                f"{org_id}:"
                f"{stored_payload.get('chunk_id', index)}"
            )

            sparse = None

            if sparse_vectors is not None:
                indices, values = sparse_vectors[index]
                sparse = dict(zip(indices, values, strict=True))

            self.points[point_id] = {
                "vector": list(vector),
                "sparse": sparse,
                "payload": stored_payload,
            }

    async def search(
        self,
        organization_id: str,
        query_vector: list[float],
        limit: int = 5,
        sparse_query: tuple[list[int], list[float]] | None = None,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []

        org_id = str(organization_id)

        mine = {
            point_id: point
            for point_id, point in self.points.items()
            if point["payload"].get("organization_id") == org_id
        }

        dense = self._rank(
            mine,
            lambda point: sum(
                a * b
                for a, b in zip(query_vector, point["vector"], strict=True)
            ),
        )

        if sparse_query is None:
            ranked = dense
        else:
            indices, values = sparse_query
            query_terms = dict(zip(indices, values, strict=True))

            sparse = self._rank(
                mine,
                lambda point: sum(
                    weight * (point["sparse"] or {}).get(term, 0.0)
                    for term, weight in query_terms.items()
                ),
            )
            ranked = self._fuse(dense, sparse)

        return [
            {
                "id": point_id,
                "score": float(score),
                "payload": dict(mine[point_id]["payload"]),
            }
            for point_id, score in ranked[:limit]
        ]

    @staticmethod
    def _rank(
        points: dict[str, dict[str, Any]],
        score: Any,
    ) -> list[tuple[str, float]]:
        # Zero-scoring points are dropped rather than ranked last. A
        # lexical retriever that shares no term with the query has not
        # found a weak match, it has found nothing, and fusing it in as a
        # ranked result would hand it credit for the ordering it inherited.
        scored = [
            (point_id, score(point))
            for point_id, point in points.items()
        ]

        return sorted(
            (row for row in scored if row[1] > 0),
            key=lambda row: row[1],
            reverse=True,
        )

    @staticmethod
    def _fuse(*rankings: list[tuple[str, float]]) -> list[tuple[str, float]]:
        """Reciprocal rank fusion, matching Qdrant's constant.

        Qdrant uses k=2 over zero-indexed ranks - checked against a live
        collection, where a point ranked first in one list and second in
        the other scored 1/2 + 1/3 = 0.8333. Guessing the textbook k=60
        here would make the double agree with the real store on which
        chunks come back but not on their order.
        """
        fused: dict[str, float] = {}

        for ranking in rankings:
            for rank, (point_id, _) in enumerate(ranking):
                fused[point_id] = fused.get(point_id, 0.0) + 1 / (2 + rank)

        return sorted(fused.items(), key=lambda row: row[1], reverse=True)

    async def delete_document(self, organization_id: str, document_id: str) -> None:
        for point_id, point in list(self.points.items()):
            payload = point["payload"]
            if payload.get("organization_id") == str(organization_id) and payload.get("document_id") == str(document_id):
                del self.points[point_id]


class FakeLLMProvider(LLMProvider):
    """Records the prompts it receives and returns a fixed answer."""

    def __init__(self, answer: str = "Fake grounded answer.") -> None:
        self.answer = answer
        self.calls: list[list[dict[str, str]]] = []

    async def generate(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> str:
        if not messages:
            raise ValueError("At least one message is required.")

        self.calls.append(messages)

        return self.answer
