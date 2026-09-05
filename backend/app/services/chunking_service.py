def chunk_text(
    text: str,
    chunk_size: int = 1000,
    overlap: int = 150,
) -> list[str]:
    """Split text into overlapping character windows.

    1,000 characters is not a round number chosen for tidiness: it is the
    largest size that reliably fits inside the 256-token window of the
    embedding model. At 4,000 the model silently truncated every chunk and
    roughly two thirds of each one contributed nothing to whether it could
    be retrieved.

    Characters are the wrong unit for this - the real limit is tokens, and
    the tokenizer belongs to the provider. See `chunk_text_by_tokens`.
    """

    if not text.strip():
        return []

    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")

    chunks = []

    start = 0
    text_length = len(text)

    while start < text_length:
        end = min(start + chunk_size, text_length)

        chunk = text[start:end].strip()

        if chunk:
            chunks.append(chunk)

        if end >= text_length:
            break

        start = end - overlap

    return chunks