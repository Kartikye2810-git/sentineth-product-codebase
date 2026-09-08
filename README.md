# Sentineth AI

Organizational intelligence platform. Upload your company's documents, then ask
questions in plain language and get answers grounded in those documents, with
citations back to the source file.

Every document, vector and answer is scoped to an organization. One tenant
cannot read another tenant's data.

Status: pre-alpha. The RAG pipeline works end to end, ingestion runs in a
separate worker process, and uploads, queries and per-organization storage are
all bounded. Every document route requires an organization API key, but anyone
who can reach the service can still create an organization, unauthenticated and
unthrottled, so do not expose this to the internet yet.

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
`document_chunks`, `ingestion_jobs` and `organization_rate_limits`.

### 5. Start the API

```bash
python -m uvicorn app.main:app --reload
```

Interactive docs: http://127.0.0.1:8000/docs

### 6. Start the worker

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

Create an organization:

```bash
curl -X POST http://127.0.0.1:8000/organizations -H "Content-Type: application/json" -d "{\"name\": \"Acme Inc\"}"
```

Copy the `id` and the `api_key` from the response. Everything below uses
them as `ORG_ID` and `ORG_KEY`; without the key every route below answers
401.

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

## API reference

| Method | Path                                             | Purpose                                 |
| ------ | ------------------------------------------------ | --------------------------------------- |
| GET    | `/`                                              | Service name and version                |
| GET    | `/health`                                        | Liveness check                          |
| POST   | `/organizations`                                 | Create an organization, returns its key |
| GET    | `/organizations/{org_id}/api-keys`               | Key metadata, never the token itself    |
| POST   | `/organizations/{org_id}/api-keys/rotate`        | Issue a replacement, revoke the old key |
| DELETE | `/organizations/{org_id}/api-keys/{key_id}`      | Revoke one key                          |
| GET    | `/organizations/{org_id}/documents`              | List documents, paginated               |
| GET    | `/organizations/{org_id}/documents/{doc_id}`     | Document status, for polling after 202  |
| POST   | `/organizations/{org_id}/documents`              | Upload a PDF (multipart), 202 + queue   |
| DELETE | `/organizations/{org_id}/documents/{doc_id}`     | Queue deletion of row, file and vectors, 202 |
| POST   | `/organizations/{org_id}/documents/{doc_id}/reindex` | Queue a re-chunk and re-embed, 202 |
| POST   | `/organizations/{org_id}/search`                 | Vector search, returns matching chunks  |
| POST   | `/organizations/{org_id}/query`                  | Retrieval-augmented answer + citations  |

Every route under `/organizations/{org_id}` requires
`Authorization: Bearer <key>` for that organization; a key belonging to
another organization is rejected with 403. `POST /organizations` is the one
exception, and returns the first key in `api_key` — the only time a token is
ever readable. Only its SHA-256 hash is stored.

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

`search` and `query` both accept `{"query": str, "limit": int}`, where `limit`
is 1 to 20 and defaults to 5. `query` caps the question at 2000 characters.

## Tests

From `backend/`:

```bash
python -m pytest
```

82 tests, well under a second, no Docker and no network required. They run
against SQLite in memory with in-memory embedding, vector and LLM providers,
but real PDF parsing, real chunking and real on-disk storage.

The suite covers the whole upload-to-answer path, organization isolation, chunk
overlap, the source-citation regression that made `sources[].filename` always
null, the API-key lifecycle, the structured log line every request emits, and
the durable-job properties: retry after a crash, a reindex leaving no stale
vectors behind, a delete leaving nothing in Postgres, on disk or in Qdrant, and
every document route rejecting a key from another organization.

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

Resource limits are separate, all prefixed `SENTINETH_` and validated at
startup in `app/settings.py`: `MAX_UPLOAD_BYTES` (25 MiB),
`MAX_DOCUMENTS_PER_ORG` (1000), `MAX_STORAGE_BYTES_PER_ORG` (1 GiB),
`UPLOADS_PER_MINUTE` (10), `QUERIES_PER_MINUTE` (60),
`PROVIDER_TIMEOUT_SECONDS` (30), `JOB_MAX_ATTEMPTS` (3),
`JOB_LEASE_SECONDS` (300). Every one has a default that works; set them when
a tenant needs a different one, not to get started.

Leave `SQL_ECHO` off unless you are debugging: it logs document content into
your terminal and slows requests down.

## Layout

```
sentineth/
  info.md                    architecture, conventions, and rules for changes
  docker-compose.yml         Postgres + Qdrant
  .env.example               template for .env
  docs/ROADMAP.md            what gets built next, in order
  backend/
    alembic/                 migrations
    app/
      main.py                app, health, organizations
      worker.py              claims and runs ingestion jobs
      settings.py            validated resource limits
      body_limit.py          ASGI request body cap
      dependencies.py        cached provider factories
      schemas.py             request/response models
      api/documents.py       upload, status, list, delete, reindex, search, query
      services/              document, chunking, retrieval, query
      providers/             embeddings, llm, vector, storage adapters
      db/                    engine, session, models
    eval/                    retrieval question set and harness
    scripts/reindex.py       rebuild vectors into a new collection
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

- User accounts, roles and permissions. A key authenticates an organization,
  not a person, so there is no way to say who did what or to give one user
  less access than another.
- Anything in front of `POST /organizations`. Organization creation is
  unauthenticated and unthrottled, which is the top blocker before any
  deployment.
- Frontend. There is none.
- File types other than PDF. Other uploads are rejected with a 415.
