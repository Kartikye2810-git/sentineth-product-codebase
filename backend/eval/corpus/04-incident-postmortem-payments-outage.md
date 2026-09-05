# Incident Postmortem: Payments API Outage

Incident ID: INC-2026-0417
Severity: One
Date of incident: 17 April 2026
Duration: 3 hours 42 minutes
Incident commander: Rosalind Achterberg
Author: Tobias Lindqvist
Status: Closed, actions tracked

## Summary

Between 09:14 and 12:56 UTC on 17 April 2026, the payments API returned
error code ERR-5031 to approximately sixty-eight per cent of requests. The
immediate cause was connection pool exhaustion in the payments service
following a schema migration that added an unindexed foreign key constraint
to the transactions table. Approximately 41,000 payment attempts failed. No
funds were lost, no data was corrupted, and no customer data was exposed.

## Impact

Four hundred and twelve merchant accounts were affected. The two largest
merchants by volume, Halloway Retail Group and Corvid Logistics, accounted
for just under half of the failed transactions. Customer Support received
one hundred and ninety-three tickets, tracked under the parent ticket
SUP-11482.

Estimated direct revenue impact was GBP 74,000 in fees not collected.
Estimated contractual service credit liability under the standard merchant
agreement is GBP 138,000, of which GBP 91,000 has been credited to date.

The status page was updated at 09:31, seventeen minutes after the first alert.
This exceeded our published commitment of updating within ten minutes.

## Timeline

09:02 UTC. Migration 20260417_add_settlement_fk is applied to the production
transactions table by the automated deployment pipeline. The migration
acquires a brief lock and completes in eleven seconds. No alert fires.

09:14 UTC. Median payments API latency rises from 82 milliseconds to 4.1
seconds. The latency alert fires to the payments on-call, Tobias Lindqvist.

09:19 UTC. On-call acknowledges. Initial hypothesis is an upstream card
network degradation, based on a similar signature in incident INC-2025-1130.

09:26 UTC. Error rate crosses fifty per cent. Incident is declared at
severity two. Rosalind Achterberg assumes incident commander.

09:31 UTC. Status page updated to "degraded performance".

09:44 UTC. Card network hypothesis is ruled out; the acquirer confirms normal
processing and our own synthetic probes to the acquirer succeed.

10:05 UTC. Incident raised to severity one after the error rate exceeds sixty
per cent and the first merchant escalation arrives.

10:38 UTC. An engineer notices that the connection pool saturation metric has
been at one hundred per cent since 09:14 and correlates the start with the
morning deployment window.

11:02 UTC. The migration is identified as the probable cause. Query plans
show a sequential scan on the transactions table for every settlement insert,
because the new foreign key had no supporting index.

11:20 UTC. Decision taken to create the missing index concurrently rather
than roll back the migration, on the basis that rolling back would require a
second exclusive lock on the same table.

11:47 UTC. Index creation begins. Because the operation is concurrent it does
not block writes, but it takes forty-one minutes on a table of this size.

12:28 UTC. Index creation completes. Latency begins to fall immediately.

12:56 UTC. Error rate returns to baseline. Incident downgraded to severity
three for monitoring, then closed at 14:30.

## Root cause

The migration added a foreign key constraint from the settlements table to
the transactions table without creating a supporting index on the referencing
column. PostgreSQL does not create such an index automatically. Every insert
into settlements therefore triggered a full scan of the transactions table,
which holds 1.4 billion rows. Each settlement insert held a pooled connection
for several seconds, and the pool of two hundred connections was exhausted
within twelve minutes of the migration completing.

The migration was reviewed and approved by two engineers. Neither reviewer
identified the missing index, and no automated check exists for this class of
defect.

## Contributing factors

The staging environment holds 2.1 million rows in the transactions table
against 1.4 billion in production, so the sequential scan completed in under
a millisecond during staging validation and produced no observable signal.

Connection pool saturation was instrumented but had no alert attached. The
metric had been at one hundred per cent for eighty-four minutes before any
engineer looked at it.

The deployment pipeline applies migrations and application code in a single
step, so there was no window in which the migration could have been observed
in isolation.

The initial hypothesis anchored the response on an upstream cause for
twenty-five minutes. The similar signature in INC-2025-1130 was genuinely
similar, but that incident's write-up did not record how the upstream cause
was confirmed, so the on-call could not quickly falsify the hypothesis.

## What went well

The concurrent index decision avoided a second locking event and was made
quickly once the cause was known. The incident commander handover was clean.
Merchant communications were accurate and did not overstate the recovery time.

## Action items

ACT-1. Add a CI check that fails any migration adding a foreign key
constraint without a corresponding index. Owner: Platform Engineering. Due 8
May 2026. Status: complete.

ACT-2. Attach an alert to connection pool saturation at eighty per cent
sustained for two minutes. Owner: Observability. Due 24 April 2026. Status:
complete.

ACT-3. Separate migration application from application deployment into two
distinct pipeline stages with a mandatory soak period. Owner: Platform
Engineering. Due 30 June 2026. Status: in progress.

ACT-4. Seed the staging transactions table to at least five per cent of
production row count. Owner: Data Platform. Due 31 July 2026. Status: not
started.

ACT-5. Amend the postmortem template to require that every ruled-out
hypothesis records the evidence used to rule it out. Owner: Rosalind
Achterberg. Due 1 May 2026. Status: complete.
