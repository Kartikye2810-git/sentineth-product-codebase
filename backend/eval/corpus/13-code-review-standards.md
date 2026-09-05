# Code Review Standards

Owner: Rosalind Achterberg, Principal Engineer
Audience: All engineers
Version: 3.4
Last revised: 11 February 2026

## Why we review

Review exists to catch defects early, to spread knowledge of the codebase, and
to keep the system coherent as more hands touch it. It does not exist to prove
seniority or to enforce personal style preferences. A review comment that
cannot be traced to correctness, clarity, or an agreed convention is a
suggestion, and should be marked as one.

## Size and scope

A pull request should be readable in one sitting. The practical guideline is
under four hundred changed lines excluding generated files, lockfiles, and
test fixtures. Larger changes are not forbidden, but the author is expected to
split them or to provide a reading order in the description.

A pull request should do one thing. Mixing a refactor with a behaviour change
makes it impossible for a reviewer to tell which lines carry risk. If a
refactor is needed to make a change clean, land the refactor first as its own
change with no behavioural difference.

## Turnaround expectations

First review within one working day. If you cannot review within that window,
say so in the thread so the author can find someone else rather than waiting.

A blocked pull request is more expensive than an interrupted reviewer. Review
work takes priority over starting new work, though not over an active
incident.

## What a reviewer is responsible for

Reviewers check that the change does what its description claims, that the
tests would fail if the behaviour regressed, that error paths are handled, and
that the change does not introduce a security or data-integrity risk. A
reviewer who approves has taken shared responsibility for the change.

Reviewers are not responsible for running the code themselves unless the
change cannot be understood from reading, in which case say so and ask for a
demonstration.

## Comment conventions

Prefix a comment with "blocking" if approval depends on it. Prefix with
"suggestion" if the author may reasonably decline. Prefix with "question" when
seeking to understand rather than to change. Unprefixed comments default to
suggestions.

Disagreements that survive two rounds of comments move to a synchronous
conversation. Long comment threads are a signal that the written medium has
stopped working, not a signal to write more.

## Tests

Every behavioural change requires a test that fails before the change and
passes after it. A test that passes both before and after does not test the
change. Reviewers may ask the author to demonstrate the failing state.

Bug fixes require a regression test reproducing the reported bug. Reverts do
not require new tests. Pure refactors require no new tests but must not modify
existing ones; a refactor that changes a test is a behaviour change wearing a
disguise.

## Automation boundary

Formatting, import ordering, and lint rules are enforced by tooling and are
never review topics. If a reviewer finds themselves commenting on style, the
correct response is to propose a lint rule rather than to repeat the comment.

## Approvals

One approval is required to merge. Changes touching authentication,
authorisation, payment handling, or data deletion require two approvals, one
of which must come from an engineer familiar with that area.

Self-merging without approval is permitted only for reverting a change that is
actively causing an incident, and must be followed by a retrospective review
within one working day.
