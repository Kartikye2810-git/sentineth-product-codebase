import pytest

from app.services.extraction_service import extract_pages, extract_text_from_pdf
from tests.pdf_builder import build_pdf_pages


def _write(tmp_path, pages):
    path = tmp_path / "doc.pdf"
    path.write_bytes(build_pdf_pages(pages))
    return str(path)


def test_extract_pages_returns_one_entry_per_page(tmp_path):
    path = _write(tmp_path, [["page one"], ["page two"], ["page three"]])

    pages = extract_pages(path)

    assert len(pages) == 3
    assert "page two" in pages[1]


def test_blank_pages_are_kept_so_numbering_does_not_shift(tmp_path):
    path = _write(tmp_path, [["first"], [], ["third"]])

    pages = extract_pages(path)

    assert len(pages) == 3
    assert pages[1] == ""
    assert "third" in pages[2]


def test_extract_text_still_joins_pages(tmp_path):
    path = _write(tmp_path, [["alpha"], ["beta"]])

    text = extract_text_from_pdf(path)

    assert "alpha" in text
    assert "beta" in text


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        extract_pages(str(tmp_path / "nope.pdf"))
