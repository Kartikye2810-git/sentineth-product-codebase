from pathlib import Path

from pypdf import PdfReader


def extract_pages(file_path: str) -> list[str]:
    """Text of every page, in order, empty pages included as empty strings.

    Positional fidelity is the point: a citation that says "page 14" is only
    worth more than "chunk 37" if the 14 is right, and dropping blank pages
    here would shift every page number after the first blank one.
    """
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    reader = PdfReader(str(path))

    return [(page.extract_text() or "").strip() for page in reader.pages]


def extract_text_from_pdf(file_path: str) -> str:
    pages = [text for text in extract_pages(file_path) if text]

    text = "\n\n".join(pages)

    if not text.strip():
        raise ValueError(
            "No extractable text found in PDF. "
            "The document may be scanned or image-based."
        )

    return text
