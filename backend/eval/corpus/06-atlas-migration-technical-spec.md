# Technical Specification: Project Atlas Data Migration

Document owner: Amara Diallo, Staff Engineer
Reviewers: Nadia Fernsby, Marcus Bellweather, Rosalind Achterberg
Status: Approved for implementation
Last revised: 22 May 2026

## 1. Problem statement

The customer records currently live in a single PostgreSQL database named
legacy_core, in a schema that has accumulated eleven years of change. The
schema encodes an assumption that each customer belongs to exactly one
organisation, which the enterprise tier has already broken through a series
of workarounds involving duplicate customer rows.

Project Atlas migrates customer records into a new schema in which the
relationship between a customer and an organisation is explicit and
many-to-many, and in which organisation membership carries a role and a
validity period.

## 2. Goals and non-goals

The migration must complete with zero data loss and no more than fifteen
minutes of write downtime. Read availability must be continuous.

It is not a goal to change any external API contract in this project. The
existing endpoints must continue to behave identically, including their
current pagination quirks, which are relied upon by at least two integrators.

It is not a goal to migrate billing records. Billing remains in legacy_core
and will be addressed by a separate project in 2027.

## 3. Target schema

Three new tables are introduced. The customers table holds identity
attributes only: identifier, primary email, display name, creation timestamp,
and soft deletion timestamp. The organisations table holds organisation
identity and tier. The memberships table joins the two, carrying a role
enumeration, a valid_from timestamp, and a nullable valid_until timestamp.

A customer with no active membership row is valid and represents a
self-service user. The application must not assume the presence of a
membership.

All three tables use UUID version seven identifiers rather than sequential
integers, so that identifiers are sortable by creation time without leaking
a row count. Legacy integer identifiers are preserved in a legacy_id column
for the duration of the migration and for twelve months afterwards.

## 4. Migration strategy

The migration uses a dual-write and backfill approach in five phases.

Phase one deploys the new schema alongside the old, with no traffic. The new
tables are empty. This phase is reversible by dropping the tables.

Phase two enables dual writes. Every write to legacy_core is mirrored to the
new schema inside the same transaction. A mirroring failure fails the whole
transaction, so the two schemas cannot diverge. This phase is reversible by
disabling the mirror.

Phase three runs the backfill for historical rows, in batches of five
thousand, ordered by legacy identifier ascending. The backfill is idempotent
and resumable, and is expected to take between nine and fourteen hours for
the estimated 31 million customer rows. The backfill runs at a rate limit of
two thousand rows per second to protect the replication lag budget.

Phase four switches reads to the new schema behind a per-organisation feature
flag, beginning with internal organisations, then one per cent of customers,
then ten, then fifty, then one hundred. Each step holds for a minimum of
twenty-four hours. This phase is reversible by flipping the flag.

Phase five stops dual writes and makes the new schema authoritative. This is
the first irreversible step, and it is gated on a manual sign-off from the
document owner and the on-call incident commander.

## 5. Consistency verification

A continuous reconciliation job compares row counts and a checksum of
material fields between the two schemas every fifteen minutes throughout
phases two, three and four. Any discrepancy raises a severity two alert and
automatically pauses the backfill.

The checksum covers email, display name, deletion state, and the set of
organisation identifiers. It deliberately excludes updated_at, which differs
by design because the mirrored write records its own timestamp.

Before phase five, a full reconciliation over all rows must complete with
zero discrepancies twice in succession, at least twenty-four hours apart.

## 6. Rollback

Phases one through four are reversible within five minutes by flag flip or
mirror disable, with no data loss, because legacy_core remains authoritative
throughout. After phase five, rollback requires a reverse backfill, estimated
at six hours, and is expected to be used only in the event of a defect
discovered within the first week.

The reverse backfill script is written and tested as part of phase four
acceptance. A rollback path that has not been executed against production-like
data is not considered to exist.

## 7. Performance expectations

The dual-write mirror adds an estimated 1.8 milliseconds to the p50 write
path and 6.2 milliseconds to the p99, measured in staging against a
production-shaped dataset. This is within the twelve millisecond budget
agreed with the API team.

The new membership join adds one index lookup to the customer read path. The
p99 read latency is expected to increase from 14 milliseconds to 17
milliseconds, which remains inside the 25 millisecond service level objective.

## 8. Open questions

Whether the legacy_id column should be dropped after twelve months or
retained indefinitely for support purposes is not yet decided. The document
owner will bring a recommendation to the architecture review in September.
