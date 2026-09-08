import re
from dataclasses import dataclass

from app.providers.embeddings.base import EmbeddingProvider


# Room for whatever the tokenizer wraps an input in - [CLS] and [SEP] for
# BERT-family models, two tokens. Four leaves headroom for a provider that
# adds more without making the chunks meaningfully smaller.
SPECIAL_TOKEN_MARGIN = 4

# Overlap as a fraction of the budget rather than a fixed count, so a
# provider with an 8,000-token window gets proportionate overlap instead of
# the 40 tokens that happened to suit a 256-token one.
OVERLAP_RATIO = 0.15

_PARAGRAPH = re.compile(r"\n\s*\n")

# Sentence end followed by whitespace. Deliberately not a full sentence
# tokenizer: this is a fallback for paragraphs too long to keep whole, and
# an occasional bad split after "Inc." costs less than a dependency.
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Chunk:
    """A piece of a document, and where in the document it came from."""

    content: str
    page_number: int


@dataclass(frozen=True)
class _Unit:
    """The smallest piece the chunker will not split further if it can help it."""

    text: str
    page_number: int
    paragraph: int


def _split_units(pages: list[str], budget: int, provider: EmbeddingProvider) -> list[_Unit]:
    """Break pages into units no larger than the budget, coarsest first.

    Paragraphs are kept whole where they fit, because a paragraph is the
    author's own statement about which sentences belong together and it is
    free information. Only paragraphs that do not fit are split into
    sentences, and only sentences that do not fit are split into words.
    """
    units: list[_Unit] = []
    paragraph_number = 0

    for page_number, page in enumerate(pages, start=1):
        if not page.strip():
            continue

        for paragraph in _PARAGRAPH.split(page):
            if not paragraph.strip():
                continue

            paragraph_number += 1
            units.append(_Unit(" ".join(paragraph.split()), page_number, paragraph_number))

    # One tokenizer pass per level, rather than one per piece.
    for splitter in (_SENTENCE.split, str.split):
        counts = provider.count_tokens([unit.text for unit in units])

        if all(count <= budget for count in counts):
            break

        divided: list[_Unit] = []

        for unit, count in zip(units, counts, strict=True):
            if count <= budget:
                divided.append(unit)
                continue

            divided.extend(
                _Unit(piece, unit.page_number, unit.paragraph)
                for piece in splitter(unit.text)
                if piece.strip()
            )

        units = divided

    return units


def chunk_pages(
    pages: list[str],
    provider: EmbeddingProvider,
    *,
    max_tokens: int | None = None,
) -> list[Chunk]:
    """Split a document into chunks the provider can embed without truncating.

    Sized in tokens, using the provider's own tokenizer, because tokens are
    the unit the limit is actually expressed in. Expressing the budget in
    characters is what produced the original defect: 4,000 characters was a
    reasonable-looking number that happened to be about four times what the
    model would read, and nothing in the system compared the two. Asking the
    provider rather than reading a constant is what makes that bug
    structurally impossible to reintroduce.

    Split on the document's own boundaries rather than at a character
    offset. A chunk that begins mid-sentence starts with a fragment that
    means nothing on its own, and it is embedded, retrieved and shown to a
    user as though it did.
    """
    # The provider's own budget by default, not a constant here, and not
    # its window either. Those were the same number while the window was
    # MiniLM's 256 and the model was the binding constraint; they are not
    # at 32k, where a legal chunk is a whole document and a citation
    # points a reader at nothing in particular.
    #
    # Within the range the harness has measured the size barely matters:
    # sweeping 120 to 252 tokens moved recall@5 between 76.5% and 82.4%
    # with no trend, neighbouring targets disagreeing as much as distant
    # ones. So the number is not tuned for retrieval, because there is no
    # retrieval signal to tune towards - it is picked for citation size,
    # and it lives on the provider so it stays next to the window it has
    # to respect.
    #
    # `max_tokens` overrides it, for the harness sweeping sizes on
    # purpose. Clamped to the window either way: no caller gets to ask for
    # more than the model will read, which is the defect this whole
    # function exists to make impossible.
    ceiling = min(max_tokens or provider.chunk_tokens, provider.max_input_tokens)

    budget = ceiling - SPECIAL_TOKEN_MARGIN

    if budget < 1:
        raise ValueError(
            f"{type(provider).__name__} reports a window of "
            f"{provider.max_input_tokens} tokens, too small to chunk into."
        )

    units = _split_units(pages, budget, provider)

    if not units:
        return []

    counts = provider.count_tokens([unit.text for unit in units])
    overlap_budget = int(budget * OVERLAP_RATIO)

    chunks: list[Chunk] = []
    start = 0

    while start < len(units):
        end = start
        used = 0

        while end < len(units) and used + counts[end] <= budget:
            used += counts[end]
            end += 1

        if end == start:
            # One unit larger than the whole budget survived every level of
            # splitting - a URL, a base64 blob, or extraction gluing a page
            # into a single token. Emit it alone; the provider logs the
            # truncation rather than the chunker looping on it forever.
            end = start + 1

        chunks.append(_join(units[start:end]))

        if end >= len(units):
            break

        # Step back over trailing units until the overlap budget is spent,
        # so a passage split across a boundary is whole in one of the two.
        overlap_used = 0
        next_start = end

        while next_start > start + 1 and overlap_used + counts[next_start - 1] <= overlap_budget:
            next_start -= 1
            overlap_used += counts[next_start]

        start = next_start

    return chunks


def _join(units: list[_Unit]) -> Chunk:
    """Reassemble units, keeping the paragraph breaks between them."""
    parts: list[str] = []

    for index, unit in enumerate(units):
        if index and unit.paragraph != units[index - 1].paragraph:
            parts.append("\n\n")
        elif index:
            parts.append(" ")

        parts.append(unit.text)

    # The page a chunk starts on. A chunk that spans a page break is cited
    # where a reader would start looking for it.
    return Chunk("".join(parts), units[0].page_number)


def chunk_text(
    text: str,
    provider: EmbeddingProvider,
    *,
    max_tokens: int | None = None,
) -> list[str]:
    """Chunk text with no page structure. Content only, no page numbers."""
    return [
        chunk.content
        for chunk in chunk_pages([text], provider, max_tokens=max_tokens)
    ]
