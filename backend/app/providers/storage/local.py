import asyncio
import hashlib
import os
import re
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from app.errors import ExtractionFailed, InputTooLarge
from app.providers.storage.base import StorageProvider


class LocalStorageProvider(StorageProvider):
    def __init__(self, base_dir: str | Path | None = None):
        if base_dir is None:
            base_dir = Path(__file__).resolve().parents[3] / "storage" / "documents"
        self.base_dir = Path(base_dir).resolve()

    def _sanitize_filename(self, filename: str) -> str:
        if not filename or not filename.strip():
            return "uploaded_file.bin"

        cleaned = filename.replace("\\", "/")
        safe_name = PurePosixPath(cleaned).name
        safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", safe_name)
        safe_name = safe_name.strip().strip(".")

        if not safe_name or safe_name in {".", ".."}:
            return "uploaded_file.bin"

        return safe_name

    async def save(
        self,
        organization_id: str,
        document_id: str,
        filename: str,
        content: bytes,
    ) -> str:
        org_root = (self.base_dir / str(organization_id)).resolve(strict=False)

        # One directory per document id. Without it two uploads sharing a
        # filename resolve to the same path and the second silently
        # overwrites the first.
        target_path = (
            org_root
            / self._sanitize_filename(str(document_id))
            / self._sanitize_filename(filename)
        ).resolve(strict=False)

        if org_root not in target_path.parents:
            raise ValueError("Filename resolves outside the organization storage directory.")

        target_path.parent.mkdir(parents=True, exist_ok=True)

        with open(target_path, "wb") as handle:
            handle.write(content)

        return str(target_path)

    async def delete(
        self,
        path: str,
    ) -> None:
        if not path:
            return

        await asyncio.to_thread(Path(path).unlink, missing_ok=True)

    async def exists(
        self,
        path: str,
    ) -> bool:
        if not path:
            return False

        return Path(path).exists()

    def save_stream(self, organization_id: str, document_id: str, filename: str,
                    stream: BinaryIO, max_bytes: int) -> tuple[str, int, str]:
        target = (self.base_dir / organization_id / document_id /
                  self._sanitize_filename(filename)).resolve()
        if self.base_dir not in target.parents:
            raise ValueError("Invalid storage path")
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.with_name(target.name + ".partial")
        size = 0
        digest = hashlib.sha256()
        try:
            with staging.open("xb") as output:
                while block := stream.read(64 * 1024):
                    size += len(block)
                    if size > max_bytes:
                        raise InputTooLarge("Upload exceeds the file size limit.")
                    digest.update(block)
                    output.write(block)
                if not size:
                    raise ExtractionFailed("Uploaded document is empty.")
                output.flush()
                os.fsync(output.fileno())
            staging.replace(target)
        except BaseException:
            staging.unlink(missing_ok=True)
            raise
        return str(target), size, digest.hexdigest()
