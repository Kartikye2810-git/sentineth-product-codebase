# Platform Engineering Roadmap, Q3 2026

Owner: Nadia Fernsby, VP Platform Engineering
Status: Approved by the Executive Team on 12 June 2026
Planning horizon: 1 July 2026 to 30 September 2026

## Context

Q2 closed with the platform carrying more operational load than the team can
sustain. The on-call rota absorbed one hundred and forty-seven pages across
the quarter, of which fifty-eight were outside working hours. Two engineers
have flagged on-call burden in their most recent one-to-ones. Reducing
unplanned work is therefore the organising principle for Q3, ahead of new
capability.

The quarter is scoped at three objectives. Anything not listed here is
explicitly out of scope and will be reconsidered in Q4 planning, which opens
on 8 September 2026.

## Objective 1: Halve unplanned operational work

Target: reduce out-of-hours pages from fifty-eight to no more than twenty-five
by the end of the quarter, measured on the trailing thirty days.

The largest single source of pages is the ingestion pipeline, which accounts
for thirty-one of the fifty-eight out-of-hours pages. Most of these are
retry-exhaustion alerts on a single downstream dependency that recovers
without intervention. Work item PLAT-401 replaces the alert with an automatic
backoff and escalates only when the failure persists beyond twenty minutes.

Work item PLAT-402 introduces alert ownership metadata, so that every alert
routes to the team that can act on it rather than to the platform rota by
default. Fourteen alerts currently route to platform despite being owned
elsewhere.

Work item PLAT-403 retires the legacy batch scheduler, which has produced
nine pages and no successful runs since March. The scheduler's remaining
consumer, the weekly usage report, moves to the standard job runner.

## Objective 2: Make deployments boring

Target: median time from merge to production under twenty minutes, with a
change failure rate below five per cent.

Current median is fifty-four minutes, dominated by a serial integration test
stage that runs for thirty-one minutes. Work item PLAT-410 partitions this
stage across eight parallel runners. Expected reduction is twenty-four
minutes.

Work item PLAT-411 separates database migration from application deployment,
delivering action item ACT-3 from incident INC-2026-0417. Migrations will run
as an explicit gated stage with a fifteen-minute soak before application code
is promoted.

Work item PLAT-412 adds automatic rollback on error-rate regression. The
rollback triggers when the post-deployment error rate exceeds the pre-
deployment rate by more than two percentage points sustained over three
minutes.

## Objective 3: Establish capacity planning

Target: a published capacity model for the three highest-cost services, with
a forecast horizon of two quarters and a monthly review.

There is currently no capacity model of any kind. Infrastructure spend grew
thirty-eight per cent quarter over quarter while request volume grew eleven
per cent, and nobody can currently explain the gap. Work item PLAT-420 is the
investigation; PLAT-421 is the model; PLAT-422 wires the model into the
monthly finance review.

## Staffing and constraints

The team has six engineers and will have seven from 4 August 2026 when Ines
Kowalczyk joins. Ines will not be counted in delivery capacity for the first
six weeks.

Two engineers, Tobias Lindqvist and Amara Diallo, are committed at fifty per
cent to the payments reliability programme until 15 August 2026 and are
excluded from roadmap capacity accordingly.

The quarter contains a two-week change freeze from 24 August to 6 September
2026 covering the retail peak readiness window. No production changes other
than severity-one remediation may be deployed during the freeze.

## Explicitly out of scope

The multi-region active-active work is deferred to 2027. The current
single-region deployment with cross-region backup meets the contractual
availability commitment of 99.9 per cent, and the active-active programme was
estimated at two quarters of the whole team.

The internal developer portal is deferred. It is valuable but it is not
operational load reduction, and Q3 is scoped on operational load.

The Kubernetes version upgrade is deferred to Q4 because the current version
reaches end of support in February 2027, which leaves adequate runway.

## Reporting

Progress is reported at the fortnightly platform review, first session 9 July
2026. Objective health is reported as a single colour per objective, based on
the metric rather than on activity completed.
