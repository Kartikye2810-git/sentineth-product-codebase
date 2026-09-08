import os
from typing import Any

from openai import AsyncOpenAI

from app.providers.embeddings.base import EmbeddingProvider, InputType


# The hosted API Catalog endpoint. NVIDIA serves these models behind an
# OpenAI-compatible surface, so the existing openai client is reused rather
# than adding an SDK: the only non-standard fields are input_type and
# truncate, which ride along in extra_body.
DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODEL = "nvidia/nemotron-3-embed-1b"

# The endpoint's own maximum, measured rather than assumed: 512 comes back
# as HTTP 400 "input count 512 exceeds maximum allowed batch size; maximum
# of 256". Sitting exactly on the ceiling is worth roughly 3x - 57 chunks/s
# against 17 at a batch of 32 - because the cost of a request is dominated
# by the round trip, not by what is in it.
DEFAULT_BATCH_SIZE = 256


class NvidiaEmbeddingProvider(EmbeddingProvider):
    """nemotron-3-embed-1b on NVIDIA's hosted API Catalog.

    Asymmetric, unlike the local MiniLM this replaces: passages are embedded
    one way at index time and questions another way at query time. NVIDIA
    documents that mixing them up "will result in large drops in retrieval
    accuracy", and nothing in the response says it happened, which is why
    `input_type` is a required argument all the way down the stack.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        base_url: str | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        **kwargs: Any,
    ) -> None:
        self.api_key = api_key or os.getenv("NVIDIA_API_KEY")

        if not self.api_key:
            raise ValueError(
                "NVIDIA_API_KEY is required to use NvidiaEmbeddingProvider."
            )

        self.model = model
        self.base_url = base_url or os.getenv(
            "NVIDIA_BASE_URL",
            DEFAULT_BASE_URL,
        )
        self.batch_size = batch_size

        kwargs.setdefault("timeout", 10.0)
        kwargs.setdefault("max_retries", 2)
        self._client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            **kwargs,
        )

    @property
    def dimension(self) -> int:
        # Fixed at 2048 for this model. It is not Matryoshka-sliceable
        # through the API: `dimensions` must be omitted or 2048, and any
        # other value is rejected with HTTP 400 "dimensions must be one of
        # 2048". Slicing client-side is possible but requires re-normalising
        # the vector, so it is not done here.
        return 2048

    @property
    def max_input_tokens(self) -> int:
        # The model's real ceiling. Note this is 128x MiniLM's 256, so it is
        # no longer the binding constraint on chunk size - a 32k-token chunk
        # would embed fine and be useless to retrieve or cite. The chunker
        # takes an explicit budget for exactly this reason.
        return 32768

    @property
    def chunk_tokens(self) -> int:
        # Not 32768. Scored against MiniLM's 86-chunk corpus and against
        # its own 147-chunk one, this model retrieves the same either way
        # (recall@5 96.1% vs 95.1%, p=1.000), so the size is set by what
        # makes a citable passage rather than by what the model can
        # swallow. 256 keeps it in the range the harness has actually
        # measured.
        return 256

    def count_tokens(self, texts: list[str]) -> list[int]:
        """Approximate, deliberately, and biased to over-count.

        The exact answer needs the model's own tokenizer, which is not
        distributed with the hosted endpoint and cannot be asked for
        per-chunk without a network round trip inside the chunker's inner
        loop. Three characters per token over-estimates English prose,
        which averages nearer four, so chunks come out smaller than the
        budget rather than larger - the safe direction.
        """
        return [-(-len(text) // 3) for text in texts]

    async def embed(
        self,
        texts: list[str],
        *,
        input_type: InputType,
    ) -> list[list[float]]:
        if not texts:
            return []

        vectors: list[list[float]] = []

        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]

            response = await self._client.embeddings.create(
                model=self.model,
                input=batch,
                extra_body={
                    "input_type": input_type,
                    # NONE means the server rejects an over-long input
                    # instead of trimming it. Silent truncation is the
                    # exact defect item 1.2 existed to remove; a loud 4xx
                    # is worth more than a vector built from a prefix.
                    "truncate": "NONE",
                },
            )

            # The API is documented to preserve input order, but the
            # response carries an index and the cost of honouring it is one
            # sort - cheap insurance against a silent misalignment that
            # would attach every chunk's vector to its neighbour's text.
            vectors.extend(
                item.embedding
                for item in sorted(response.data, key=lambda item: item.index)
            )

        return vectors
