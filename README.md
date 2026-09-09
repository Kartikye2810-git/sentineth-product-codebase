# Sentineth AI

Organizational intelligence platform. Upload your company's documents, then ask
questions in plain language and get answers grounded in those documents, with
citations back to the source file.

Every document, vector and answer is scoped to an organization. One tenant
cannot read another tenant's data.

Status: pre-alpha, but deployable. The RAG pipeline works end to end, ingestion
runs in a separate worker process, and uploads, queries and per-organization
storage are all bounded. Requests now come from someone: people sign in and hold
a revocable session, machines hold a role-scoped API key, every route checks
membership, and who did what to which document is written to an append-only
audit log. There is a container image, a readiness check that actually asks
Postgres and Qdrant, metrics, and a rehearsed restore procedure. There is still
no frontend, and PDF is the only accepted file type.

## How it works

```
PDF upload
  -> stream to disk, queue an ingestion job, answer 202 + Location
                                |
worker (separate process)       |
  -> claim a job (SELECT ... FOR UPDATE SKIP LOCKED, leased)
  -> extract text (pypdf), one entry per page
  -> chunk to the embedding model's token window
  -> embed (nemotron-3-embed-1b, 2048 dims, NVIDIA)
  -> index in Qdrant, payload stamped with organization_id
  -> document status READY, or FAILED with an error code
                                |
GET the Location until status is READY
                                |
question                        |
  -> embed the question         |
  -> vector search, filtered by organization_id  <----+
  -> assemble the retrieved chunks into a prompt
  -> LLM (OpenRouter) answers using only that context
  -> answer + citations, with page numbers
```

Postgres holds documents, chunk metadata and the job queue. Qdrant holds the
vectors. Files land on local disk under `backend/storage/documents/`.

The queue is Postgres rather than Redis on purpose: the job and the document
row it belongs to are written in one transaction, so a queued job that has no
document, or a document nothing will ever process, is not a state this system
can reach. That is worth more here than the throughput a dedicated broker
would add, and it is one less thing to run.

Providers (embeddings, LLM, vector store, file storage) sit behind interfaces in
`backend/app/providers/`, so any one of them can be swapped without touching the
services. See `info.md` for the full architecture and the rules that changes
to it must follow.

## Stack

| Layer      | Choice                                        |
| ---------- | --------------------------------------------- |
| API        | FastAPI, Uvicorn                              |
| Database   | PostgreSQL 17, SQLAlchemy 2.0, Alembic         |
| Vectors    | Qdrant 1.19                                   |
| Embeddings | NVIDIA `nemotron-3-embed-1b` (2048 dims), `all-MiniLM-L6-v2` offline |
| Ingestion  | Postgres-backed job queue, worker process      |
| LLM        | OpenRouter (OpenAI-compatible API)            |
| Extraction | pypdf                                         |
| Identity   | Argon2id passwords, opaque bearer sessions, role-scoped API keys |
| Operations | Docker image, Prometheus metrics, Sentry, pg_dump backups |

## Prerequisites

- Python 3.12+
- Docker Desktop (for Postgres and Qdrant)
- An OpenRouter API key, from https://openrouter.ai/keys

## Setup

Run everything below from the `sentineth/` directory (the one holding this
README) unless stated otherwise.

### 1. Start the databases

```bash
docker compose up -d
```

This brings up Postgres on `5432` and Qdrant on `6333`. Both use named Docker
volumes, so their data survives a container restart.

> **Heads up if you ran an earlier version of this project.** The Qdrant service
> was added to `docker-compose.yml` recently and is pinned to `v1.19.0`. If you
> already have a `sentineth-qdrant` container from before, `docker compose up`
> will replace it. Any vectors in the old container are lost, because it had no
> volume mounted. Nothing in Postgres is affected, but you will need to
> re-upload your documents to rebuild the index.

Check they are healthy:

```bash
docker compose ps
```

### 2. Create the virtualenv and install dependencies

```bash
cd backend
python -m venv .venv
```

Activate it. On Windows PowerShell:

```bash
.venv\Scripts\Activate.ps1
```

On bash (Git Bash, WSL, macOS, Linux):

```bash
source .venv/Scripts/activate
```

Then install:

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

This pulls in PyTorch, so expect a large download the first time. For a
CPU-only machine you can save roughly 2 GB by installing torch from the CPU
index first:

```bash
pip install torch==2.13.0 --index-url https://download.pytorch.org/whl/cpu
```

### 3. Configure the environment

Copy the template and fill it in:

```bash
cp ../.env.example ../.env
```

The `.env` file lives at the repo root (`sentineth/.env`), not in `backend/`.
It is gitignored. Never commit it.

Minimum working configuration:

```ini
DATABASE_URL=postgresql+psycopg://sentineth:sentineth_dev_password@localhost:5432/sentineth

QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=

OPENROUTER_API_KEY=sk-or-v1-your-key-here
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_LLM_MODEL=meta-llama/llama-3.3-70b-instruct:free

EMBEDDING_PROVIDER=nvidia
NVIDIA_API_KEY=nvapi-your-key-here
```

The username, password and database name in `DATABASE_URL` must match the
`environment` block in `docker-compose.yml`.

`NVIDIA_API_KEY` comes from https://build.nvidia.com and is required unless
you set `EMBEDDING_PROVIDER=local`, which runs MiniLM on the CPU with no key
and no network. Without a key the API and the worker fail closed with a 503
rather than quietly falling back to the weaker model - the two are 12.7
points of recall@5 apart on the eval set, so the fallback would be a silent
downgrade of the thing the product sells.

`OPENROUTER_LLM_MODEL` must be a real model slug from
https://openrouter.ai/models. The value shipped in `.env.example` is a
placeholder and will fail with a model-not-found error.

### 4. Run the migrations

From `backend/`:

```bash
alembic upgrade head
```

This creates `organizations`, `organization_api_keys`, `documents`,
`document_chunks`, `ingestion_jobs`, `organization_rate_limits`, `users`,
`memberships`, `user_sessions`, `invitations`, `auth_throttles`,
`audit_events` and `usage_records`.

### 5. Create the first account

There is no public sign-up. The first account is made by whoever runs the
service, from `backend/`:

```bash
python -m app.admin create-user --email you@example.com
```

The password is prompted, never passed as an argument, because arguments end up
in shell history and in `ps`. Everyone after the first is added by an
invitation from an owner. `python -m app.admin reset-password --email ...`
recovers a locked-out account and revokes that account's sessions.

### 6. Start the API

```bash
python -m uvicorn app.main:app --reload
```

Interactive docs: http://127.0.0.1:8000/docs

### 7. Start the worker

In a second terminal, from `backend/`:

```bash
python -m app.worker
```

Nothing is indexed without it. The API accepts an upload, writes the file and
queues a job; the worker is what turns that job into chunks and vectors. Run
as many as you like - they claim jobs with `SELECT ... FOR UPDATE SKIP LOCKED`
and cannot take each other's work.

With `EMBEDDING_PROVIDER=local`, the first request that needs embeddings
downloads the MiniLM weights (about 90 MB) into the Hugging Face cache and
takes 30 to 60 seconds. Every request after that reuses the loaded model,
because providers are cached for the lifetime of the process in
`app/dependencies.py`.

## Try it end to end

Sign in with the account you just created:

```bash
curl -X POST http://127.0.0.1:8000/auth/login -H "Content-Type: application/json" -d "{\"email\": \"you@example.com\", \"password\": \"your-password\"}"
```

The `access_token` is a session, good for 12 hours and revocable at any time
with `POST /auth/logout`. Everything below calls it `SESSION`.

Create an organization. This is the one route that needs a signed-in person
rather than a key - a key belongs to an organization, so it cannot be what
brings one into existence:

```bash
curl -X POST http://127.0.0.1:8000/organizations \
  -H "Authorization: Bearer SESSION" -H "Content-Type: application/json" -d "{\"name\": \"Acme Inc\"}"
```

You become its owner. Copy the `id` and the `api_key` from the response;
everything below uses them as `ORG_ID` and `ORG_KEY`. That key is the only
time a token is ever readable, and either credential works on the routes
below - the session because you are an owner of that organization, the key
because it was issued to it.

Upload a PDF:

```bash
curl -i -X POST http://127.0.0.1:8000/organizations/ORG_ID/documents \
  -H "Authorization: Bearer ORG_KEY" -F "file=@/path/to/your.pdf"
```

You get **202 Accepted**, a body reporting `"status": "QUEUED"`, and a
`Location` header pointing at the document. Indexing has not happened yet.
Poll that URL until the status settles:

```bash
curl http://127.0.0.1:8000/organizations/ORG_ID/documents/DOC_ID \
  -H "Authorization: Bearer ORG_KEY"
```

`QUEUED` and `PROCESSING` mean keep polling. `READY` reports `chunk_count`.
`FAILED` reports `error_code` and `error_message` saying which of the ways it
could fail it took - an unreadable PDF, a missing file, a provider that was
down. Delete and reindex answer 202 the same way, for the same reason: the
work happens in the worker, and a request that blocked until it finished would
be a request whose latency is somebody else's document.

Ask a question:

```bash
curl -X POST http://127.0.0.1:8000/organizations/ORG_ID/query \
  -H "Authorization: Bearer ORG_KEY" -H "Content-Type: application/json" -d "{\"query\": \"What is the revenue target for Q3?\"}"
```

You get an `answer` plus a `sources` array naming the file and chunk each claim
came from. If nothing relevant is indexed, the answer says so rather than
guessing.

Search without generating an answer:

```bash
curl -X POST http://127.0.0.1:8000/organizations/ORG_ID/search \
  -H "Authorization: Bearer ORG_KEY" -H "Content-Type: application/json" -d "{\"query\": \"revenue target\", \"limit\": 5}"
```

Then see who did it:

```bash
curl http://127.0.0.1:8000/organizations/ORG_ID/audit-events \
  -H "Authorization: Bearer SESSION"
```

## Accounts, roles and keys

Two kinds of caller, one authorization check. A **session** identifies a
person and carries their membership role for the organization in the URL. An
**API key** identifies an integration and carries the role it was issued with.
`require_organization_role` compares whichever one presented itself against
what the route needs, so a viewer key and a viewer person are refused in the
same place, for the same reason.

| Role   | Can                                                                 |
| ------ | ------------------------------------------------------------------- |
| viewer | read documents, search, query                                       |
| member | everything a viewer can, plus upload, reindex and delete            |
| owner  | everything a member can, plus keys, audit history and usage         |

Membership itself - inviting, changing a role, removing someone - needs an
owner who is a person, not a key. An integration that leaks should not be able
to add an owner to the organization it leaked from.

Adding someone: an owner posts to `/organizations/{org_id}/invitations` with an
email and a role and gets back a single-use token, valid 48 hours. The invitee
posts it to `/auth/accept-invitation` with the password they want. There is no
email delivery yet, so the token has to be handed over some other way.
Demoting or removing an owner revokes the invitations they issued, and an
organization will not let its last human owner go.

## API reference

| Method | Path                                             | Needs        | Purpose                             |
| ------ | ------------------------------------------------ | ------------ | ----------------------------------- |
| GET    | `/`                                              | -            | Service name and version            |
| GET    | `/live`                                          | -            | Liveness: the process is up         |
| GET    | `/ready`, `/health`                              | -            | Readiness: Postgres, Qdrant, storage |
| GET    | `/metrics`                                       | metrics token | Prometheus exposition              |
| POST   | `/auth/login`                                    | -            | Exchange a password for a session   |
| POST   | `/auth/accept-invitation`                        | -            | Join an organization, get a session |
| GET    | `/auth/me`                                       | session      | The signed-in account               |
| POST   | `/auth/logout`                                   | session      | Revoke this session                 |
| POST   | `/auth/change-password`                          | session      | Rotate the password, revoke sessions |
| POST   | `/organizations`                                 | session      | Create an organization, returns its key |
| GET    | `/organizations`                                 | session      | Organizations you are a member of   |
| GET    | `/organizations/{org_id}/members`                | human owner  | Who is in it, and as what           |
| PATCH  | `/organizations/{org_id}/members/{user_id}`      | human owner  | Change a role                       |
| DELETE | `/organizations/{org_id}/members/{user_id}`      | human owner  | Remove a member                     |
| GET    | `/organizations/{org_id}/invitations`            | human owner  | Pending and past invitations        |
| POST   | `/organizations/{org_id}/invitations`            | human owner  | Invite an email at a role           |
| DELETE | `/organizations/{org_id}/invitations/{inv_id}`   | human owner  | Revoke an invitation                |
| GET    | `/organizations/{org_id}/api-keys`               | owner        | Key metadata, never the token itself |
| POST   | `/organizations/{org_id}/api-keys`               | owner        | Issue a key at a role               |
| POST   | `/organizations/{org_id}/api-keys/rotate`        | the key      | Issue a replacement, revoke the old key |
| DELETE | `/organizations/{org_id}/api-keys/{key_id}`      | owner        | Revoke one key                      |
| GET    | `/organizations/{org_id}/audit-events`           | owner        | Who did what, when, paginated       |
| GET    | `/organizations/{org_id}/usage`                  | owner        | LLM tokens and cost, by model       |
| GET    | `/organizations/{org_id}/documents`              | viewer       | List documents, paginated           |
| GET    | `/organizations/{org_id}/documents/{doc_id}`     | viewer       | Document status, for polling after 202 |
| POST   | `/organizations/{org_id}/documents`              | member       | Upload a PDF (multipart), 202 + queue |
| DELETE | `/organizations/{org_id}/documents/{doc_id}`     | member       | Queue deletion of row, file and vectors, 202 |
| POST   | `/organizations/{org_id}/documents/{doc_id}/reindex` | member   | Queue a re-chunk and re-embed, 202  |
| POST   | `/organizations/{org_id}/search`                 | viewer       | Vector search, returns matching chunks |
| POST   | `/organizations/{org_id}/query`                  | viewer       | Retrieval-augmented answer + citations |

Every route under `/organizations/{org_id}` requires
`Authorization: Bearer <credential>` and membership of that organization; a
credential belonging to another organization is rejected with 403, and one
with too low a role is rejected with 403 as well. `POST /organizations`
returns the first key in `api_key` — the only time a token is ever readable.
Only its SHA-256 hash is stored, and passwords are hashed separately with
Argon2id.

Rotation is done by the key being rotated, authenticating as itself. It keeps
the role and cannot extend its own expiry, so a leaked key cannot rotate itself
into a longer-lived or more powerful one; an owner issues a new key for that.

Failed requests answer with `{"detail": {"error_code": ..., "message": ...}}`.
The code is stable and says which failure it was, because "processing failed"
for all of them is not something a caller can act on:

| Status | `error_code` | Means |
| --- | --- | --- |
| 409 | `DOCUMENT_BUSY` | Ingestion is in flight; the delete or reindex will not race it |
| 410 | `SOURCE_MISSING` | The row outlived its file. Upload it again; retrying will not help |
| 413 | `INPUT_TOO_LARGE` | Over the upload, page, chunk or storage limit |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | Not a PDF |
| 422 | `EXTRACTION_FAILED` | A PDF with no extractable text, usually a scan |
| 429 | `QUOTA_EXCEEDED` | Rate limit or per-organization quota, with `Retry-After` |
| 503 | `PROVIDER_UNAVAILABLE` | Embedding provider or vector store is down or timed out; retryable |

A `FAILED` document carries the same code on the status route, so the reason
survives the request that caused it.

Authentication failures are separate and answer `{"detail": "..."}`: 401 when
no usable bearer credential was presented, 403 when the credential is real but
revoked, expired, from another organization, or below the role the route needs.
Repeated login, invitation and password-change attempts are throttled per
account and per source address, and answer 429 with `Retry-After`.

`search` and `query` both accept `{"query": str, "limit": int}`, where `limit`
is 1 to 20 and defaults to 5. `query` caps the question at 2000 characters.

## Tests

From `backend/`:

```bash
python -m pytest
```

104 tests, a few seconds, no Docker and no network required. They run against
SQLite in memory with in-memory embedding, vector and LLM providers, but real
PDF parsing, real chunking and real on-disk storage.

The suite covers the whole upload-to-answer path, organization isolation, chunk
overlap, the source-citation regression that made `sources[].filename` always
null, the API-key lifecycle, the structured log line every request emits, and
the durable-job properties: retry after a crash, a reindex leaving no stale
vectors behind, and a delete leaving nothing in Postgres, on disk or in Qdrant.

Every test authenticates for real. There is no `dependency_overrides` entry
switching authorization off, because the one place a bug there would matter is
the one place it would then be invisible. `tests/test_identity.py` walks every
tenant route with nine credential states - absent, wrong scheme, malformed,
expired key, revoked key, key from another organization, expired session,
revoked session, session with no membership - and asserts each is refused,
then checks that a viewer credential cannot write and a member credential
cannot administer.

Two further tests need a real Postgres, because SQLite cannot prove row
locking or `SKIP LOCKED`. They skip unless you point them at one:

```bash
TEST_POSTGRES_URL=postgresql+psycopg://sentineth:sentineth_dev_password@localhost:5432/sentineth \
  python -m pytest tests/test_postgres_jobs.py
```

They assert that six workers claim six distinct jobs, that a held lease is not
stolen, and that query latency does not move while a deliberately blocked
ingestion runs.

The fakes in `tests/fakes.py` subclass the real provider interfaces, so if a
provider signature changes without its implementations following, the tests
fail rather than silently passing.

## Retrieval evaluation

The test suite proves the pipeline runs. It says nothing about whether the
right passage comes back. That is measured separately, in `backend/eval/`: a
22-document corpus and 134 questions in four kinds - span, identifier,
unanswerable and conflicting - scored as recall@k and MRR, with `compare.py`
for paired significance testing between two runs. It needs Qdrant and an
embedding provider, and `eval/README.md` explains what each number means and
which ones are not yet trustworthy.

## Environment variables

| Variable               | Required | Default                         | Notes                                        |
| ---------------------- | -------- | ------------------------------- | -------------------------------------------- |
| `DATABASE_URL`         | yes      | none                            | App refuses to start without it              |
| `QDRANT_URL`           | no       | `http://localhost:6333`         |                                              |
| `QDRANT_API_KEY`       | no       | none                            | Leave empty for local Docker                 |
| `OPENROUTER_API_KEY`   | yes      | none                            | Needed by `/query`; other routes work without |
| `OPENROUTER_BASE_URL`  | no       | `https://openrouter.ai/api/v1`  |                                              |
| `OPENROUTER_LLM_MODEL` | no       | `openrouter/free`               | Default is a placeholder, set a real slug    |
| `OPENAI_API_KEY`       | no       | none                            | Only for the unused OpenAI providers         |
| `NVIDIA_API_KEY`       | yes      | none                            | Needed by the default embedding provider     |
| `NVIDIA_BASE_URL`      | no       | `https://integrate.api.nvidia.com/v1` |                                        |
| `EMBEDDING_PROVIDER`   | no       | `nvidia`                        | `local` for offline work, no key needed      |
| `QDRANT_COLLECTION`    | no       | derived from the provider       | Set only during a reindex cutover            |
| `QDRANT_HYBRID`        | no       | `false`                         | Fixed when the collection is created         |
| `RERANK`               | no       | `false`                         | Loads a cross-encoder, costs latency per query |
| `SQL_ECHO`             | no       | `false`                         | Set `true` to log every SQL statement        |

Everything else is prefixed `SENTINETH_`, and all of it lands in one validated
`Settings` object in `app/settings.py` rather than in `os.getenv` calls spread
across the code. A bad value fails at startup, not at the first request that
happened to need it.

| Variable                              | Default        | Notes                                       |
| ------------------------------------- | -------------- | ------------------------------------------- |
| `SENTINETH_ENVIRONMENT`               | `development`  | `production` turns on the strict checks below |
| `SENTINETH_METRICS_TOKEN`             | none           | Bearer token for `/metrics`; required in production |
| `SENTINETH_SENTRY_DSN`                | none           | Error tracking off when empty               |
| `SENTINETH_ALLOWED_HOSTS`             | localhost      | JSON list; `*` is refused in production     |
| `SENTINETH_ALLOWED_ORIGINS`           | empty          | JSON list, for CORS                         |
| `SENTINETH_STORAGE_DIR`               | `backend/storage/documents` | Where uploaded files live      |
| `SENTINETH_SECRETS_DIR`               | none           | Directory of secret files, one per variable |
| `SENTINETH_SESSION_HOURS`             | 12             | Session lifetime                            |
| `SENTINETH_INVITATION_HOURS`          | 48             | Invitation lifetime                         |
| `SENTINETH_AUTH_ATTEMPTS_PER_MINUTE`  | 10             | Per account                                 |
| `SENTINETH_AUTH_IP_ATTEMPTS_PER_MINUTE` | 60           | Per source address                          |
| `SENTINETH_MAX_ORGANIZATIONS_PER_USER` | 5             | Organizations one account can own           |

Resource limits use the same prefix: `MAX_UPLOAD_BYTES` (25 MiB),
`MAX_DOCUMENTS_PER_ORG` (1000), `MAX_STORAGE_BYTES_PER_ORG` (1 GiB),
`UPLOADS_PER_MINUTE` (10), `QUERIES_PER_MINUTE` (60),
`PROVIDER_TIMEOUT_SECONDS` (30), `JOB_MAX_ATTEMPTS` (3),
`JOB_LEASE_SECONDS` (300). Every one has a default that works; set them when
a tenant needs a different one, not to get started.

With `SENTINETH_ENVIRONMENT=production` the service additionally refuses to
start on SQLite, on a metrics token under 32 characters, on `*` in the allowed
hosts or origins, on a plain-HTTP provider URL, on `LOG_LEVEL=DEBUG`, and on
`SQL_ECHO`. Each of those is a thing that is fine locally and a hole in
production, so the check is the environment, not the reviewer.

Leave `SQL_ECHO` off unless you are debugging: it logs document content into
your terminal and slows requests down.

## Running it as a service

`Dockerfile` builds one image that runs either process - `uvicorn app.main:app`
for the API, `python -m app.worker` for the worker - as a non-root user, with a
`HEALTHCHECK` that hits `/ready`. The default build installs no local model
weights, because the default embedding provider is hosted; `--build-arg
WITH_LOCAL_MODELS=true` adds torch and MiniLM for offline use.

```bash
docker compose -f docker-compose.yml -f compose.app.yml up -d --build
```

`deploy/compose.production.yml` is the same image with the settings a real
deployment needs: secrets read from files rather than the environment, a
read-only root filesystem, dropped capabilities, and the API bound to
localhost for a reverse proxy to terminate TLS in front of.

`/live` says the process is up. `/ready` asks Postgres for its schema
revision, asks Qdrant about the collection's dimension and hybrid config, and
checks the storage directory is writable - it answers 503 when any of those is
wrong, which is what a load balancer should act on. `/metrics` exposes request
counts and latency, retrieval hit rate, job states and queue age; per-organization
LLM cost is on `/organizations/{org_id}/usage` instead, where it is scoped to a
tenant rather than to the process.

## Backups and restore

`python -m app.backup backup DIR --quiesced` takes a `pg_dump`, copies every
document's source file, checksums each one against `documents.content_hash`,
and writes a manifest. `verify` re-checks a backup without touching anything;
`restore` refuses to run into a database or storage directory that is not
empty, and revokes every session, key and invitation on the way in, because a
restored backup is a snapshot of credentials someone may have rotated since.

Qdrant is not in that path by default, on purpose: vectors are derived from
Postgres and the source files, so the recovery procedure rebuilds them with
`scripts/reindex.py` rather than restoring them. `scripts/rehearse_restore.py`
runs that whole procedure against disposable databases and collections, and CI
runs it on every push, so the restore path is exercised continuously rather
than first attempted during an incident. `docs/OPERATIONS.md` is the written
procedure.

## Layout

```
sentineth/
  info.md                    architecture, conventions, and rules for changes
  docker-compose.yml         Postgres + Qdrant
  compose.app.yml            API and worker on top of those
  Dockerfile                 one image, two commands
  deploy/                    production compose file and env template
  .env.example               template for .env
  docs/ROADMAP.md            what gets built next, in order
  docs/OPERATIONS.md         deploy, backup, restore, incident procedures
  backend/
    alembic/                 migrations
    app/
      main.py                app wiring, middleware, routers
      worker.py              claims and runs ingestion jobs
      settings.py            validated settings and resource limits
      security.py            authentication and role checks
      audit.py               append-only audit trail
      health.py              /live, /ready
      observability.py       metrics, usage recording
      admin.py               operator CLI: users, owners, passwords
      backup.py              backup, verify, restore
      relocate_storage.py    move the storage directory safely
      body_limit.py          ASGI request body cap
      dependencies.py        cached provider factories
      schemas.py             request/response models
      identity_schemas.py    auth, membership, audit models
      api/auth.py            signup, login, logout, invitations
      api/organizations.py   organizations, members, keys, audit, usage
      api/documents.py       upload, status, list, delete, reindex, search, query
      services/              document, chunking, retrieval, query
      providers/             embeddings, llm, vector, storage adapters
      db/                    engine, session, models
    eval/                    retrieval question set and harness
    scripts/reindex.py       rebuild vectors into a new collection
    scripts/rehearse_restore.py  end-to-end recovery drill, run in CI
    tests/                   pytest suite
    storage/documents/       uploaded files (gitignored)
```

## Working on this

### Adding a migration

Change `app/db/models.py`, then from `backend/`:

```bash
alembic revision --autogenerate -m "describe the change"
```

Read the generated file before applying it. Autogenerate misses some changes,
notably server defaults and column type widening. Then:

```bash
alembic upgrade head
```

To confirm the migrations and the models still agree:

```bash
alembic check
```

"No new upgrade operations detected" means they match.

### Changing the embedding model

The vector dimension is part of the stored data. `get_vector_store()` reads it
from the active embedding provider so the two cannot drift, but an existing
Qdrant collection is not migrated automatically. Switching to a model with a
different dimension means recreating the collection and re-embedding every
document. See `info.md` for the procedure.

### Regenerating requirements.txt

Do not run `pip freeze > requirements.txt` in PowerShell. PowerShell's `>`
writes UTF-16, which pip cannot read, and the failure looks like a corrupt file
rather than an encoding problem. Use:

```bash
pip freeze | Out-File -Encoding utf8 requirements.txt
```

or run the plain redirect from bash or cmd instead.

### Keeping Qdrant versions in step

`qdrant-client` in `requirements.txt` and the `qdrant/qdrant` image tag in
`docker-compose.yml` are pinned to the same minor version on purpose. A
mismatch produces a compatibility warning at startup and, across larger gaps,
real API differences. Bump both together.

## Not built yet

What gets built next, and in what order, is in `docs/ROADMAP.md`.

- Frontend. There is none.
- File types other than PDF. Other uploads are rejected with a 415.
- Email delivery. Invitations return a token in the API response; sending it
  to the invited person is currently your job.
- Password reset by the account holder. An operator resets passwords with
  `python -m app.admin reset-password`; there is no self-service flow.
- Cross-organization search. A query answers from one tenant's documents.
