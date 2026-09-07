from abc import ABC, abstractmethod
from typing import Any


class VectorStore(ABC):

    @abstractmethod
    async def upsert(
        self,
        organization_id: str,
        vectors: list[list[float]],
        payloads: list[dict[str, Any]],
    ) -> None:
        pass

    @abstractmethod
    async def search(
        self,
        organization_id: str,
        query_vector: list[float],
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        pass

    @abstractmethod
    async def delete_document(self, organization_id: str, document_id: str) -> None:
        pass
