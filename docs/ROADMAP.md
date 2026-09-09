# Roadmap

The single source of truth for what Sentineth builds next, and in what order.
`info.md` section 26 and `docs/PROJECT_STATUS_SEPT_2026.md` used to carry
roadmaps of their own; both now point here.

Phases 0 through 3 are delivered. Phase 4 is next.

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

## Phase 0 — Restore a trustworthy baseline — **delivered** (`19d4751`)

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

## Phase 1 — Make retrieval correct, and make correctness measurable — **delivered** (`4732698`)

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

## Phase 2 — Make the service survive more than one user — **delivered**

- **2.1** — Get blocking work off the event loop. Every synchronous call the
  API still makes runs in a thread: the Qdrant client, the local embedding
  model, the reranker, request-body buffering and file deletion. PDF
  extraction and chunking left the API process altogether under 2.2.
- **2.2** — Move ingestion out of the request entirely; upload becomes an
  async 202 contract with a status to poll. Upload, delete and reindex all
  answer 202 with a `Location` header and queue an `ingestion_jobs` row;
  `GET /organizations/{org}/documents/{id}` is the status route. A separate
  `python -m app.worker` process claims jobs with `SELECT ... FOR UPDATE SKIP
  LOCKED` under a lease.
- **2.3** — Bound the inputs: file size, page count, request body, query
  length, concurrent jobs per organization. All of it lives in
  `app/settings.py` with `SENTINETH_`-prefixed overrides, plus per-org
  document count and storage quotas and per-minute upload and query limits.
- **2.4** — Make timeouts and failure modes explicit at every provider call.
  Every provider call has a deadline; a timeout is `PROVIDER_UNAVAILABLE`
  (503, retryable) rather than a hung request, and the taxonomy gained
  `SOURCE_MISSING` (410), `INPUT_TOO_LARGE` (413), `QUOTA_EXCEEDED` (429) and
  `DOCUMENT_BUSY` (409).

The queue is Postgres, not Redis. The job and the document row are written in
one transaction, so "queued job with no document" and "document nothing will
process" are unreachable states rather than states to reconcile. Redis buys
throughput this phase does not need and adds a second system that can lose
the association.

**Acceptance criteria, each with a test:**

- *Retries.* A transient provider failure is retried and leaves no duplicate
  vectors. A crashed worker's job is reclaimed only after its lease expires.
  Retries back off exponentially. The budget is finite: exhausting it
  dead-letters the document as `FAILED` with `RETRIES_EXHAUSTED`, and a
  failure that retrying cannot fix is not retried at all.
- *Idempotency.* Point IDs are `uuid5` over document, `index_generation` and
  chunk index — but deterministic IDs alone are not retry safety, because a
  changed chunker regenerates different chunk IDs and the old points would
  survive. So a re-ingest deletes the document's vectors before writing any.
  A reindex after a chunking change leaves no stale vectors.
- *Cancellation and deletion races.* Deleting a queued document cancels its
  job. A document being processed rejects a conflicting delete or reindex
  with 409 rather than racing the worker.
- *Recovery across Postgres, files and Qdrant.* A delete leaves nothing in
  any of the three. A document whose file is gone fails as `SOURCE_MISSING`
  (410) and is not retried, because retrying cannot find bytes that do not
  exist. Vectors belonging to failed or deleted documents are never returned
  by search.

**Done when:** a load test with concurrent uploads and queries keeps query
latency flat while ingestion runs. `tests/test_postgres_jobs.py` asserts this
against a real Postgres, along with six workers claiming six distinct jobs
and a held lease not being stolen.

---

## Phase 3 — Real identity, and the ability to operate the service — **delivered**

- **3.1** — Users, memberships and roles above the organization API keys.
  `users`, `memberships`, `user_sessions` and `invitations`, with Argon2id
  password hashing and opaque `sentineth_session_*` bearer tokens stored only
  as SHA-256 hashes. A session lasts 12 hours and is revocable; an invitation
  is single-use and lasts 48. API keys carry a role too, so a viewer key and a
  viewer person meet the same check in `require_organization_role`.
  `POST /organizations` now requires a signed-in person — a key belongs to an
  organization, so it cannot be what brings one into existence.
- **3.2** — Test the security boundary properly.
  `tests/test_identity.py` runs nine credential states — missing, wrong
  scheme, malformed, expired, revoked, foreign, and the session equivalents of
  the last three — against every tenant route the app exposes, enumerated from
  the route table rather than listed by hand, so a route added later is
  covered the day it is added. Missing and malformed answer 401; real but
  unusable answers 403.
- **3.3** — Append-only audit log of security and data events.
  `audit_events` records actor type, actor id, action, resource, request id
  and organization. Append-only is enforced by a database trigger that rejects
  `UPDATE`, `DELETE` and `TRUNCATE`, not by convention in the application, and
  owners read their own organization's history through the API.
- **3.4** — Ship an operable service. One Dockerfile running the API or the
  worker as a non-root user; `/live` for the process, `/ready` for Postgres's
  schema revision, Qdrant's dimension and hybrid config, and a writable
  storage directory; Prometheus metrics behind a token; every setting
  validated in `app/settings.py` at startup rather than at first use.
- **3.5** — Backups, and a restore that has actually been run. `pg_dump` plus
  the source files, each checksummed against `documents.content_hash`.
  `scripts/rehearse_restore.py` performs the whole documented recovery against
  disposable databases and collections, and CI runs it on every push.
- **3.6** — Secrets and configuration management. Secrets are `SecretStr` and
  can be read from files rather than the environment; `production` refuses to
  start on SQLite, a short metrics token, a wildcard host or origin, a
  plain-HTTP provider URL, debug logging or SQL echo.

**Acceptance criteria, each with a test:**

- *Credentials fail closed.* Every tenant route rejects a credential that is
  missing, malformed, expired, revoked or from another organization, and does
  so with the same code for the same reason regardless of route.
- *Roles are not advisory.* A viewer cannot upload, a member cannot read the
  audit log or issue keys, and an API key — whatever its role — cannot change
  membership. Rotation keeps the rotating key's role and cannot extend its own
  expiry, so a leaked key cannot rotate itself into a stronger one.
- *An organization keeps a human owner.* Removing or demoting the last human
  owner is refused, and demoting one revokes the invitations they issued, so
  access cannot be restored through a door the removed owner left open.
- *History cannot be edited.* Raw `UPDATE`, `DELETE` and `TRUNCATE` against
  `audit_events` are rejected by the database.
- *Readiness means ready.* `/ready` answers 503 when the schema revision is
  wrong, the collection's dimension disagrees with the embedding provider, or
  storage is not writable — the states where accepting traffic would corrupt
  or fail silently.
- *Restore is rehearsed, not hoped for.* The drill restores into empty state,
  refuses to restore into non-empty state, rebuilds vectors from Postgres and
  the files, and revokes every session, key and invitation on the way in.

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
