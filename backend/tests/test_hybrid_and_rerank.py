"""Both of these are off in production, and both are measured harmful there.

eval/harness.py, 102 questions against nemotron-3-embed-1b: fusing a
lexical vector in took recall@1 from 77.5% to 64.7% and won nothing
(0 questions improved, 13 broken), and reranking the top 30 with
ms-marco-MiniLM-L-6-v2 took it to 66.7%. The tests below cover the
mechanisms anyway - if either is ever switched on, on a corpus where the
answer comes out differently, it should be switched on to working code.
"""

import asyncio

import pytest

from app.providers.rerank.base import RerankProvider
from app.services.lexical_service import encode_passage, encode_query, term_id
from app.services.retrieval_service import RERANK_DEPTH, retrieve
from tests.fakes import FakeEmbeddingProvider, FakeVectorStore


def run(coroutine):
    # The suite has no async plugin and needs one for four tests here.
    return asyncio.run(coroutine)


class ReverseReranker(RerankProvider):
    """Scores in reverse of the order it was handed.

    Nothing about it is realistic; the point is that its output cannot be
    confused with the retriever's, so a test can tell which of the two
    decided the order it is looking at.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def score(self, query: str, passages: list[str]) -> list[float]:
        self.calls.append((query, len(passages)))

        return [float(index) for index in range(len(passages))]


def indexed(store: FakeVectorStore, texts: list[str], vectors, sparse=None) -> None:
    run(
        store.upsert(
            "org",
            vectors,
            [{"chunk_id": str(i), "content": t} for i, t in enumerate(texts)],
            sparse_vectors=sparse,
        )
    )


def test_term_ids_survive_a_restart():
    # Ids are written into the index at ingestion and looked up by a query
    # encoded in another process, so a per-process salt - which is what the
    # built-in hash has - would break lexical search after a redeploy, and
    # only for documents indexed before it.
    assert term_id("policy") == 4034725142


def test_repeated_terms_saturate():
    ids, values = encode_passage("audit audit audit audit review")
    weights = dict(zip(ids, values, strict=True))

    # Four occurrences are worth well under four times one.
    assert weights[term_id("audit")] < 2 * weights[term_id("review")]


def test_a_question_weights_each_term_once():
    ids, values = encode_query("refund refund policy")

    assert len(ids) == 2
    assert values == [1.0, 1.0]


def test_lexical_vectors_must_line_up_with_dense_ones():
    store = FakeVectorStore()

    with pytest.raises(ValueError, match="matching lengths"):
        run(store.upsert("org", [[1.0]], [{"chunk_id": "a"}], sparse_vectors=[]))


def test_hybrid_promotes_a_chunk_the_dense_vector_ranked_last():
    """The case hybrid exists for: a term the embedding has no meaning for.

    Note how much has to be true for it to work. Reciprocal rank fusion
    reads positions and not scores, so the lexical side gets no credit for
    matching emphatically - only for matching first. Here it wins because
    the other passages contain none of the query's terms at all and are
    dropped from the lexical ranking entirely. Where both retrievers
    return the same candidates in a similar order, which is the normal
    case, fusion mostly reshuffles them - which is what the eval measured
    when it took recall@1 down by 12.7 points.
    """
    store = FakeVectorStore()
    passages = [
        "the cafeteria menu rotates on a fortnightly basis",
        "visitor parking permits are issued at reception",
        "SEV2 tickets escalate to the on-call engineer within a day",
    ]

    # Identical dense vectors, so dense ranking is insertion order and the
    # answer is last - exactly the position lexical matching has to rescue
    # it from.
    indexed(
        store,
        passages,
        [[1.0, 0.0]] * 3,
        sparse=[encode_passage(text) for text in passages],
    )

    hits = run(
        store.search("org", [1.0, 0.0], limit=1, sparse_query=encode_query("SEV2"))
    )

    assert hits[0]["payload"]["content"].startswith("SEV2 tickets")

    # And without the lexical side it stays where dense put it, last.
    dense_only = run(store.search("org", [1.0, 0.0], limit=1))

    assert dense_only[0]["payload"]["content"].startswith("the cafeteria")


def test_reranker_decides_the_order_and_the_score():
    store = FakeVectorStore()
    provider = FakeEmbeddingProvider()
    texts = [f"chunk number {n} about expenses" for n in range(5)]

    indexed(store, texts, run(provider.embed(texts, input_type="passage")))

    plain = run(retrieve("org", "expenses", provider, store, limit=3))
    reranked = run(
        retrieve(
            "org",
            "expenses",
            provider,
            store,
            limit=3,
            rerank_provider=ReverseReranker(),
        )
    )

    assert [hit["chunk_id"] for hit in reranked] != [hit["chunk_id"] for hit in plain]

    # The score reported is the one that produced the order shown.
    assert [hit["score"] for hit in reranked] == sorted(
        (hit["score"] for hit in reranked), reverse=True
    )


def test_reranking_looks_wider_than_it_returns():
    """Otherwise it is a sort, not a second chance.

    A shortlist the same size as the answer lets the reranker only reorder
    chunks the first stage already ranked highly, which is the one thing
    it is not needed for.
    """
    store = FakeVectorStore()
    provider = FakeEmbeddingProvider()
    texts = [f"chunk {n} expenses policy" for n in range(RERANK_DEPTH + 10)]

    indexed(store, texts, run(provider.embed(texts, input_type="passage")))

    reranker = ReverseReranker()
    run(
        retrieve(
            "org", "expenses", provider, store, limit=3, rerank_provider=reranker
        )
    )

    assert reranker.calls == [("expenses", RERANK_DEPTH)]


def test_without_a_reranker_nothing_extra_is_fetched():
    store = FakeVectorStore()
    provider = FakeEmbeddingProvider()
    texts = [f"chunk {n} expenses policy" for n in range(RERANK_DEPTH + 10)]

    indexed(store, texts, run(provider.embed(texts, input_type="passage")))

    assert len(run(retrieve("org", "expenses", provider, store, limit=3))) == 3
