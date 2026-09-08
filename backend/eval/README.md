# Retrieval evaluation

This directory answers one question: **when we change retrieval, does it get
better or worse, and by how much?**

Every item in Phase 1 of the roadmap changes retrieval. Without a number,
each one ships on the strength of somebody having tried three queries and
liked the look of the answers. The harness exists so that a change to
chunking, to the embedding model, or to the search strategy produces a
before-and-after that can be put in front of a design partner.

## Running it

```bash
docker compose up -d qdrant          # from the repo root
python eval/build_corpus.py          # markdown -> PDF
python eval/harness.py               # score the current configuration
python eval/harness.py --report out.json
python eval/harness.py --validate-only   # check the question set, score nothing
```

The harness drops and recreates its own collection (`sentineth_eval`) on
every run, and indexes under a fresh organisation id, so a run can never
score vectors left behind by an earlier one.

## What is being measured

The harness drives the **real pipeline**: `extract_text_from_pdf` →
`chunk_text` → the configured `EmbeddingProvider` → the configured
`VectorStore`. It reads no settings of its own. Change the chunker or swap
the provider and the number moves here without anyone editing this
directory.

It deliberately stops before the LLM. Scoring a generated answer needs a
judge, and a judge is a second model whose own drift would land inside the
number we are trying to defend. Retrieval either put the right passage in
the context window or it did not; that part is measurable without opinion.

Metrics are **recall@1 / @3 / @5** and **MRR**, over a search depth of 10.

## The corpus

`corpus/*.md` — 20 documents, written by hand to look like the internal
documents of a mid-size software company: policies, an incident postmortem,
a roadmap, board minutes, a runbook, a technical spec.

They are stored as markdown because markdown diffs and PDFs do not, and
rendered to `corpus_pdf/` by `build_corpus.py`. The rendered PDFs are
gitignored: they are a build product. Feeding the harness plain text instead
would skip pypdf and quietly change what the chunker sees, so the corpus is
always scored through the same extractor production uses.

Documents are multi-page on purpose. Page numbers become ground truth in
roadmap item 1.3, and a single-page corpus cannot tell a correct page number
from a hardcoded `1`.

**Twelve of the twenty documents are judged** — every question points at one
of `01`–`12`. Documents `13`–`20` carry no questions at all. They are
distractors, and they are the reason the number means anything: with twelve
short documents, "the answer is in the top 5" is a weak claim, because the
top 5 is a large fraction of everything there is. Several of them are
deliberate near-misses for a judged document — an earlier quarter's roadmap,
a second postmortem, an expenses FAQ sitting next to the expenses policy.

## The ground-truth contract

Each line of `questions.jsonl`:

```json
{
  "id": "q004",
  "doc": "01-travel-and-expense-policy",
  "question": "What is the nightly hotel limit for a stay in San Francisco?",
  "answer_span": "GBP 210 per night in New York and San Francisco",
  "review": "draft"
}
```

Ground truth is **a document plus a verbatim span, never a chunk id.** Chunk
ids change the moment chunking changes, and items 1.2 and 1.3 both change
chunking; a question set keyed to chunk ids would invalidate itself halfway
through the phase it was built to measure. A span survives re-chunking and
survives a model swap, so the same 102 questions stay comparable across all
of Phase 1.

A retrieved chunk counts as a hit when **both** hold:

1. its `document_id` equals the question's `doc`, and
2. its content contains the `answer_span`.

Requiring only (1) would score a policy question correct for retrieving any
chunk of a sixty-page policy. Requiring only (2) is what makes (3) below
necessary.

### Two rules the question set must satisfy

`--validate-only` enforces both, and the harness refuses to print a number
if either is broken:

- **Present.** The span appears in its own document. A span that does not is
  unscoreable — nothing can ever match it, and it depresses every metric for
  a reason that has nothing to do with retrieval.
- **Unique.** The span appears in *no other* document. This is the one that
  fails silently: a distractor containing the same sentence would answer the
  question perfectly and still be scored wrong, because scoring credits only
  the attributed document. It caught a real collision the first time it ran —
  a vendor-certification sentence restated word for word in both the security
  policy and the procurement guidelines.

The comparison normalises whitespace and case, because the PDF extractor
breaks lines wherever the page did and a span written as one sentence comes
back with newlines inside it.

### Writing questions

Questions are **hand-written, not model-generated.** The point of ground
truth is that it is trusted; ground truth produced by a model measures
agreement with that model. They are phrased the way somebody would actually
ask — "When do I have to send my laptop back after leaving?" — rather than
as a restatement of the source sentence, which would reduce the task to
lexical overlap and flatter every retriever equally.

`review` is `draft` until a question has been read back and accepted. Every
report prints how many are still draft, so a number is never quoted without
saying how provisional its ground truth is.

## Comparing two runs

```bash
python eval/compare.py before.json after.json
```

**Do not read a delta between two headline percentages.** The same questions
are scored before and after, so the runs are paired, and the quantity that
matters is which individual questions changed. Two runs can differ by four
points of recall@5 with eighteen questions moving one way and thirteen the
other — that is a reshuffle — or with eighteen moving one way and none the
other, which is a result. `compare.py` prints won/lost counts and McNemar's
exact p-value over the questions that changed.

The scale of the noise is worth internalising: sweeping the chunk token
target from 120 to 252 moved recall@5 between 76.5% and 82.4% **with no
trend**, adjacent targets disagreeing as much as distant ones. On 102
questions, a six-point swing can be nothing more than where the chunk
boundaries happened to fall. A coarse sweep of the same parameter had
produced a convincing-looking peak that the finer sweep dissolved.

## Reading the numbers

**Chunk count is part of the result.** recall@5 over 33 large chunks and
recall@5 over 88 smaller ones are not the same test: the second asks the
retriever to find the answer in a much smaller slice of the corpus. Every
report records the token window, overlap, provider and dimension that
produced it for exactly this reason — quote those alongside the metric or
the metric is not comparable to anything.

**A headline number can hide two effects that cancel.** Item 1.2 moved
recall@5 by +4.9pp overall, p=0.47, which reads as "no change". Splitting
the questions by whether the truncation defect could reach them showed
+14.9pp on the 67 it had made invisible and −14.3pp on the 35 it never
touched. Both effects are real and the average of them is not. When a
change has a mechanism, define the subgroup that mechanism predicts — from
the configuration, before looking at any result — and score that subgroup
too.
## In CI

The `eval` job in `.github/workflows/ci.yml` runs the harness on every PR,
uploads the JSON report as an artifact, and comments the table.

It is `continue-on-error: true` and **must never be added to the required
checks.** Retrieval quality is a number to argue about, not a gate: a
legitimate change can move recall a point in either direction, and a red X
for that would only teach everyone to ignore red Xs.
