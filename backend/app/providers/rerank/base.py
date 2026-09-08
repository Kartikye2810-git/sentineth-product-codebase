from abc import ABC, abstractmethod


class RerankProvider(ABC):
    """Scores a question against candidate passages, jointly.

    An embedding model reads the question and the passage separately and
    never compares them - that is what makes the index possible, since a
    passage is embedded once and reused for every question ever asked. It
    is also what it costs: the score is a distance between two summaries
    written in ignorance of each other.

    A cross-encoder reads the pair together and can attend across it, so
    it scores far better and cannot be indexed at all. That makes it a
    second pass over a shortlist, not a retriever: it can only reorder
    what the first stage returned, and a chunk missing from that
    shortlist is one it will never see.
    """

    @abstractmethod
    def score(self, query: str, passages: list[str]) -> list[float]:
        """Relevance of each passage to the query, higher being better.

        Comparable within one call and not across calls: these are raw
        model outputs, on no particular scale.
        """
