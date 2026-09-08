import os
from typing import Any

from openai import AsyncOpenAI

from app.providers.embeddings.base import EmbeddingProvider, InputType


class OpenAIEmbeddingProvider(EmbeddingProvider):
    def __init__(
        self,
        api_key: str | None = None,
        model: str = "text-embedding-3-small",
        base_url: str | None = None,
        organization: str | None = None,
        **kwargs: Any,
    ) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY is required to use OpenAIEmbeddingProvider.")

        self.model = model
        self.base_url = base_url
        self.organization = organization
        kwargs.setdefault("timeout", 10.0)
        kwargs.setdefault("max_retries", 2)
        self._client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            organization=self.organization,
            **kwargs,
        )

    @property
    def dimension(self) -> int:
        return 1536

    @property
    def max_input_tokens(self) -> int:
        # text-embedding-3-small accepts 8,191 tokens.
        return 8191

    @property
    def chunk_tokens(self) -> int:
        # Same reasoning as the NVIDIA provider: 8,191 is the ceiling, not
        # a sensible chunk. Unmeasured for this model specifically - this
        # provider is not the default and has never been scored by the
        # harness - so it takes the range the harness has measured on the
        # other two rather than a number of its own.
        return 256

    def count_tokens(self, texts: list[str]) -> list[int]:
        """Approximate, deliberately, and biased to over-count.

        The exact answer needs tiktoken, which this project does not
        depend on. The approximation is safe here for two reasons: the
        chunker only uses counts to stay *under* a budget, and three
        characters per token over-estimates English prose, which averages
        closer to four - so chunks come out smaller than they had to be
        rather than larger than the model will read. If this provider ever
        becomes the default, add tiktoken and make this exact.
        """
        return [-(-len(text) // 3) for text in texts]

    async def embed(
        self,
        texts: list[str],
        *,
        input_type: InputType,
    ) -> list[list[float]]:
        # text-embedding-3-small is symmetric; nothing to switch on.
        del input_type

        if not texts:
            return []

        response = await self._client.embeddings.create(
            model=self.model,
            input=texts,
        )

        return [item.embedding for item in response.data]
