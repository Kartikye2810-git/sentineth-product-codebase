from abc import ABC, abstractmethod
from typing import BinaryIO


class StorageProvider(ABC):

    @abstractmethod
    async def save(
        self,
        organization_id: str,
        document_id: str,
        filename: str,
        content: bytes,
    ) -> str:
        pass

    @abstractmethod
    async def delete(
        self,
        path: str,
    ) -> None:
        pass

    @abstractmethod
    async def exists(
        self,
        path: str,
    ) -> bool:
        pass

    @abstractmethod
    def save_stream(self, organization_id: str, document_id: str, filename: str,
                    stream: BinaryIO, max_bytes: int) -> tuple[str, int, str]:
        """Bounded streaming write; returns path, size, SHA-256. Runs in a worker thread."""
