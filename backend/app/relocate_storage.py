"""Rewrite document paths for a verified storage mount while writers are stopped."""

import argparse
from pathlib import Path

from sqlalchemy import select

from app.audit import record
from app.backup import digest
from app.db.database import SessionLocal
from app.db.models import Document, IngestionJob
from app.settings import get_settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("new_root", type=Path)
    parser.add_argument("--quiesced", action="store_true")
    args = parser.parse_args()
    if not args.quiesced or not args.new_root.is_absolute():
        parser.error("Stop all writers and supply --quiesced and an absolute destination")
    current = get_settings().storage_dir.resolve()
    with SessionLocal() as db:
        if db.scalar(select(IngestionJob.id).where(IngestionJob.status == "PROCESSING").limit(1)):
            parser.error("A worker is still processing")
        for document in db.scalars(select(Document).with_for_update()):
            path = Path(document.storage_path).resolve()
            if not path.is_relative_to(current) or not path.is_file():
                parser.error("A source file is missing or outside the configured root")
            if document.content_hash and digest(path) != document.content_hash:
                parser.error("A source checksum does not match its document")
            document.storage_path = str(args.new_root / path.relative_to(current))
            record(db, "operator.storage_relocated", document.organization_id, document.id)
        db.commit()
    print("Paths updated. Verify the new mount before starting API and workers.")


if __name__ == "__main__":
    main()
