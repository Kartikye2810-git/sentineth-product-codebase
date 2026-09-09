"""The provider interfaces, exercised where nothing else exercises them.

The running application only ever constructs the local embedding provider
and the OpenRouter LLM provider. The OpenAI implementations are kept as
evidence that these interfaces are implementable by a second vendor, and
that is only evidence if something checks it. Constructing an
`AsyncOpenAI` client performs no I/O, so a dummy key is enough.
"""

import pytest

from app.dependencies import (
    COLLECTIONS,
    EMBEDDING_PROVIDERS,
    active_collection_name,
    active_embedding_provider,
)
from app.providers.embeddings.base import EmbeddingProvider
from app.providers.embeddings.openai import OpenAIEmbeddingProvider
from app.providers.llm.base import LLMProvider
from app.providers.llm.openai import OpenAIProvider
from app.settings import get_settings


def test_the_openai_providers_satisfy_the_interfaces():
    embedding_provider = OpenAIEmbeddingProvider(api_key="dummy-key")
    llm_provider = OpenAIProvider(api_key="dummy-key")

    assert isinstance(embedding_provider, EmbeddingProvider)
    assert isinstance(llm_provider, LLMProvider)
    assert embedding_provider.dimension == 1536


@pytest.mark.parametrize(
    "provider_class", [OpenAIEmbeddingProvider, OpenAIProvider]
)
def test_an_openai_provider_refuses_to_construct_without_a_key(
    provider_class, monkeypatch
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        provider_class()


def test_the_default_embedding_configuration_is_nemotron(monkeypatch):
    """Provider and collection must default together or not at all.

    They were defaulted in separate expressions, and one of them did not
    case-fold, so `EMBEDDING_PROVIDER=NVIDIA` sent the application to the
    nemotron collection and `scripts/reindex.py` to the MiniLM one - 2048
    dimensional vectors aimed at a 384 dimensional collection.
    """
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    monkeypatch.delenv("QDRANT_COLLECTION", raising=False)

    assert active_embedding_provider() == "nvidia"
    assert active_collection_name() == "sentineth_documents_nemotron"

    get_settings.cache_clear()
    monkeypatch.setenv("EMBEDDING_PROVIDER", "  NVIDIA  ")
    assert active_embedding_provider() == "nvidia"
    assert active_collection_name() == "sentineth_documents_nemotron"

    get_settings.cache_clear()
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
    assert active_collection_name() == "sentineth_documents"

    # An explicit collection wins, because that is how a reindex is cut over.
    get_settings.cache_clear()
    monkeypatch.setenv("QDRANT_COLLECTION", "rebuild_2026_09")
    assert active_collection_name() == "rebuild_2026_09"


def test_every_selectable_embedding_provider_has_a_collection():
    assert set(COLLECTIONS) == set(EMBEDDING_PROVIDERS)
