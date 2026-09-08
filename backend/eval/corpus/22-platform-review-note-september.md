# Platform Review Note, 10 September 2026

Attending: Nadia Fernsby, Rosalind Achterberg, Amara Diallo, Marcus
Bellweather, Ines Kowalczyk
Chair: Nadia Fernsby
Purpose: fortnightly review of the Q3 platform roadmap against its metrics

## Standing note on scope changes

Three items in the approved Q3 roadmap have changed since the plan was signed
off in June. They are recorded here because the roadmap document is not
reissued mid-quarter, and anyone reading it alone will be reading a figure
this meeting has already moved.

## Change 1: legacy batch scheduler

Retirement of the legacy batch scheduler is deferred to Q4. The weekly usage
report cannot move to the standard job runner this quarter: it depends on two
columns the runner does not expose, and the work to expose them was not in
the estimate.

The scheduler is left running with its alerts routed to a low-priority queue
rather than to the on-call rota, so it stops contributing pages without
anyone having to keep it healthy. Nadia noted that this is the outcome the
original item was trying to buy, at a fraction of the work, and asked whether
the retirement is worth doing at all in Q4.

## Change 2: migration soak period

The soak between the migration stage and application promotion is extended to
forty-five minutes. Fifteen minutes was set from the observed detection time
in the April payments incident, but two of the three migrations run under the
new gate this quarter produced their first anomalous metric after the twenty
minute mark, and both would have been promoted before the signal appeared.

Amara noted that a longer soak makes the deployment median worse and that
this is the correct trade: the deployment objective is not worth buying with
a migration incident.

## Change 3: alert ownership count

The alert ownership audit found more misrouted alerts than the roadmap
recorded. Twenty-two alerts route to the platform rota despite being owned by
another team, not the number quoted in the plan. The difference is alerts
that were created after the plan was written and defaulted to platform
because that is what the alert template does.

The template is being changed to require an owner rather than default to one.
That work is small and is being done inside the existing item rather than
tracked separately.

## Metrics at this review

Out-of-hours pages over the trailing thirty days: nine. The quarter's target
is met with three weeks to spare and the objective is reported green.

Median merge to production: thirty-one minutes, against a starting point of
fifty-four. The parallel test partitioning delivered less than modelled
because two test files dominate the longest partition.

Change failure rate: four point one per cent, within target.

## Next review

24 September 2026, which is the last review of the quarter. Nadia to bring a
first draft of the Q4 plan for discussion rather than approval.
