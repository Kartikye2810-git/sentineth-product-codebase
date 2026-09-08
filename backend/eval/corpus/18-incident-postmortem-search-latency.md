# Incident Postmortem: Search Latency Degradation

Incident ID: INC-2026-0208
Severity: Two
Date of incident: 8 February 2026
Duration: 6 hours 11 minutes
Incident commander: Amara Diallo
Author: Rosalind Achterberg
Status: Closed, actions tracked

## Summary

Between 06:40 and 12:51 UTC on 8 February 2026, document search latency
degraded from a median of 190 milliseconds to a median of 8.4 seconds. The
service remained available throughout and returned correct results; it
returned them too slowly to be usable. The cause was an unbounded result set
being loaded into memory by a reporting query that shared a connection pool
with the search path.

## Impact

All organisations were affected to some degree. Search requests did not fail,
so no error budget was consumed under the availability definition, which is
itself a finding recorded below.

Customer Support received forty-one tickets. Three enterprise customers
escalated, tracked under parent ticket SUP-10233. No service credits were
claimed because the availability commitment was technically met.

## Timeline

06:40 UTC. The scheduled monthly usage report begins. It has run without
incident for fourteen months.

06:44 UTC. Search p50 latency crosses one second. No alert fires, because the
latency alert is configured on p99 with a threshold of ten seconds and the p99
had not yet crossed it.

07:15 UTC. First customer ticket arrives describing search as very slow.

08:02 UTC. Support escalates to engineering after four further tickets.

08:30 UTC. Engineering acknowledges. Initial investigation focuses on the
vector store, on the assumption that a slow search is a search-engine problem.

09:50 UTC. Vector store metrics are confirmed normal. Attention moves to the
application layer.

10:35 UTC. Connection pool wait time is identified as the dominant component
of request latency.

11:20 UTC. The monthly report is identified as the pool consumer. It had grown
to select 62 million rows without pagination, a consequence of ordinary data
growth rather than any change.

12:30 UTC. The report job is terminated manually.

12:51 UTC. Latency returns to baseline.

## Root cause

A reporting query with no pagination and no row limit grew, through normal
data accumulation, until its memory and connection footprint starved the
interactive request path. Nothing changed on 8 February; the system crossed a
threshold that had been approaching for fourteen months.

The reporting job and the interactive path shared a single connection pool,
so a batch workload could exhaust capacity needed by user-facing requests.

## Contributing factors

Latency alerting was configured only on p99. A degradation affecting the
median while leaving the tail comparatively unchanged produced no alert for
two hours and eleven minutes.

The availability definition counts a request as successful if it returns a
2xx response, with no latency component. A service that answers every request
in eight seconds is fully available by that definition, which is why no error
budget was consumed and no service credits were due for a six-hour
degradation.

The first hour of investigation assumed the problem lay in the component whose
name matches the symptom. Search was slow, so the search engine was
investigated. This is a recurring pattern.

## What went well

Once connection pool wait time was examined, the cause was found in
forty-five minutes. Support's ticket summaries were precise about timing,
which allowed the correlation with the report schedule to be made quickly.

## Action items

ACT-1. Move batch and reporting workloads to a dedicated connection pool.
Owner: Platform Engineering. Due 6 March 2026. Status: complete.

ACT-2. Add p50 and p90 latency alerting alongside p99. Owner: Observability.
Due 20 February 2026. Status: complete.

ACT-3. Add a latency component to the internal availability definition, and
raise with Legal whether the customer-facing definition should change. Owner:
Rosalind Achterberg. Due 30 April 2026. Status: complete, customer-facing
definition unchanged on legal advice.

ACT-4. Add a row limit and pagination to every reporting query, and a test
that fails when an unbounded query is introduced. Owner: Data Platform. Due
31 May 2026. Status: complete.
