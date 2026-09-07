"""The provider interfaces, exercised where nothing else exercises them.

The running application only ever constructs the local embedding provider
and the OpenRouter LLM provider. The OpenAI implementations are kept as
evidence that these interfaces are implementable by a second vendor, and
that is only evidence if something checks it. Constructing an
`AsyncOpenAI` client performs no I/O, so a dummy key is enough.
"""

import pytest

from app.providers.embeddings.base import EmbeddingProvider
from app.providers.embeddings.openai import OpenAIEmbeddingProvider
from app.providers.llm.base import LLMProvider
from app.providers.llm.openai import OpenAIProvider


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
