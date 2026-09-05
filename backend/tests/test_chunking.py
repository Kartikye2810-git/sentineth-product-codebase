import pytest

from app.services.chunking_service import SPECIAL_TOKEN_MARGIN, chunk_text
from tests.fakes import FakeEmbeddingProvider


@pytest.fixture
def provider() -> FakeEmbeddingProvider:
    return FakeEmbeddingProvider(max_input_tokens=20)


def test_empty_text_produces_no_chunks(provider):
    assert chunk_text("", provider) == []
    assert chunk_text("   \n  ", provider) == []


def test_short_text_is_a_single_chunk(provider):
    assert chunk_text("hello world", provider) == ["hello world"]


def test_no_chunk_exceeds_the_provider_window(provider):
    text = " ".join(f"word{n}" for n in range(200))

    chunks = chunk_text(text, provider)

    assert len(chunks) > 1
    assert all(
        count <= provider.max_input_tokens
        for count in provider.count_tokens(chunks)
    )


def test_chunks_overlap_and_cover_the_whole_text(provider):
    words = [f"word{n}" for n in range(200)]

    chunks = chunk_text(" ".join(words), provider)

    # Every word survives, in order, across the chunk boundaries.
    seen: list[str] = []
    for chunk in chunks:
        for word in chunk.split():
            if word not in seen:
                seen.append(word)

    assert seen == words

    # Consecutive chunks share a tail, so a sentence spanning a boundary
    # is embedded whole in at least one of them.
    assert chunks[0].split()[-1] in chunks[1].split()


def test_chunk_size_follows_the_provider_not_a_constant():
    """The point of the interface: a bigger window means bigger chunks.

    This is the regression test for the original defect. The chunker used
    to carry its own character constant, so a model with a different window
    changed nothing about how text was cut for it.
    """
    text = " ".join(f"word{n}" for n in range(400))

    small = chunk_text(text, FakeEmbeddingProvider(max_input_tokens=20))
    large = chunk_text(text, FakeEmbeddingProvider(max_input_tokens=100))

    assert len(large) < len(small)


class CharTokenProvider(FakeEmbeddingProvider):
    """A provider whose tokens are characters.

    FakeEmbeddingProvider counts words, so no single word can ever exceed
    its window and the indivisible-unit branch below is unreachable with
    it. Counting characters makes one long word bigger than the budget,
    which is what extraction produces when it glues a page together.
    """

    def count_tokens(self, texts: list[str]) -> list[int]:
        return [len(text) for text in texts]


def test_a_word_larger_than_the_window_is_emitted_rather_than_looped_on():
    text = f"short {'x' * 5000} short"

    chunks = chunk_text(text, CharTokenProvider(max_input_tokens=20))

    assert chunks
    assert "".join(chunks).count("x") == 5000


def test_window_too_small_to_chunk_is_rejected():
    with pytest.raises(ValueError):
        chunk_text("some text", FakeEmbeddingProvider(max_input_tokens=SPECIAL_TOKEN_MARGIN))
