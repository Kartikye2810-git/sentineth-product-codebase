# Sentineth Project Status

## Vision

Sentineth is intended to be a company intelligence platform: it should connect Slack, Teams, GitHub, email, meetings, documents, and internal systems; continuously build organizational memory; become the company-wide source of truth; answer questions with grounded retrieval; and eventually power agent workflows.

## Current State Summary

Maturity is approximately **35%** of that vision, through Phase 3 of
`docs/ROADMAP.md`. Today the repository provides a backend-first PDF RAG
service: organizations, organization-filtered document storage and Qdrant
search, measured retrieval, durable background ingestion behind an async
202 contract, bounded inputs and per-organization quotas, and OpenRouter
answer generation. Requests now carry an actor: user accounts, sessions,
invitations, owner/member/viewer roles on both people and API keys, an
append-only audit trail, health and readiness probes, metrics, a container
image and a rehearsed restore. It has no frontend, connectors, agents or
knowledge graph.

Architecture: FastAPI + SQLAlchemy/Postgres for state and for the job
queue, a separate worker process for ingestion, local filesystem for
source files, Qdrant for vectors, NVIDIA `nemotron-3-embed-1b` for
embeddings (MiniLM remains as the offline provider), and OpenRouter for
the answer model.

## Completed Work

### Multi-Tenant Foundation

`Organization`, `Document`, and Qdrant payloads carry organization identity. Retrieval filters Qdrant by `organization_id`; document deletion/reindex services compare the URL organization with `Document.organization_id`.

### Security Hardening

Commit `d8dec032e40c1b20f704f52fd82642b9aef90d45` — `feat: secure organization document lifecycle` — adds `OrganizationApiKey`, SHA-256 key storage, one-time key creation, rotation, route authorization, document ownership validation, and organization-scoped vector deletion. See `backend/app/security.py`, `backend/app/main.py`, and `backend/app/services/document_service.py`.

### RAG Infrastructure

PDF upload extracts text with pypdf, chunks it, embeds chunks, stores vectors in Qdrant, retrieves tenant-filtered chunks, and sends grounded context to OpenRouter. Sources return document/chunk metadata. Core paths are `backend/app/services/ingestion_service.py`, `retrieval_service.py`, and `query_service.py`.

### Document Lifecycle

Implemented routes: upload, status, list, delete, and single-document reindex in `backend/app/api/documents.py`. Upload, delete and reindex answer 202 and queue work; delete removes organization-filtered vectors, the stored file, and the database record.

### Measurable Retrieval (Phase 1)

Chunking follows semantic boundaries and is sized to the embedding model's token window; retrieval carries page numbers into citations. `backend/eval/` holds a 22-document corpus and 134 questions in four kinds — span, identifier, unanswerable and conflicting — with recall@k, MRR and per-kind reporting, and `compare.py` for paired significance testing between runs. Latest run: **94.1% recall@5, MRR 0.832** across the 118 recall-scored questions.

### Durable Ingestion and Limits (Phase 2)

`ingestion_jobs` in Postgres, claimed by `python -m app.worker` with `SELECT ... FOR UPDATE SKIP LOCKED` under a lease, with finite retries and dead-lettering. Blocking work runs in a thread; every provider call has a timeout. `app/settings.py` bounds upload size, page count, chunk count, request body, query length, and per-organization documents, storage, pending jobs, uploads per minute and queries per minute. Failures carry stable error codes: 409, 410, 413, 415, 422, 429, 503.

### Identity and Operations (Phase 3)

`User`, `Membership`, `UserSession`, `Invitation`, `AuditEvent` and
`UsageRecord` in Postgres. Argon2id passwords, opaque revocable session
tokens, single-use invitations, and roles carried by API keys as well as by
people, all resolved through one check in `app/security.py`. `POST
/organizations` requires a signed-in person; membership administration
requires a human owner, and an organization cannot lose its last one. The
audit trail is append-only by database trigger. Operationally: a Dockerfile
running API or worker as non-root, `/live` and `/ready`, token-guarded
`/metrics`, per-organization LLM usage and cost, settings validated at
startup with production-only strictness, and `pg_dump`-based backup, verify
and fail-closed restore rehearsed by `scripts/rehearse_restore.py` in CI.

## Current Architecture

```mermaid
flowchart LR
  Client --> API[FastAPI]
  API --> Auth[Bearer session or org API key]
  API --> PG[(Postgres)]
  API --> Store[Local file storage]
  API --> Q[(Qdrant)]
  API --> LLM[OpenRouter]
```

### Ingestion Flow

```mermaid
flowchart LR
 PDF --> Upload --> Store --> Job[(ingestion_jobs)]
 Upload --> Accepted[202 + Location]
 Job --> Worker --> Extract --> Chunk --> Embed --> Qdrant
 Worker --> Document[(Postgres document/chunks)]
 Accepted -.poll.-> Document
```

### Query Flow

```mermaid
flowchart LR
 Question --> Embed --> FilteredSearch[Qdrant organization filter] --> Context --> OpenRouter --> Answer
```

### Authorization Flow

```mermaid
flowchart LR
 Bearer --> SHA256 --> Credential[user_sessions or organization_api_keys]
 Credential --> Role[membership or key role] --> OrgRoute
 OrgRoute --> Ownership[document.organization_id check]
 OrgRoute --> Audit[(audit_events)]
```

## Repository Map

- `backend/app/api`: FastAPI HTTP routes; `documents.py` owns lifecycle/search/query endpoints, `auth.py` sessions and invitations, `organizations.py` tenants, members, keys, audit and usage.
- `backend/app/services`: business flow; ingestion, chunking, extraction, retrieval, query, and document lifecycle.
- `backend/app/db`: SQLAlchemy engine and the `Organization`, `Document`, `DocumentChunk`, `OrganizationApiKey`, `IngestionJob`, `OrganizationRateLimit`, `User`, `Membership`, `UserSession`, `Invitation`, `AuthThrottle`, `AuditEvent` and `UsageRecord` models.
- `backend/app/worker.py`: claims and runs ingestion jobs; `settings.py` holds the validated configuration and resource limits.
- `backend/app/security.py`, `audit.py`, `health.py`, `observability.py`: authentication and roles, the append-only trail, probes, metrics and usage.
- `backend/app/admin.py`, `backup.py`, `relocate_storage.py`, `scripts/rehearse_restore.py`: operator commands and the recovery drill; `docs/OPERATIONS.md` is the written procedure.
- `backend/eval`: retrieval corpus, question set and harness.
- `backend/app/providers`: swappable embeddings, LLM, storage, and vector interfaces/adapters.
- `backend/tests`: in-memory provider tests for RAG, chunking, durable jobs and the credential matrix in `test_identity.py`; `test_postgres_jobs.py` needs a real Postgres and skips without `TEST_POSTGRES_URL`.
- `backend/alembic`: schema migrations; the identity and operations migration is `f93a61c2d704_phase3_identity_operations.py`.

## Current Technical Debt

- No knowledge model, connectors, agents or frontend — Phases 4 to 6. API-key list/revoke/rotate landed in Phase 0.4; CI landed in 0.2; rate limiting in 2.3; users, roles, audit and operations in Phase 3.
- No email delivery, so an invitation token has to be handed to its recipient by other means, and there is no self-service password reset — an operator runs `python -m app.admin reset-password`.
- Qdrant is not backed up; vectors are rebuilt from Postgres and the source files on restore, which is correct but slow for a large corpus.
- PDF-only extraction; unsupported types map to 415.
- Local storage is still a scalability bottleneck, and the worker is a single process by default (it scales by running more).
- No real Qdrant/OpenRouter integration suite. Postgres has two gated tests, and the retrieval benchmark exists in `backend/eval/`.

## Vision Gap Analysis

### Already Built

PDF RAG, tenant-filtered vector retrieval, API-key foundation, and basic document lifecycle.

### Partially Built

Multi-tenancy and security: organization boundaries, users, roles and an audit trail exist; no SSO, and permissions are per-organization rather than per-source. Organizational knowledge is document-only, not persistent cross-source memory.

### Completely Missing

Slack, GitHub, Teams, email, meeting ingestion, change detection, organizational memory, knowledge graph, agents/workflows, and frontend.

## Next Priorities

See `docs/ROADMAP.md`. It is the single source of truth for sequencing, and
replaces the three-phase priority tables that used to sit here.

## Enterprise Readiness Assessment

Security **65%**: hashed credentials, Argon2id passwords, revocable sessions, role-scoped keys, tenant filters, throttled authentication and a full credential-rejection matrix, but no SSO, MFA or per-source permissions. Multi-tenancy **75%**: org filters, ownership checks, per-org quotas and membership administration exist. Observability **55%** (structured logs, metrics, readiness, per-org usage; no tracing). Compliance **25%** (append-only audit trail; no retention policy or data-export path). Reliability **65%** (durable retries, explicit failure taxonomy, rehearsed restore). Scalability **35%** (ingestion is off the request path and horizontally scalable; storage is not).

## Guidance For Future Contributors

Read this document and `info.md` first. Inspect architecture and call sites before edits. Preserve organization boundaries in every DB query, vector filter, storage path, and service action. Never bypass authorization. Keep commits narrowly scoped; do not mix generated artifacts with feature work; run tests before every commit.

## Recommended Next Task

Phase 4 of `docs/ROADMAP.md`: the knowledge model. Phases 0 through 3 are
delivered. Start at 4.1, the `Source` abstraction above `Document` — every
later item, and every Phase 5 connector, is built on it, and introducing it
after entities exist means rewriting them.
