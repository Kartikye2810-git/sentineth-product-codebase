from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Length of the vectors this provider produces.

        The vector store must be configured with the same value.
        Changing provider or model without recreating the collection
        and re-embedding is not supported. See AGENTS.md section 14.
        """

    @property
    @abstractmethod
    def max_input_tokens(self) -> int:
        """Longest input this provider embeds without truncating it.

        The chunker asks for this rather than carrying a constant of its
        own. A constant is how the two drifted apart in the first place:
        the chunker cut at 4,000 characters, the model stopped reading at
        256 tokens, and nothing in between compared the two numbers.
        """

    @abstractmethod
    def count_tokens(self, texts: list[str]) -> list[int]:
        """Token counts as this provider's own tokenizer produces them.

        Counts exclude any special tokens the provider adds around an
        input, so that the counts of separately-counted pieces sum to the
        count of those pieces joined - which is what lets the chunker pack
        a chunk without re-tokenizing after every addition.
        """

    @abstractmethod
    async def embed(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        pass
