# Phase 4 operations guide

Sentineth is an API with separate durable ingestion and knowledge workers. Nemotron remains
the default embedding model; OpenRouter generates answers. MiniLM remains an
explicit offline configuration. The configured answer LLM also performs the
second-pass extraction, but its claims remain proposals until a human reviews them.

There is no public deployment yet. The founder has no hosting server or domain;
this package can be verified locally and deployed when that destination exists.

## Accounts and access

Accounts are invitation-only. An operator creates the first account from the
server console. Passwords are entered at a hidden prompt, never as arguments.
From `backend/`, with the environment configured:

```bash
alembic upgrade head
python -m app.admin create-user --email owner@example.com
python -m app.admin initialize-vectors
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# In a separate process:
python -m app.worker
# In another process:
python -m app.knowledge_worker
```

For a Phase 2 organization, supply `--organization <UUID>` when creating its
first human owner. To attach an existing account to a legacy organization that
has no human owner, use `python -m app.admin attach-owner --email owner@example.com
--organization <UUID>`. This operation refuses organizations that already have
an owner. Existing API keys are preserved as `owner` keys by the additive
migration. They cannot manage human membership; adopt existing organizations
before onboarding colleagues.

Open `/docs`. Call `POST /auth/login` with email and password, then paste the
returned `access_token` into Swagger's Authorize field. `POST /organizations`
now requires this human session and limits each account to five owned
organizations by default. `GET /organizations` lists the caller's memberships.
Passwords use Argon2id; only hashes of session tokens, invitations and API keys
are stored. Human sessions expire after 12 hours by default.

| Action | Viewer | Member | Owner |
| --- | --- | --- | --- |
| List/read documents, search, ask questions | Yes | Yes | Yes |
| Upload, delete, reindex | No | Yes | Yes |
| Read entities, relationships and extraction proposals | Yes | Yes | Yes |
| Review proposals; create or correct knowledge | No | Human only | Human only |
| Create/list/revoke API keys, audit history, usage | No | No | Yes |
| Invite people, list/edit/remove members | No | No | Human owner only |
| Rotate the authenticating API key | Yes | Yes | Yes |

Rotation preserves the key's role and cannot extend an existing expiration.
New integration keys default to `member`; choose `viewer` for read access.
Key secrets are returned only on creation/rotation. Keep them in a secret
manager. A human session and a machine key both use `Authorization: Bearer`.
No authentication cookies are set. Do not place tokens in URLs or logs.

Owners use `POST /organizations/{id}/invitations` with email and role. Deliver
the returned one-time token privately to that person; this version does not
send email. The recipient calls `POST /auth/accept-invitation` with token,
matching email and a 12–128 character password. Existing accounts must supply
their current password. Invitations expire after 48 hours and can be revoked.
Acceptance cannot change an existing member's role. Membership changes take
effect on subsequent requests; the last human owner cannot be removed or
demoted. Demoting/removing an owner revokes their pending invitations.

`POST /auth/logout` revokes the current session. `POST /auth/change-password`
revokes all of that user's sessions. Operator recovery is `python -m app.admin
reset-password --email owner@example.com`; it also revokes all sessions and
re-enables an account disabled during disaster recovery. There is no public
self-service password recovery or MFA/SSO yet.

## Configuration and secrets

`app/settings.py` is the single validated settings source, shared by API,
worker, migrations and maintenance scripts. Environment variables override the
root `.env`; secret files are also supported with `SENTINETH_SECRETS_DIR`.
Files use the environment variable names (for example `DATABASE_URL`,
`NVIDIA_API_KEY`, `SENTINETH_METRICS_TOKEN`). Environment/`.env` values take
precedence over secret files: do not leave placeholder secrets in either.

`SENTINETH_ENVIRONMENT=production` requires PostgreSQL, the selected provider
keys, explicit allowed hosts/origins, HTTPS for external AI provider URLs, a
metrics token of at least 32 characters, and non-debug logging. Provider and
resource settings fail validation before serving requests. Missing databases,
schema revisions, collection configurations and storage permissions are
reported by readiness. Readiness does not call paid AI services.

The authentication throttle is shared across processes in PostgreSQL. Defaults
are 10 attempts/minute per account and 60/minute per socket peer. Caller-supplied
forwarded IP headers are ignored. Behind a reverse proxy the peer limit applies
to the proxy; tune `SENTINETH_AUTH_IP_ATTEMPTS_PER_MINUTE` for the deployment and
add per-client limits at the trusted proxy. Do not enable unrestricted proxy
header trust.

## Build and deploy

```bash
docker build -t sentineth:phase4 .
```

The default image contains the hosted-model dependencies, runs as UID 10001,
and does not contain `.env`, source documents, model caches or Git history.
For MiniLM or the optional local reranker, build with
`--build-arg WITH_LOCAL_MODELS=true`. No silent model fallback occurs.

Local Compose can reuse the existing database services:

1. Back up the existing database and document directory before upgrading.
2. Stop API/worker writers and apply `alembic upgrade head` from the host.
3. Set `CONTAINER_DATABASE_URL` to the same PostgreSQL credentials/database
   using hostname `postgres` instead of `localhost`.
4. Document rows contain absolute paths. Before moving a corpus from the host
   into containers, relocate its paths to `/data/documents` using the procedure
   below. Verify the bind mount is writable by UID 10001.
5. Run `docker compose -f docker-compose.yml -f compose.app.yml run --rm api
   python -m app.admin initialize-vectors`, then start the API and both workers with
   `docker compose -f docker-compose.yml -f compose.app.yml up -d`.
6. Visit `http://127.0.0.1:8000/docs`, log in, upload a PDF, poll its `Location`
   until `READY`, let both workers finish, review proposals under
   `/organizations/{id}/knowledge`, and ask a question with source provenance.

For a fresh public deployment use `deploy/compose.production.yml` with external
private PostgreSQL/Qdrant, a reviewed image tag in `SENTINETH_IMAGE`, a copy of
`deploy/production.env.example` as `deploy/production.env`, and secret files in
`deploy/secrets`. Bind-mounted Compose secrets must be readable by the container
user while the parent secret directory stays private on the host. The production
file exposes the API only on localhost. Put a TLS reverse proxy in front of it,
set the real hostname, and keep PostgreSQL/Qdrant off the public internet.
The local development Compose database credentials are not production credentials.

Use different database roles for migrations and runtime. The runtime role needs
SELECT/INSERT/UPDATE/DELETE on application tables, SELECT on `alembic_version`,
and schema USAGE. Revoke UPDATE/DELETE/TRUNCATE on `audit_events`; grant only
SELECT/INSERT there. Never grant runtime schema ownership, CREATE, superuser,
or migration permissions. Reapply grants after restoring a backup (`--no-acl`
is intentional). The audit trigger rejects bulk UPDATE/DELETE/TRUNCATE, but a
schema administrator can remove that trigger.

Deploy one API process per container so its Prometheus counters have a clear
process boundary. Run the ingestion and knowledge workers separately. Apply
migrations as a release step
with migration credentials before starting the new version, not independently
in every replica. The included worker has restart behavior, but no separate
heartbeat probe yet; watch queue age and document failures.

## Health, metrics, and investigation

- `/live`: process liveness, no downstream checks.
- `/ready` and `/health`: return 503 unless PostgreSQL is reachable at the
  expected migration, the Qdrant collection matches dimension/hybrid settings,
  and the document storage directory is accessible. Initialize a fresh
  collection with the admin command; readiness never creates one.
- `/metrics`: Prometheus text, requiring its own bearer secret. Metrics use
  route templates rather than document IDs or arbitrary URLs as labels.
- `/organizations/{id}/usage`: owner-only totals by model, including prompt and
  completion tokens and provider-reported USD costs. Unknown cost is `null`,
  never presented as zero; `requests_with_reported_cost` indicates coverage.
  These are observed completed provider responses, not a billing ledger:
  calls whose connection drops before usage arrives may still be billed.
- `sentineth_knowledge_jobs{state=...}` reports the durable extraction queue.
- `/organizations/{id}/audit-events`: owner-only history with actor, resource,
  UTC timestamp and request ID. Supports `limit` (maximum 100) and `offset`.

Suggested initial alerts: readiness failing for two minutes; increasing HTTP
5xx rate; query p95 latency above the chosen service objective; available queue
age over five minutes; increasing failed-document events; or retrieval hit rate
falling below the measured baseline. Ingest throughput comes from the rate of
`sentineth_document_events{action="document.indexed"}`. HTTP/retrieval counters
reset on API restart; job counts and document event totals come from PostgreSQL.

To investigate a document, find its upload/reindex/delete audit event, follow
the request ID into structured API/worker logs, then inspect its status and
worker outcome event. Events commit alongside mutations; document history
survives deletion. Queries record counts, never question text or retrieved
content. Logs carry authenticated actor/organization IDs. Worker jobs preserve
the originating request ID across retries. Production exception logs omit
exception values; validation responses omit raw inputs, including passwords.

Optional `SENTINETH_SENTRY_DSN` enables error tracking. The error hook strips
request bodies, headers, breadcrumbs, user data, exception values and frame
locals. Request IDs correlate errors with local logs. No external error tracker
is configured until a DSN is supplied. See the source before expanding telemetry;
customer document text must not become observability data.

## Backup policy

Initial operational target: a nightly encrypted off-host backup, retaining
seven daily and four weekly copies; target RPO 24 hours. Take an additional
backup before each migration. These are policy targets, not an already configured
scheduler or measured large-corpus RTO. Provision the backup destination and job
when choosing hosting. The bundled tool creates a protected local directory;
encryption, off-host upload, retention and restore alerts belong in that job.

Stop the API and workers for a consistent capture of PostgreSQL and files.
The tool refuses a currently processing job, but `--quiesced` is an operator
assertion that writers really are stopped. A manifest is written last, with
SHA-256 checksums; missing manifest means an incomplete backup.

```bash
# From backend, using PostgreSQL 17 pg_dump/pg_restore on PATH:
python -m app.backup backup /secure/backups/run-001 --quiesced --snapshot
python -m app.backup verify /secure/backups/run-001
# For the local development PostgreSQL container, append:
# --postgres-container sentineth-postgres
```

A backup contains a PostgreSQL custom dump (including users, keys, audit and
chunks), original document files, configuration metadata, and optionally a
Qdrant collection snapshot. Credentials are never passed in process arguments.
Backups contain sensitive hashes and company data; treat them as secrets.
Qdrant also retains the generated snapshot server-side: remove old snapshots
according to the same retention policy after confirming off-host copies.
Snapshot export supports this single-node deployment; distributed Qdrant
requires per-node snapshots ([Qdrant snapshot documentation](https://qdrant.tech/documentation/snapshots/)).

## Restore and rebuild

Only restore trusted backups into a NEW empty database and empty storage
location. `pg_restore` runs database code from the dump. Use migration/admin
credentials for recovery. Never run a restore against a serving database.

1. Stop all writers. Set `DATABASE_URL` to the new empty recovery database and
   `SENTINETH_STORAGE_DIR` to the new storage location.
2. Verify the backup, then run:

   ```bash
   python -m app.backup restore /secure/backups/run-001 --quiesced
   alembic upgrade head
   alembic check
   ```

   The tool checks every source checksum, restores transactionally, and remaps
   stored document paths. It revokes all restored API keys, sessions and
   invitations, and disables accounts. This prevents old backups reactivating
   credentials revoked after the backup. Keep the service offline on any error;
   retry in another fresh database/directory.

3. Recover the designated owner with `python -m app.admin reset-password --email
   owner@example.com`, verify their membership, and reapply runtime database
   grants. Recover other accounts and issue new API keys/invitations explicitly.
4. Keep writers stopped while rebuilding a **new** vector collection:

   ```bash
   python scripts/reindex.py --provider nvidia --collection recovery_2026_09
   python scripts/reindex.py --provider nvidia --collection recovery_2026_09 --verify-only
   ```

   Match the backup's provider and hybrid setting. Set `QDRANT_COLLECTION` to
   the verified replacement only after rebuilding. Do not rebuild into the
   active or an existing nonempty collection: stale vectors can outlive SQL
   records. This path regenerates vectors from READY chunks in PostgreSQL.
   Qdrant snapshot restoration is an optional shortcut; the SQL/source rebuild
   remains the recovery authority.
5. If extracted chunks are missing or need to be regenerated, start the service
   against an empty replacement collection, temporarily restrict access to the
   recovery owner, and reindex documents through the existing reindex endpoint
   and worker. Respect job/rate limits and poll each document to `READY`; failed
   sources must be repaired before opening access. This reconstructs the index
   from original PDFs. Existing queued/deletion jobs are retained by backup;
   allow those to reach a terminal state before accepting normal traffic.
6. Verify document/chunk/point counts, search for a known fact with each tenant's
   credentials, confirm a cited answer, check denial with another tenant's key,
   and inspect `/ready`, audit history, usage and worker logs. Only then reopen
   the reverse proxy to normal traffic. Retain the old database/storage/index
   until recovery acceptance is complete.

For an existing host corpus that is only changing its container path, run
`python -m app.relocate_storage /data/documents --quiesced` while the host's
`SENTINETH_STORAGE_DIR` still identifies the current source root. It verifies
all existing source hashes before rewriting paths in one transaction. The new
root must then be mounted with the same relative file layout; this command
rewrites metadata and does not copy files. Keep writers stopped until the
mount is verified from the container.

## Rehearse recovery

```bash
# Real PostgreSQL + Qdrant, deterministic test embeddings, no AI service calls:
python scripts/rehearse_restore.py --postgres-container sentineth-postgres
# Synthetic policy only, real NVIDIA embeddings and a live container/LLM check:
python scripts/rehearse_restore.py --postgres-container sentineth-postgres \
  --live-provider --image sentineth:phase4
```

The script creates uniquely named disposable databases and collections, tests
migration of a legacy key, proves SQL audit immutability, indexes a synthetic
PDF, captures a snapshot, backs up and restores into fresh locations, rebuilds
from chunks, deletes the restored chunks, rebuilds again from the PDF, and
checks retrieval. The optional container check verifies login, readiness and a
cited answer. Test databases/collections/containers are removed in `finally`;
local synthetic backup files and a result report remain for inspection.

Run this in CI and after changing schema, storage layout, providers or recovery
code. A tiny synthetic rehearsal proves the procedure, not production restore
time. Measure RTO on a representative encrypted backup before promising it.
