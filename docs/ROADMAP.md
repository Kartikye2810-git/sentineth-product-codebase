# Roadmap

The single source of truth for what Sentineth builds next, and in what order.
`AGENTS.md` section 26 and `docs/PROJECT_STATUS_SEPT_2026.md` used to carry
roadmaps of their own; both now point here.

Phases are sequential. Each one exists because the next phase depends on it,
and the "Done when" line is the test for moving on — not a feeling of
completeness.

| Phase | Focus | Effort | Why it comes first |
| ----- | ----- | ------ | ------------------ |
| 0 | Trustworthy baseline | ~1 wk | Nothing downstream can be measured against a baseline that is not green |
| 1 | Correct, measurable retrieval | ~2 wk | Retrieval quality is the product claim; measure before tuning |
| 2 | Concurrency | ~2 wk | Changes the upload API contract, so it precedes anything built on it |
| 3 | Identity and operability | ~3 wk | Phase 4 needs an actor; retrofitting identity into a schema is expensive |
| 4 | Knowledge model | ~4–6 wk | This is the product |
| 5 | Connectors | ~2–3 wk each | Needs the source and permission models from Phases 3 and 4 |
| 6 | Frontend and agents | — | Would be rebuilt twice if started earlier |

---

## Phase 0 — Restore a trustworthy baseline

Bug fixes on the existing baseline. No feature work.

- **0.1** — Match `tests/fakes.py` to the provider interfaces so `main` is
  green. *Done when a fresh clone of `main` runs the suite green.*
- **0.2** — GitHub Actions on push and PR: `ruff check`, `pytest`, then
  `alembic upgrade head` and `alembic check` against a throwaway Postgres
  service container. `pyproject.toml` carries ruff and mypy configuration.
  *Done when a PR that breaks a test cannot be merged.*
- **0.3** — Store uploads at `{org}/{document_id}/{filename}` so two uploads
  sharing a filename cannot collide. *Done when both files survive and
  deleting one leaves the other intact.*
- **0.4** — Complete the API-key lifecycle: list metadata, revoke, optional
  expiry, and an explicit UTC clock. *Done when a leaked key can be revoked
  through the API.*
- **0.5** — Response models on every route, with pagination on the document
  listing. *Done when no route returns a bare ORM object.*
- **0.6** — Structured JSON logging with a request id per line, and an error
  taxonomy: `UnsupportedMediaType` → 415, `ExtractionFailed` → 422,
  `ProviderUnavailable` → 503. *Done when a failed upload produces one
  actionable log line and a status code the client can act on.*
- **0.7** — One layer owns the transaction boundary, and a failed ingest
  deletes the file it stored. *Done when a failed upload leaves no orphaned
  file.*
- **0.8** — Remove code nothing calls, and correct the documentation.
  *Done when the README describes the software that exists.*

**Done when:** CI is green on a fresh clone and every item above passes its
own criterion.

---

## Phase 1 — Make retrieval correct, and make correctness measurable

The highest-value phase. Everything Sentineth claims rests on returning the
right passage.

- **1.1** — Build the retrieval evaluation harness first, before changing any
  retrieval behaviour. A fixed corpus, a question set with known-correct
  chunks, and recall@k / MRR reported per run.
- **1.2** — Align chunk size with the embedding model's context window.
- **1.3** — Chunk on semantic boundaries (paragraph, section, sentence)
  rather than character offsets.
- **1.4** — Re-evaluate the embedding model against the harness, now that
  there is a number to compare.
- **1.5** — Add a Qdrant payload index on `organization_id`.
- **1.6** — Add hybrid retrieval (dense + keyword) and a reranker.

**Done when:** the harness reports a recall@5 you would be willing to put in
front of a customer, and every later change is measured against it.

---

## Phase 2 — Make the service survive more than one user

- **2.1** — Get blocking work off the event loop.
- **2.2** — Move ingestion out of the request entirely; upload becomes an
  async 202 contract with a status to poll.
- **2.3** — Bound the inputs: file size, page count, request body, query
  length, concurrent jobs per organization.
- **2.4** — Make timeouts and failure modes explicit at every provider call.

**Done when:** a load test with concurrent uploads and queries keeps query
latency flat while ingestion runs.

---

## Phase 3 — Real identity, and the ability to operate the service

- **3.1** — Users, memberships and roles, above the current organization
  API keys.
- **3.2** — Test the security boundary properly: missing, wrong, revoked and
  expired credentials against every route, including cross-organization
  lifecycle actions.
- **3.3** — Append-only audit log of security and data events.
- **3.4** — Ship an operable service: Dockerfile, health and readiness
  probes, metrics, configuration validated at startup.
- **3.5** — Backups, and a restore that has actually been run.
- **3.6** — Secrets and configuration management.

**Done when:** a design partner can be given a URL and credentials, and you
can tell them what happened when something goes wrong.

---

## Phase 4 — The knowledge model: from documents to company intelligence

- **4.1** — Introduce a `Source` abstraction above `Document`, so a Slack
  thread and a PDF are both sources.
- **4.2** — Add the entity layer: people, projects, decisions, systems.
- **4.3** — Add the relationship layer connecting those entities.
- **4.4** — Build the extraction pipeline that populates entities and
  relationships from sources, with confidence and provenance.
- **4.5** — Make retrieval entity-aware.

**Done when:** the system can answer a question that no single document
states outright, and show which sources support the answer.

---

## Phase 5 — Connectors

Built on the Phase 4.1 `Source` abstraction, never around it.

Start with **one** connector and finish it: OAuth, incremental sync, change
detection, deletion propagation, per-source permission mapping, and backfill
with rate-limit handling. A half-finished connector produces confident answers
from stale data.

GitHub first if the design partners are engineering-led; Slack first if they
are operations-led.

Permission mapping is the part to design before the first connector ships.
Once Slack is ingested, "who may see this content" stops being a
per-organization question and becomes per-channel — an extension of the
Phase 3 identity model, and expensive to retrofit.

---

## Phase 6 — Frontend and agent workflows

Deliberately last. A UI built on the Phase 0–2 API shape would be rebuilt
after 2.2 changes upload to an async contract, and again after Phase 4 makes
entities first-class.

When it starts: a workspace, a document library, a chat surface with inline
citations that open the source at the right page, and a view of the entity
graph so users can see why the system believes what it says — and correct it.
That correction loop is the point.

Agent workflows come after the graph is trustworthy.
