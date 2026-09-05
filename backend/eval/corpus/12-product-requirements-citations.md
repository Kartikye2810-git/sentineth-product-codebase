# Product Requirements: Answer Citations

Product owner: Helena Vasquez, standing in for the vacant Head of Product role
Engineering lead: Rosalind Achterberg
Design: Not yet assigned
Status: Draft, under review
Last revised: 8 August 2026

## 1. Problem

Users do not trust answers they cannot verify. In fourteen of the nineteen
customer interviews conducted between May and July 2026, the interviewee
independently raised verification as their primary concern before being
prompted about it. The most frequently used phrase was some variant of "how do
I know it did not make that up".

The current product returns an answer with a filename. A filename is
insufficient. A policy document of sixty pages with a filename attached
requires the user to search the document manually, which is the work they were
trying to avoid.

## 2. Objective

Every generated answer must be traceable to the specific passage that supports
it, at a granularity a user can check in under ten seconds.

The success measure is the proportion of answers where a user, given the
citation, can locate the supporting passage without using search. The target
is ninety per cent, measured through moderated usability sessions with at
least twelve participants.

## 3. Requirements

Each answer must display between one and five citations. Answers supported by
more than five passages display the five highest-scoring and indicate that
further supporting passages exist.

A citation must identify the source document by its display name, the page
number within that document, and a quoted extract of no more than forty words
taken verbatim from the source.

Page numbers are mandatory, not optional. A citation that identifies a
document but not a location within it does not meet this requirement. Where a
source has no natural pagination, such as a chat transcript, the citation
must instead carry a timestamp or message identifier.

Clicking a citation must open the source document at the cited page with the
quoted extract highlighted. The document must open in under two seconds at the
ninety-fifth percentile for documents up to fifty megabytes.

Where the system cannot support an answer with at least one citation, it must
decline to answer rather than answer without citation. An uncited answer is
considered a defect, not a degraded experience.

## 4. Out of scope for the first release

Cross-document citation clustering, where several passages making the same
point are collapsed into one citation, is deferred. It is desirable but adds
ranking complexity that is not justified before the basic capability ships.

Citation feedback, where a user marks a citation as wrong or irrelevant, is
deferred to the second release. The data model must not preclude it.

Citations into connected sources such as Slack or email are out of scope until
the connector programme delivers those sources.

## 5. Dependencies

This requires page numbers to be captured during document extraction and
carried through the indexing pipeline into the retrieval payload. That
capability does not currently exist and is the critical path for this feature.

It also requires the retrieval layer to return passages accurate enough that
the quoted extract genuinely supports the answer. Citation quality is bounded
above by retrieval quality: a citation pointing at the wrong passage is worse
than no citation, because it manufactures unwarranted confidence.

## 6. Open questions

Whether the quoted extract should be the retrieved passage in full or a
model-selected sentence from within it is undecided. The full passage is
honest about what the system used; a selected sentence is easier to read.
Recommendation is to display the selected sentence with the full passage
available on expansion.

How to present a citation when the supporting passage spans a page boundary is
undecided. The current proposal is to cite the page on which the passage
begins.

## 7. Rollout

The feature ships behind a flag to internal users first, then to three design
partners who have specifically asked for verification, then generally. No date
is committed until the extraction dependency has an owner.
