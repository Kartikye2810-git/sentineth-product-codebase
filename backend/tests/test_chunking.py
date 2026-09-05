import inspect

import pytest

from app.services.chunking_service import chunk_text


def test_empty_text_produces_no_chunks():
    assert chunk_text("") == []
    assert chunk_text("   \n  ") == []


def test_short_text_is_a_single_chunk():
    assert chunk_text("hello world") == ["hello world"]


def test_long_text_is_split_with_overlap():
    text = "a" * 9000

    chunks = chunk_text(text, chunk_size=1000, overlap=150)

    assert len(chunks) == 11
    assert all(len(chunk) <= 1000 for chunk in chunks)

    # Reassembling with the overlap removed must recover the original.
    rebuilt = chunks[0] + "".join(chunk[150:] for chunk in chunks[1:])
    assert rebuilt == text


def test_default_chunk_size_fits_the_embedding_window():
    """The default must be small enough for the model that embeds it.

    This is the test that was missing. The old one passed chunk_size=4000
    explicitly, so it asserted that the chunker honours an argument - which
    it did - while the default sat at a value the embedding model truncated.
    A test that never touches the default cannot notice the default is wrong.
    """
    defaults = inspect.signature(chunk_text).parameters

    assert defaults["chunk_size"].default <= 1000
    assert defaults["overlap"].default < defaults["chunk_size"].default


def test_overlap_must_be_smaller_than_chunk_size():
    with pytest.raises(ValueError):
        chunk_text("some text", chunk_size=100, overlap=100)
