# Data Retention and Deletion Standard

Owner: Yusuf Adeyemi, General Counsel and Data Protection Officer
Applies to: All systems processing personal data or customer content
Version: 3.0
Effective: 1 April 2026

## 1. Principles

Data is retained only for as long as there is a lawful basis and a
demonstrable business need. Retention periods are set by data category, not by
system, so that the same category is treated consistently wherever it is
stored. When a retention period expires, deletion is mandatory and is not
subject to individual discretion.

Deletion means the data is irrecoverable through ordinary means. Marking a row
as deleted is not deletion. Where a system cannot support hard deletion,
cryptographic erasure of the relevant key material is an acceptable
alternative, provided the key is not held elsewhere.

## 2. Retention schedule

Customer content, meaning documents and files uploaded by a customer, is
retained for the duration of the contract plus ninety days. The ninety-day
tail exists to allow for accidental deletion recovery and contract renewal.

Derived data generated from customer content, including extracted text, chunk
records, and vector embeddings, follows the same period as the content it was
derived from and must be deleted in the same operation. A deletion that
removes the source document but leaves its embeddings is a failed deletion.

Application logs are retained for ninety days in hot storage and a further
two hundred and seventy days in cold storage, giving a total of twelve months.

Security and audit logs are retained for twenty-four months, reflecting the
evidentiary requirement rather than the operational one.

Employee personnel records are retained for six years after the end of
employment, consistent with the statutory limitation period for contractual
claims.

Recruitment records for unsuccessful candidates are retained for twelve months
after the decision, and only where the candidate has consented.

Financial records and supporting documentation are retained for seven years
after the end of the accounting period to which they relate.

Marketing contact data is deleted twenty-four months after the last
interaction, where interaction means an email open, a click, a form
submission, or a meeting.

## 3. Deletion on customer request

A customer may request deletion of their data at any time. Deletion of an
individual document must complete within twenty-four hours of the request,
across all stores including the vector index and any cache.

Deletion of an entire organisation's data on contract termination must
complete within thirty days, and a written confirmation of deletion is issued
to the customer within a further five working days.

Backups are the recognised exception. Data in backup snapshots is deleted when
the snapshot expires rather than on request. Backup snapshots are retained for
thirty-five days, so the maximum window during which deleted data persists in
backup is thirty-five days from deletion. This is disclosed in the customer
data processing agreement.

## 4. Data subject rights

Requests from individuals to access, correct, port, or erase their personal
data are handled by the Data Protection Officer and must be fulfilled within
one calendar month of receipt. The period may be extended by two further
months for complex requests, provided the individual is informed of the
extension and the reason within the first month.

Where the company is a processor rather than a controller, requests received
directly from an individual are forwarded to the relevant customer within
three working days and are not answered directly.

## 5. Legal hold

A legal hold suspends all deletion for the data in scope. Legal holds are
issued in writing by the General Counsel and are recorded in the legal hold
register with a scope definition, an issue date, and a named owner.

A legal hold overrides every retention period in this standard. Automated
deletion jobs must check the hold register before executing and must fail
closed if the register is unreachable.

Holds are reviewed every six months and released in writing when no longer
required.

## 6. Verification

The Data Protection Officer verifies deletion compliance quarterly by sampling
ten deletion events and confirming, in each store independently, that the data
is absent. The sample must include at least two organisation-level deletions.

Any store found to be retaining data past its period is treated as a
compliance incident and is reported to the Audit and Risk Committee.

## 7. Responsibilities

System owners are responsible for implementing the retention schedule in their
systems and for documenting how deletion is achieved. Where a system cannot
meet a retention requirement, the gap must be registered as an exception with
a remediation date, exactly as required by the Information Security Policy.
