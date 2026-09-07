"""In-memory provider doubles.

These implement the real provider interfaces, so a signature drift
between a base class and its implementations shows up as a test
failure. See AGENTS.md section 20.
"""

import math
import re
import zlib
from typing import Any

from app.providers.embeddings.base import EmbeddingProvider
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
    ) -> None:
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
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
    ) -> None:
        if len(vectors) != len(payloads):
            raise ValueError(
                "vectors and payloads must have matching lengths."
            )

        org_id = str(organization_id)

        for index, (vector, payload) in enumerate(zip(vectors, payloads, strict=True)):
            stored_payload = dict(payload)
            stored_payload["organization_id"] = org_id

            point_id = (
                f"{org_id}:"
                f"{stored_payload.get('chunk_id', index)}"
            )

            self.points[point_id] = {
                "vector": list(vector),
                "payload": stored_payload,
            }

    async def search(
        self,
        organization_id: str,
        query_vector: list[float],
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []

        org_id = str(organization_id)
        scored: list[tuple[float, str, dict[str, Any]]] = []

        for point_id, point in self.points.items():
            if point["payload"].get("organization_id") != org_id:
                continue

            score = sum(
                a * b
                for a, b in zip(query_vector, point["vector"], strict=True)
            )
            scored.append((score, point_id, point))

        scored.sort(key=lambda row: row[0], reverse=True)

        return [
            {
                "id": point_id,
                "score": float(score),
                "payload": dict(point["payload"]),
            }
            for score, point_id, point in scored[:limit]
        ]

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
