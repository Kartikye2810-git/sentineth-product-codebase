# Sentineth AI

Organizational intelligence platform. Upload your company's documents, then ask
questions in plain language and get answers grounded in those documents, with
citations back to the source file.

Every document, vector and answer is scoped to an organization. One tenant
cannot read another tenant's data.

Status: pre-alpha. The RAG pipeline works end to end. Every document
route requires an organization API key, but anyone who can reach the service
can create an organization and there is no rate limiting, so do not expose
this to the internet yet.

## How it works

```
PDF upload
  -> extract text (pypdf)
  -> chunk (4000 chars, 500 overlap)
  -> embed (all-MiniLM-L6-v2, 384 dims, local)
  -> index in Qdrant, payload stamped with organization_id
                                |
question                        |
  -> embed the question         |
  -> vector search, filtered by organization_id  <----+
  -> assemble the retrieved chunks into a prompt
  -> LLM (OpenRouter) answers using only that context
  -> answer + citations
```

Postgres holds documents and chunk metadata. Qdrant holds the vectors. Files
land on local disk under `backend/storage/documents/`.

Providers (embeddings, LLM, vector store, file storage) sit behind interfaces in
`backend/app/providers/`, so any one of them can be swapped without touching the
services. See `AGENTS.md` for the full architecture and the rules that changes
to it must follow.

## Stack

| Layer      | Choice                                        |
| ---------- | --------------------------------------------- |
| API        | FastAPI, Uvicorn                              |
| Database   | PostgreSQL 17, SQLAlchemy 2.0, Alembic         |
| Vectors    | Qdrant 1.19                                   |
| Embeddings | sentence-transformers `all-MiniLM-L6-v2` (CPU) |
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
```

The username, password and database name in `DATABASE_URL` must match the
`environment` block in `docker-compose.yml`.

`OPENROUTER_LLM_MODEL` must be a real model slug from
https://openrouter.ai/models. The value shipped in `.env.example` is a
placeholder and will fail with a model-not-found error.

### 4. Run the migrations

From `backend/`:

```bash
alembic upgrade head
```

This creates `organizations`, `documents` and `document_chunks`.

### 5. Start the API

```bash
python -m uvicorn app.main:app --reload
```

Interactive docs: http://127.0.0.1:8000/docs

The first request that needs embeddings downloads the MiniLM weights (about
90 MB) into the Hugging Face cache and takes 30 to 60 seconds. Every request
after that reuses the loaded model, because providers are cached for the
lifetime of the process in `app/dependencies.py`.

## Try it end to end

Create an organization:

```bash
curl -X POST http://127.0.0.1:8000/organizations -H "Content-Type: application/json" -d "{\"name\": \"Acme Inc\"}"
```

Copy the `id` from the response. Everything below uses it as `ORG_ID`.

Upload a PDF:

```bash
curl -X POST http://127.0.0.1:8000/organizations/ORG_ID/documents -F "file=@/path/to/your.pdf"
```

A successful response reports `"status": "READY"` and the number of chunks
indexed.

Ask a question:

```bash
curl -X POST http://127.0.0.1:8000/organizations/ORG_ID/query -H "Content-Type: application/json" -d "{\"query\": \"What is the revenue target for Q3?\"}"
```

You get an `answer` plus a `sources` array naming the file and chunk each claim
came from. If nothing relevant is indexed, the answer says so rather than
guessing.

Search without generating an answer:

```bash
curl -X POST http://127.0.0.1:8000/organizations/ORG_ID/search -H "Content-Type: application/json" -d "{\"query\": \"revenue target\", \"limit\": 5}"
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
| POST   | `/organizations/{org_id}/documents`              | Upload and index a PDF (multipart)      |
| DELETE | `/organizations/{org_id}/documents/{doc_id}`     | Delete the row, the file and its vectors |
| POST   | `/organizations/{org_id}/documents/{doc_id}/reindex` | Re-chunk and re-embed one document  |
| POST   | `/organizations/{org_id}/search`                 | Vector search, returns matching chunks  |
| POST   | `/organizations/{org_id}/query`                  | Retrieval-augmented answer + citations  |

Every route under `/organizations/{org_id}` requires
`Authorization: Bearer <key>` for that organization; a key belonging to
another organization is rejected with 403. `POST /organizations` is the one
exception, and returns the first key in `api_key` — the only time a token is
ever readable. Only its SHA-256 hash is stored.

Failed requests answer with `{"detail": {"error_code": ..., "message": ...}}`:
415 for a non-PDF upload, 422 when text cannot be extracted, 503 when the
embedding provider or vector store is unreachable.

`search` and `query` both accept `{"query": str, "limit": int}`, where `limit`
is 1 to 20 and defaults to 5. `query` caps the question at 2000 characters.

## Tests

From `backend/`:

```bash
python -m pytest
```

38 tests, well under a second, no Docker and no network required. They run
against SQLite in memory with in-memory embedding, vector and LLM providers,
but real PDF parsing, real chunking and real on-disk storage.

The suite covers the whole upload-to-answer path, organization isolation, chunk
overlap, the source-citation regression that made `sources[].filename` always
null, the API-key lifecycle, and the structured log line every request emits.

The fakes in `tests/fakes.py` subclass the real provider interfaces, so if a
provider signature changes without its implementations following, the tests
fail rather than silently passing.

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
| `SQL_ECHO`             | no       | `false`                         | Set `true` to log every SQL statement        |

Leave `SQL_ECHO` off unless you are debugging: it logs document content into
your terminal and slows requests down.

## Layout

```
sentineth/
  AGENTS.md                  architecture, conventions, and rules for changes
  docker-compose.yml         Postgres + Qdrant
  .env.example               template for .env
  backend/
    alembic/                 migrations
    app/
      main.py                app, health, organizations
      dependencies.py        cached provider factories
      schemas.py             request/response models
      api/documents.py       upload, list, delete, reindex, search, query
      services/              document, chunking, retrieval, query
      providers/             embeddings, llm, vector, storage adapters
      db/                    engine, session, models
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
document. See `AGENTS.md` for the procedure.

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
- Background processing. Upload is synchronous, so a large PDF holds the request
  open until indexing finishes.
