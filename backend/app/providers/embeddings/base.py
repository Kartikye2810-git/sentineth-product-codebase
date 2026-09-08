from abc import ABC, abstractmethod
from typing import Literal


# What the text being embedded is for. Symmetric models ignore it; an
# asymmetric one embeds a question and the passage answering it through
# different code paths and needs telling which it has been handed.
InputType = Literal["query", "passage"]


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

    @property
    @abstractmethod
    def chunk_tokens(self) -> int:
        """How large a chunk should be when indexing with this provider.

        Separate from `max_input_tokens`, which is the model's ceiling.
        The two were the same thing while the ceiling was 256 and it was
        the binding constraint. At a 32k ceiling it is not: a 32k-token
        chunk embeds without complaint and is useless to retrieve or to
        cite, so something other than the model has to set the size.

        Measured rather than guessed. eval/harness.py finds retrieval
        insensitive to chunk size across this range - MiniLM moved
        76.5-82.4% recall@5 with no trend between 120 and 252 tokens, and
        nemotron-3-embed-1b scored the same on 86 chunks as on 147
        (p=1.000). So the number is chosen for citation quality, which
        wants the smaller end, not for a retrieval effect that is not
        there.
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
        *,
        input_type: InputType,
    ) -> list[list[float]]:
        """Embed texts, told whether they are queries or passages.

        Keyword-only and required, with no default, because the failure
        mode is silent. NVIDIA's retriever models document that embedding
        a query as a passage "will result in large drops in retrieval
        accuracy" - no exception, no warning, just worse answers. A
        default would make the wrong value the easy one to reach for,
        which is how the chunker and the model window drifted apart.
        """
