from abc import ABC, abstractmethod


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
