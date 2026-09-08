"""Sparse lexical vectors, to sit beside the dense ones.

A dense embedding matches meaning, which is what makes it good, and it is
also why it misses. "What is the SLA for a P1?" and "how fast do we have
to respond to a sev-one?" embed close together; so do "P1" and "P2", and
an identifier, a filename or an error code has no meaning to be close to
- it either appears in the text or it does not. Lexical matching is exact
where embeddings are approximate, so the two fail on different questions,
which is the whole reason for running both.

Term weights here are BM25's, with one deliberate omission: no document
length normalisation (b=0). That term exists to stop a long document
winning on term count alone, and the chunker already bounds every chunk
to the same token budget, so the correction it applies would be noise.
Inverse document frequency is not computed here at all - Qdrant holds the
corpus statistics and applies IDF at query time, which is also what keeps
this correct as documents are added, and what makes a stopword list
unnecessary: a term in every chunk gets an IDF near zero on its own.
"""

import re
import zlib
from collections import Counter


# BM25's term frequency saturation. The fourth occurrence of a word in a
# chunk says much less than the second, and k1 sets how fast that levels
# off; 1.2 is the usual default and nothing here argues for another value.
K1 = 1.2

_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric runs.

    No stemming. It would fold "policy" and "policies" together, which
    should help, but it also folds pairs that should stay apart, and this
    corpus is full of near-miss identifiers - P1 against P2, v2 against
    v3 - where lexical matching earns its place by being literal.
    """
    return _TOKEN.findall(text.lower())


def term_id(token: str) -> int:
    """A stable unsigned 32-bit id for a term.

    CRC32 rather than the built-in hash, which is salted per process:
    ids assigned at indexing time have to still mean the same term when a
    query is encoded in a different process, months later. Two terms can
    collide, but across a vocabulary of even 100k terms that is expected
    to happen about once in a 4-billion-value space, and the cost of a
    collision is one term occasionally matching a word it should not.
    """
    return zlib.crc32(token.encode("utf-8"))


def encode_passage(text: str) -> tuple[list[int], list[float]]:
    """Term ids and saturated term frequencies for something being indexed."""
    counts = Counter(term_id(token) for token in tokenize(text))

    return (
        list(counts.keys()),
        [count * (K1 + 1) / (count + K1) for count in counts.values()],
    )


def encode_query(text: str) -> tuple[list[int], list[float]]:
    """Term ids and weights for a question.

    Weight 1.0 per distinct term, as BM25 scores a query: a word asked
    twice is not twice the evidence, and questions are too short for
    frequency to mean anything anyway.
    """
    ids = list(dict.fromkeys(term_id(token) for token in tokenize(text)))

    return ids, [1.0] * len(ids)
