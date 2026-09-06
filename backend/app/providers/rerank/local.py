import logging

from app.providers.rerank.base import RerankProvider


logger = logging.getLogger(__name__)

# Trained on MS MARCO, which is real questions against real passages, and
# small enough to run on a CPU next to the application. The 6-layer size
# is the usual default; the 12-layer variant scores a little better and
# costs about twice as much per pair.
DEFAULT_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"


class LocalRerankProvider(RerankProvider):
    """Cross-encoder reranking on the CPU, in-process."""

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        # Lazily, for the same reason LocalEmbeddingProvider does it:
        # importing sentence-transformers pulls in torch, and a process
        # that never reranks should not pay for that at startup.
        from sentence_transformers import CrossEncoder

        self._model = CrossEncoder(model_name)

    def score(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []

        return [
            float(score)
            for score in self._model.predict([(query, p) for p in passages])
        ]
