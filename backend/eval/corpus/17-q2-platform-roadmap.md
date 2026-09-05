# Platform Engineering Roadmap, Q2 2026

Owner: Nadia Fernsby, VP Platform Engineering
Status: Closed, retained for reference
Planning horizon: 1 April 2026 to 30 June 2026

## Retrospective note

This roadmap is superseded by the Q3 2026 plan. It is retained because two of
its objectives carried over and because the quarter's outcome is the reason
Q3 was scoped the way it was.

## Context at the time

Q1 closed with the team at five engineers and a growing backlog of
reliability work that had been repeatedly deprioritised in favour of feature
support. The quarter was planned on the assumption of two new hires landing in
April, only one of which materialised.

## Objective 1: Observability foundations

Target: every customer-facing service emitting structured logs, traces, and a
standard set of four service metrics by the end of the quarter.

Outcome: partially achieved. Nine of eleven services were migrated. The two
remaining are the legacy batch scheduler, which is being retired rather than
migrated, and the reporting service, which is scheduled for Q3.

Work item PLAT-310 delivered the shared logging library. Work item PLAT-311
delivered trace propagation across service boundaries. Work item PLAT-312,
the metrics standard, slipped and completed three weeks into Q3.

## Objective 2: Reduce build times

Target: continuous integration under fifteen minutes at the ninety-fifth
percentile.

Outcome: not achieved. The p95 finished the quarter at twenty-eight minutes
against a starting point of thirty-four. Caching improvements delivered less
than modelled because the cache key included a frequently-changing file, which
was not discovered until late May.

The remaining work moved into Q3 as the integration test partitioning item.

## Objective 3: On-call sustainability

Target: publish an on-call charter, introduce a follow-the-sun rota with the
Bengaluru team, and reduce page volume.

Outcome: the charter was published and the rota introduced. Page volume rose
rather than fell, from one hundred and twelve pages in Q1 to one hundred and
forty-seven in Q2, driven principally by the ingestion pipeline and by alerts
that had been added without an owner.

This outcome is what made operational load the organising principle of Q3
rather than one objective among several.

## What we learned

Three of the quarter's misses share a cause: the work was estimated against a
capacity that assumed hires who had not yet started. Q3 planning excludes
unstarted hires from capacity entirely.

Alert volume was treated as a lagging indicator of reliability rather than as
a workload in its own right. It is now tracked as a first-class metric with
its own target.

The build time objective was expressed as a percentile without a baseline
distribution, so it was not possible to tell during the quarter whether the
work was helping. Objectives now carry a stated starting value.
