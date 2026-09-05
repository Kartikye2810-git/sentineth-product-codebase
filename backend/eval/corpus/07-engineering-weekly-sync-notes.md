# Engineering Weekly Sync, Notes

Date: Tuesday 26 August 2026, 10:00 to 10:52 BST
Chair: Nadia Fernsby
Note taker: Ines Kowalczyk
Present: Nadia Fernsby, Rosalind Achterberg, Tobias Lindqvist, Amara Diallo,
Ines Kowalczyk, Marcus Bellweather (items 1 and 4 only), Delphine Okonkwo
(item 5 only)
Apologies: Priya Raghunathan

## Item 1: Change freeze readiness

The retail peak change freeze begins on Monday 24 August and runs to Sunday 6
September. Nadia confirmed the freeze is now in effect as of this meeting.

Marcus raised that the security patch cadence does not stop during a freeze.
Agreed that critical and high severity patches remain deployable during the
freeze under the severity-one remediation exemption, but that the deployment
must be announced in the engineering channel before it starts, not after.

Decision: security patches at critical or high severity are exempt from the
change freeze. Medium and low severity patches wait until 7 September.
Recorded in the platform decision log as DEC-2026-088.

Action: Tobias to publish the list of deferred medium and low patches by
Thursday 28 August so the backlog is visible before the freeze ends.

## Item 2: Atlas phase four progress

Amara reported that Project Atlas is at fifty per cent read traffic on the new
schema and has held there for six days rather than the planned twenty-four
hours. The hold is deliberate: the reconciliation job found two discrepancies
last Wednesday, both traced to a nullable display name being written as an
empty string by one legacy code path rather than as null.

The underlying defect is fixed. The two affected rows were corrected manually.
Amara wants a further full reconciliation pass before proceeding to one
hundred per cent.

Nadia asked whether this changes the phase five date. Amara said it moves it
by approximately one week, to the week commencing 14 September, which is after
the freeze ends and is therefore acceptable.

Decision: Atlas proceeds to one hundred per cent read traffic only after two
consecutive clean full reconciliations, and phase five is rescheduled to the
week commencing 14 September.

## Item 3: On-call load

Rosalind presented the trailing thirty day page count: thirty-four pages, of
which eleven were out of hours. This is down from fifty-eight out-of-hours
pages in the Q2 baseline and is ahead of the Q3 target of twenty-five.

The largest remaining contributor is the nightly export job, which has paged
four times in thirty days, each time due to a downstream timeout that
resolves on retry. PLAT-401 covers the ingestion pipeline but not this job.

Action: Rosalind to raise a follow-up work item extending the backoff pattern
from PLAT-401 to the nightly export job. Target for Q4 unless it pages again
before the freeze ends, in which case it is treated as unplanned work and done
immediately.

## Item 4: Secret scanning false positives

Marcus reported that secret scanning blocked eleven merges last week, of which
nine were false positives caused by test fixtures containing realistic-looking
API keys.

Discussion about whether to allowlist the test fixture directory. Marcus
opposed a blanket allowlist on the grounds that a real secret committed to a
test fixture is still a real secret. Agreed instead to change the fixtures to
use an obviously synthetic prefix that the scanner recognises.

Decision: test fixtures adopt the prefix sentineth_test_ for all synthetic
credentials, and the scanner is configured to ignore that prefix specifically.
Recorded as DEC-2026-089.

Action: Ines to update the existing fixtures. Estimated half a day.

## Item 5: Onboarding feedback

Delphine shared feedback from the last two onboarding cohorts. The most common
complaint, from seven of nine new joiners, was that the local development
environment took more than a day to stand up, and that the written setup
instructions were out of date in three places.

Ines confirmed this from personal experience, having joined on 4 August.

Action: Ines to rewrite the local development setup guide while the experience
is recent, and to have a new joiner follow it unaided as the acceptance test.
Due before the next cohort starts on 15 September.

## Item 6: Any other business

Amara noted that the architecture review scheduled for 2 September falls
inside the freeze but involves no production change, and will go ahead.

Next meeting: Tuesday 2 September, 10:00 BST. Rosalind chairs; Nadia is on
leave.
