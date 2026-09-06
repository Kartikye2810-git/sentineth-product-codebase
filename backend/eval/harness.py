"""Retrieval evaluation harness.

Answers one question: for a hand-written question whose answer is known to
live in a particular passage of a particular document, does the retrieval
stack actually return that passage, and at what rank?

What it measures is the real path - `extract_pages`, `chunk_pages`,
the configured `EmbeddingProvider`, and the configured `VectorStore` - so a
change to any of them shows up here without the harness being edited. It
deliberately stops before the LLM: the answer step cannot be scored without
a judge, and a judge would put a model between the change and the number.

Ground truth is a document plus a verbatim `answer_span`, never a chunk id.
Chunk ids change the moment chunking changes, which is item 1.2 and item 1.3
of this very phase; a span survives re-chunking and a model swap, so the same
question set stays comparable across the whole phase.

    python eval/harness.py                      # score the current config
    python eval/harness.py --report out.json    # and write a machine report
"""

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


EVAL_DIR = Path(__file__).resolve().parent
BACKEND_DIR = EVAL_DIR.parent

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Hosted providers need their keys, and the harness is run from a shell that
# has not sourced anything. Same file the application reads.
load_dotenv(BACKEND_DIR.parent / ".env")

from app.dependencies import EMBEDDING_PROVIDERS  # noqa: E402
from app.providers.embeddings.base import EmbeddingProvider  # noqa: E402
from app.providers.rerank.base import RerankProvider  # noqa: E402
from app.providers.rerank.local import LocalRerankProvider  # noqa: E402
from app.providers.vector.base import VectorStore  # noqa: E402
from app.providers.vector.qdrant import QdrantVectorStore  # noqa: E402
from app.services.chunking_service import (  # noqa: E402
    OVERLAP_RATIO,
    SPECIAL_TOKEN_MARGIN,
    chunk_pages,
)
from app.services.extraction_service import (  # noqa: E402
    extract_pages,
    extract_text_from_pdf,
)
from app.services.lexical_service import encode_passage  # noqa: E402
from app.services.retrieval_service import RERANK_DEPTH, retrieve  # noqa: E402


DEFAULT_QUESTIONS = EVAL_DIR / "questions.jsonl"
DEFAULT_CORPUS = EVAL_DIR / "corpus_pdf"
DEFAULT_COLLECTION = "sentineth_eval"

# Retrieve deeper than the largest reported k so MRR has somewhere to fall.
SEARCH_DEPTH = 10
REPORTED_K = (1, 3, 5)

_WHITESPACE = re.compile(r"\s+")


def normalise(text: str) -> str:
    """Collapse whitespace and case for span comparison.

    A PDF extractor breaks lines wherever the page did, so a span that a
    human wrote as one sentence arrives with newlines inside it. Comparing
    raw text would fail on formatting rather than on retrieval.
    """
    return _WHITESPACE.sub(" ", text).strip().casefold()


@dataclass
class Question:
    id: str
    doc: str
    question: str
    answer_span: str
    review: str = "draft"


@dataclass
class Result:
    question: Question
    rank: int | None
    retrieved: list[str] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return self.rank is not None


def load_questions(path: Path) -> list[Question]:
    questions: list[Question] = []

    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue

        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{number} is not valid JSON: {exc}") from exc

        questions.append(
            Question(
                id=record["id"],
                doc=record["doc"],
                question=record["question"],
                answer_span=record["answer_span"],
                review=record.get("review", "draft"),
            )
        )

    seen = [q.id for q in questions]
    if len(set(seen)) != len(seen):
        raise ValueError("question ids must be unique")

    return questions


def read_corpus(corpus: Path) -> dict[str, str]:
    """Return normalised text for every PDF in the corpus, keyed by stem."""
    pdfs = sorted(corpus.glob("*.pdf"))

    if not pdfs:
        raise FileNotFoundError(
            f"{corpus} contains no PDFs. Run `python eval/build_corpus.py` first."
        )

    return {pdf.stem: normalise(extract_text_from_pdf(str(pdf))) for pdf in pdfs}


def validate(questions: list[Question], texts: dict[str, str]) -> tuple[list[str], list[str]]:
    """Check every question against the corpus it will be scored on.

    Two things can make a question unscoreable, and both are properties of the
    question set rather than of retrieval:

    `missing` - the span is not in the document it is attributed to, so it can
    never be retrieved and depresses every metric for no reason.

    `ambiguous` - the span also appears in some other document. Scoring counts
    a hit only when the retrieved chunk is from `question.doc`, so a distractor
    carrying the same sentence would be marked wrong while actually answering
    the question. That is a defect in the ground truth, not in the retriever.
    """
    missing: list[str] = []
    ambiguous: list[str] = []

    for question in questions:
        if question.doc not in texts:
            raise FileNotFoundError(
                f"{question.doc}.pdf is missing from the corpus (question "
                f"{question.id}). Run `python eval/build_corpus.py` first."
            )

        span = normalise(question.answer_span)

        if span not in texts[question.doc]:
            missing.append(question.id)
            continue

        elsewhere = [doc for doc, text in texts.items() if doc != question.doc and span in text]

        if elsewhere:
            ambiguous.append(f"{question.id} (also in {', '.join(elsewhere)})")

    return missing, ambiguous


async def index_corpus(
    corpus: Path,
    organization_id: str,
    embedding_provider: EmbeddingProvider,
    vector_store: VectorStore,
    max_chunk_tokens: int | None = None,
    chunk_provider: EmbeddingProvider | None = None,
) -> dict[str, int]:
    """Extract, chunk, embed and upsert every PDF. Returns chunks per document."""
    counts: dict[str, int] = {}

    for pdf in sorted(corpus.glob("*.pdf")):
        doc = pdf.stem
        chunks = chunk_pages(
            extract_pages(str(pdf)),
            chunk_provider or embedding_provider,
            max_tokens=max_chunk_tokens,
        )

        if not chunks:
            counts[doc] = 0
            continue

        vectors = await embedding_provider.embed(
            [chunk.content for chunk in chunks],
            input_type="passage",
        )

        payloads: list[dict[str, Any]] = [
            {
                "organization_id": organization_id,
                "document_id": doc,
                "chunk_id": f"{doc}:{index}",
                "chunk_index": index,
                "page_number": chunk.page_number,
                "content": chunk.content,
                "filename": f"{doc}.pdf",
            }
            for index, chunk in enumerate(chunks)
        ]

        await vector_store.upsert(
            organization_id=organization_id,
            vectors=vectors,
            payloads=payloads,
            sparse_vectors=(
                [encode_passage(chunk.content) for chunk in chunks]
                if vector_store.hybrid
                else None
            ),
        )

        counts[doc] = len(chunks)

    return counts


async def run_questions(
    questions: list[Question],
    organization_id: str,
    embedding_provider: EmbeddingProvider,
    vector_store: VectorStore,
    depth: int = SEARCH_DEPTH,
    rerank_provider: RerankProvider | None = None,
) -> list[Result]:
    results: list[Result] = []

    for question in questions:
        # Through retrieve() rather than reaching for the store directly.
        # The harness exists to predict what the application does, and it
        # can only do that if it takes the same path - reimplementing the
        # fetch-wider-then-rerank order here would eventually measure a
        # pipeline nobody ships.
        hits = await retrieve(
            organization_id=organization_id,
            query=question.question,
            embedding_provider=embedding_provider,
            vector_store=vector_store,
            limit=depth,
            rerank_provider=rerank_provider,
        )

        span = normalise(question.answer_span)
        rank: int | None = None
        retrieved: list[str] = []

        for position, hit in enumerate(hits, start=1):
            doc = str(hit.get("document_id", ""))
            retrieved.append(doc)

            # A hit is the right document AND the passage that answers it.
            # Document-level matching would score a policy question correct
            # for retrieving any chunk of a sixty-page policy.
            if rank is None and doc == question.doc:
                if span in normalise(str(hit.get("content", ""))):
                    rank = position

        results.append(Result(question=question, rank=rank, retrieved=retrieved))

    return results


def score(results: list[Result]) -> dict[str, float]:
    total = len(results)

    if total == 0:
        return {}

    metrics = {
        f"recall@{k}": sum(1 for r in results if r.rank is not None and r.rank <= k) / total
        for k in REPORTED_K
    }
    metrics["mrr"] = statistics.fmean(
        (1.0 / r.rank if r.rank is not None else 0.0) for r in results
    )
    metrics["not_retrieved"] = sum(1 for r in results if not r.found) / total

    return metrics


def configuration(
    embedding_provider: EmbeddingProvider,
    max_chunk_tokens: int | None = None,
    chunk_provider: EmbeddingProvider | None = None,
    hybrid: bool = False,
    rerank_provider: RerankProvider | None = None,
) -> dict[str, Any]:
    """Record the settings that produced a number, so runs stay comparable."""
    counter = chunk_provider or embedding_provider

    return {
        "chunker": {
            "function": f"{chunk_pages.__module__}.{chunk_pages.__name__}",
            "tokenizer": type(counter).__name__,
            "max_input_tokens": counter.max_input_tokens,
            # The budget actually applied, resolved the same way the
            # chunker resolves it. A report that recorded only the window
            # and an unset override would describe a 32k-window model as
            # having chunked at 32k, which is not what happened.
            "chunk_tokens": min(
                max_chunk_tokens or counter.chunk_tokens,
                counter.max_input_tokens,
            ),
            "max_chunk_tokens": max_chunk_tokens,
            "special_token_margin": SPECIAL_TOKEN_MARGIN,
            "overlap_ratio": OVERLAP_RATIO,
        },
        "embedding_provider": type(embedding_provider).__name__,
        "dimension": embedding_provider.dimension,
        "search_depth": SEARCH_DEPTH,
        "retrieval": "hybrid" if hybrid else "dense",
        "rerank": (
            {
                "provider": type(rerank_provider).__name__,
                "depth": RERANK_DEPTH,
            }
            if rerank_provider is not None
            else None
        ),
    }


def render(
    metrics: dict[str, float],
    results: list[Result],
    config: dict[str, Any],
    chunk_counts: dict[str, int],
    elapsed: float,
) -> str:
    lines: list[str] = []
    total = len(results)
    drafts = sum(1 for r in results if r.question.review == "draft")

    lines.append("Retrieval evaluation")
    lines.append("=" * 62)
    lines.append(
        f"chunker         {config['chunker']['chunk_tokens']} token chunks "
        f"({config['chunker']['max_input_tokens']} token window) / "
        f"{config['chunker']['overlap_ratio']:.0%} overlap"
    )
    lines.append(
        f"embeddings      {config['embedding_provider']} "
        f"({config['dimension']} dimensions)"
    )
    rerank = config.get("rerank")
    lines.append(
        f"retrieval       {config.get('retrieval', 'dense')}"
        + (
            f" + rerank top {rerank['depth']} ({rerank['provider']})"
            if rerank
            else ", no rerank"
        )
    )
    lines.append(
        f"corpus          {len(chunk_counts)} documents, "
        f"{sum(chunk_counts.values())} chunks"
    )
    lines.append(f"questions       {total} ({drafts} still marked draft)")
    lines.append("")

    for k in REPORTED_K:
        key = f"recall@{k}"
        hits = round(metrics[key] * total)
        lines.append(f"  {key:<12} {metrics[key]:>7.1%}   ({hits}/{total})")

    lines.append(f"  {'MRR':<12} {metrics['mrr']:>7.3f}")
    lines.append(
        f"  {'missed':<12} {metrics['not_retrieved']:>7.1%}   "
        f"({round(metrics['not_retrieved'] * total)}/{total} not in top {SEARCH_DEPTH})"
    )
    lines.append("")

    missed = [r for r in results if not r.found]

    if missed:
        lines.append(f"Not retrieved at any rank ({len(missed)}):")

        for result in missed:
            lines.append(f"  {result.question.id}  {result.question.doc}")
            lines.append(f"      {result.question.question}")

        lines.append("")

    lines.append(f"completed in {elapsed:.1f}s")

    return "\n".join(lines)


async def main_async(args: argparse.Namespace) -> int:
    questions = load_questions(args.questions)

    texts = read_corpus(args.corpus)
    missing, ambiguous = validate(questions, texts)

    if missing:
        print(
            "These answer spans do not appear in their document, so they can "
            "never be retrieved. Fix the span or the corpus before trusting "
            f"any number below:\n  {', '.join(missing)}",
            file=sys.stderr,
        )

    if ambiguous:
        print(
            "These answer spans appear in more than one document. Scoring only "
            "credits the attributed document, so these would be counted wrong "
            f"while answering correctly:\n  {', '.join(ambiguous)}",
            file=sys.stderr,
        )

    if missing or ambiguous:
        return 2

    if args.validate_only:
        print(
            f"{len(questions)} questions against {len(texts)} documents: every "
            "answer span is present in its own document and in no other."
        )
        return 0

    started = time.monotonic()

    if args.provider not in EMBEDDING_PROVIDERS:
        print(
            f"Unknown provider {args.provider!r}. Expected one of: "
            f"{', '.join(sorted(EMBEDDING_PROVIDERS))}.",
            file=sys.stderr,
        )
        return 2

    embedding_provider: EmbeddingProvider = EMBEDDING_PROVIDERS[args.provider]()

    vector_store: VectorStore = QdrantVectorStore(
        collection_name=args.collection,
        vector_size=embedding_provider.dimension,
        hybrid=args.hybrid,
    )

    # Constructed once, outside the question loop: it loads a model, and
    # paying for that 102 times would be timing the loader.
    rerank_provider = LocalRerankProvider() if args.rerank else None

    # Every run gets its own organisation id inside a collection that was just
    # dropped, so a run can never score against vectors left by the previous
    # one. Reproducibility is the entire point of a baseline number.
    organization_id = str(uuid.uuid4())

    # Chunking and embedding are separable on purpose. Two models with
    # different tokenizers produce different chunk boundaries at the same
    # nominal budget, so comparing them head-on measures the chunker and
    # the model at once. Pinning the chunker to one provider makes the
    # difference between two runs the embedding and nothing else.
    chunk_provider = embedding_provider

    if args.chunk_with and args.chunk_with != args.provider:
        chunk_provider = EMBEDDING_PROVIDERS[args.chunk_with]()

    chunk_counts = await index_corpus(
        args.corpus,
        organization_id,
        embedding_provider,
        vector_store,
        args.max_chunk_tokens,
        chunk_provider,
    )

    results = await run_questions(
        questions,
        organization_id,
        embedding_provider,
        vector_store,
        rerank_provider=rerank_provider,
    )

    metrics = score(results)
    config = configuration(
        embedding_provider,
        args.max_chunk_tokens,
        chunk_provider,
        hybrid=vector_store.hybrid,
        rerank_provider=rerank_provider,
    )
    elapsed = time.monotonic() - started

    print(render(metrics, results, config, chunk_counts, elapsed))

    if args.report:
        report = {
            "metrics": metrics,
            "configuration": config,
            "corpus": {
                "documents": len(chunk_counts),
                "chunks": sum(chunk_counts.values()),
                "chunks_per_document": chunk_counts,
            },
            "questions": {
                "total": len(questions),
                "draft": sum(1 for q in questions if q.review == "draft"),
            },
            "results": [
                {
                    "id": r.question.id,
                    "doc": r.question.doc,
                    "rank": r.rank,
                    "retrieved": r.retrieved,
                }
                for r in results
            ],
            "elapsed_seconds": round(elapsed, 2),
        }

        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"\nreport written to {args.report}")

    return 0


def drop_collection(name: str) -> None:
    """Delete the eval collection if it exists.

    The vendor client is imported here rather than used through `VectorStore`
    on purpose: dropping a collection is not something application code should
    ever be able to do, so the capability stays in the harness instead of
    being added to the provider interface for the convenience of a test tool.
    """
    from qdrant_client import QdrantClient

    client = QdrantClient(
        url=os.getenv("QDRANT_URL", "http://localhost:6333"),
        api_key=os.getenv("QDRANT_API_KEY"),
        timeout=30,
    )

    if client.collection_exists(name):
        client.delete_collection(name)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Score the retrieval stack against the hand-written question set.",
    )
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument(
        "--provider",
        default="local",
        help="embedding provider to score (local, nvidia)",
    )
    parser.add_argument(
        "--chunk-with",
        default=None,
        help=(
            "count tokens with this provider instead of the one being "
            "scored, so two models can be compared on identical chunks"
        ),
    )
    parser.add_argument(
        "--max-chunk-tokens",
        type=int,
        default=None,
        help=(
            "cap chunk size below the model window. Needed to compare "
            "models with different windows on equal terms - a 256-token "
            "model and a 32k-token one otherwise get different chunks as "
            "well as different embeddings, and the delta means nothing."
        ),
    )
    parser.add_argument(
        "--hybrid",
        action="store_true",
        help="index and search a lexical vector beside the dense one",
    )
    parser.add_argument(
        "--rerank",
        action="store_true",
        help=f"re-score the top {RERANK_DEPTH} with a cross-encoder",
    )
    parser.add_argument("--report", type=Path, help="write a JSON report here")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="check the question set against the corpus and exit",
    )
    args = parser.parse_args()

    if not args.validate_only:
        drop_collection(args.collection)

    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
