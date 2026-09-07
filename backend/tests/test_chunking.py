import pytest

from app.services.chunking_service import chunk_text


def test_empty_text_produces_no_chunks():
    assert chunk_text("") == []
    assert chunk_text("   \n  ") == []


def test_short_text_is_a_single_chunk():
    assert chunk_text("hello world") == ["hello world"]


def test_long_text_is_split_with_overlap():
    text = "a" * 9000

    chunks = chunk_text(text, chunk_size=4000, overlap=500)

    assert len(chunks) == 3
    assert all(len(chunk) <= 4000 for chunk in chunks)

    # Reassembling with the overlap removed must recover the original.
    rebuilt = chunks[0] + "".join(chunk[500:] for chunk in chunks[1:])
    assert rebuilt == text


def test_overlap_must_be_smaller_than_chunk_size():
    with pytest.raises(ValueError):
        chunk_text("some text", chunk_size=100, overlap=100)
