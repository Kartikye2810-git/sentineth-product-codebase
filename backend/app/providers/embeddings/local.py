import logging

from app.providers.embeddings.base import EmbeddingProvider, InputType


logger = logging.getLogger(__name__)


class LocalEmbeddingProvider(EmbeddingProvider):
    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
    ) -> None:
        # Imported lazily: sentence-transformers pulls in torch, which is
        # slow and memory-hungry to import. Deferring it here keeps app
        # startup (and any code path not using local embeddings) cheap.
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name)

    @property
    def dimension(self) -> int:
        return 384

    @property
    def max_input_tokens(self) -> int:
        # Read off the model rather than hardcoded, so pointing this
        # provider at a different sentence-transformers model cannot leave
        # a stale 256 behind.
        return int(self._model.max_seq_length)

    @property
    def chunk_tokens(self) -> int:
        # The whole window: at 256 the model is still the constraint.
        return self.max_input_tokens

    def count_tokens(self, texts: list[str]) -> list[int]:
        if not texts:
            return []

        encoded = self._model.tokenizer(texts, add_special_tokens=False)

        return [len(ids) for ids in encoded["input_ids"]]

    async def embed(
        self,
        texts: list[str],
        *,
        input_type: InputType,
    ) -> list[list[float]]:
        # MiniLM is symmetric: it was trained with the same encoder for
        # both sides, so there is nothing to switch on. Accepted and
        # ignored so call sites can stay honest about what they hold.
        del input_type

        if not texts:
            return []

        self._warn_if_truncated(texts)

        embeddings = self._model.encode(
            texts,
            normalize_embeddings=True,
        )

        return embeddings.tolist()

    def _warn_if_truncated(self, texts: list[str]) -> None:
        """Say something when the model is about to stop reading.

        sentence-transformers truncates past max_seq_length silently: no
        exception, no warning, no log line. That silence is what let two
        thirds of every chunk sit in the payload while contributing
        nothing to the vector that decides retrieval. It logs rather than
        raises because a query is embedded on this path too, and an
        over-long question should still be answered - badly, but audibly.
        """
        budget = self.max_input_tokens
        over = [count for count in self.count_tokens(texts) if count > budget]

        if not over:
            return

        logger.warning(
            "embedding input exceeds the model window and will be truncated: "
            "%d of %d inputs over %d tokens (largest %d). Text past the cutoff "
            "does not affect the vector.",
            len(over),
            len(texts),
            budget,
            max(over),
        )
