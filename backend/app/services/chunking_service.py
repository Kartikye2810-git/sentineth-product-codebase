from app.providers.embeddings.base import EmbeddingProvider


# Room for whatever the tokenizer wraps an input in - [CLS] and [SEP] for
# BERT-family models, two tokens. Four leaves headroom for a provider that
# adds more without making the chunks meaningfully smaller.
SPECIAL_TOKEN_MARGIN = 4

# Overlap as a fraction of the budget rather than a fixed count, so a
# provider with a 8,000-token window gets proportionate overlap instead of
# the 40 tokens that happened to suit a 256-token one.
OVERLAP_RATIO = 0.15

def chunk_text(
    text: str,
    provider: EmbeddingProvider,
) -> list[str]:
    """Split text into chunks the provider can embed without truncating.

    Sized in tokens, using the provider's own tokenizer, because tokens are
    the unit the limit is actually expressed in. Expressing the budget in
    characters is what produced the original defect: 4,000 characters was a
    reasonable-looking number that happened to be about four times what the
    model would read, and nothing in the system compared the two.

    Asking the provider rather than reading a constant is what makes the
    bug structurally impossible to reintroduce. Swap in a long-context
    model and the chunks grow to match it with no change here.
    """
    words = text.split()

    if not words:
        return []

    # The whole window, not a fraction of it. eval/harness.py was swept
    # across targets from 120 to 252 tokens and recall@5 moved between
    # 76.5% and 82.4% with no trend - neighbouring targets disagreed as
    # much as distant ones, so the differences are where the chunk
    # boundaries happened to fall, not a preference the data supports.
    # With nothing to tune towards, take the whole window: it needs no
    # constant to keep true, and it gives the answering model the most
    # context per citation. Retune only against a harness run.
    budget = provider.max_input_tokens - SPECIAL_TOKEN_MARGIN

    if budget < 1:
        raise ValueError(
            f"{type(provider).__name__} reports a window of "
            f"{provider.max_input_tokens} tokens, too small to chunk into."
        )

    overlap_budget = int(budget * OVERLAP_RATIO)

    # One tokenizer pass for the whole document. Counts are per word and
    # therefore additive, so packing a chunk is arithmetic rather than a
    # re-tokenization after every word.
    counts = provider.count_tokens(words)

    chunks: list[str] = []
    start = 0

    while start < len(words):
        end = start
        used = 0

        while end < len(words) and used + counts[end] <= budget:
            used += counts[end]
            end += 1

        if end == start:
            # A single word longer than the whole budget - a URL, a base64
            # blob, or extraction gluing a page together. Emit it alone and
            # move on; the provider logs the truncation.
            end = start + 1

        chunks.append(" ".join(words[start:end]))

        if end >= len(words):
            break

        # Step back over trailing words until the overlap budget is spent.
        overlap_used = 0
        next_start = end

        while next_start > start + 1 and overlap_used + counts[next_start - 1] <= overlap_budget:
            next_start -= 1
            overlap_used += counts[next_start]

        start = next_start

    return chunks
