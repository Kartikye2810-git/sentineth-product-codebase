from abc import ABC, abstractmethod
from typing import Any


class VectorStore(ABC):

    # Whether this store holds a lexical index beside the dense one.
    # A plain class attribute, not a property, so an implementation sets
    # it per instance - for Qdrant it is a fact about the collection that
    # was opened, not about the class. False here means callers can read
    # it on any store without asking whether it exists.
    hybrid: bool = False

    @abstractmethod
    async def upsert(
        self,
        organization_id: str,
        vectors: list[list[float]],
        payloads: list[dict[str, Any]],
        sparse_vectors: list[tuple[list[int], list[float]]] | None = None,
    ) -> None:
        pass

    @abstractmethod
    async def search(
        self,
        organization_id: str,
        query_vector: list[float],
        limit: int = 5,
        sparse_query: tuple[list[int], list[float]] | None = None,
    ) -> list[dict[str, Any]]:
        """Nearest chunks for one organization.

        `sparse_query` opts into hybrid search: dense and lexical are
        retrieved separately and fused. Optional rather than required
        because a store need not support lexical matching at all, and a
        caller that passes it to one that does not should get dense
        results rather than an error - the ranking is worse, the answer
        is not wrong.
        """

    @abstractmethod
    async def delete_document(self, organization_id: str, document_id: str) -> None:
        pass
