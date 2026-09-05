# Business Continuity and Disaster Recovery Plan

Owner: Marcus Bellweather, CISO
Plan sponsor: Helena Vasquez, Chief Executive Officer
Version: 2.2
Last tested: 14 February 2026
Next test due: 14 August 2026

## Scope

This plan covers the loss of a primary cloud region, the loss of access to the
London office, the loss of a critical vendor, and the sustained unavailability
of key personnel. It does not cover security incidents, which are handled
under the incident response process in the Information Security Policy.

## Recovery objectives

The recovery time objective for the customer-facing service is four hours. The
recovery point objective is fifteen minutes, meaning that in the worst case up
to fifteen minutes of recently written data may be lost.

Internal systems including the expense portal, the workplace booking system,
and the internal wiki carry a recovery time objective of two working days and
are explicitly deprioritised behind the customer-facing service.

These objectives are commitments about intent and preparation. The contractual
availability commitment to customers is stated separately in the service level
agreement and is not the same number.

## Backup arrangements

Database backups are taken continuously through write-ahead log shipping to a
second region, with a full snapshot daily. Snapshots are retained for
thirty-five days. Object storage is replicated to a second region
asynchronously with a typical lag under two minutes.

A backup that has not been restored is a hypothesis. A restore is performed
into an isolated environment monthly, and the restored system is checked
against a fixed set of consistency assertions. The restore date and outcome
are recorded in the continuity register.

## Regional failover

The service runs in a single primary region with warm standby in a second. A
regional failover is a manual decision taken by the incident commander in
consultation with the CTO or, in their absence, the CEO.

Failover is expected to take between ninety minutes and three hours,
dominated by database promotion and DNS propagation. The procedure is
documented in the runbook and is exercised during the semi-annual test.

Failback is deliberately not automated. Returning to the primary region is
scheduled during a low-traffic window once the original fault is understood.

## Loss of premises

All roles are capable of remote working, so loss of an office is a
productivity event rather than a continuity event. Employees are notified
through the emergency notification system, which is tested quarterly.

Physical assets in the London office include no production systems. The only
material loss would be unsynchronised local work, which the endpoint policy
already mitigates.

## Vendor failure

Vendors assessed as critical are listed in the continuity register with a
named alternative and an estimated switching time. Where no alternative
exists, that fact is recorded explicitly rather than left implicit.

The identity provider and the cloud provider are both single points of failure
with no practical short-term alternative. This is an accepted risk, recorded
in the risk register and reviewed annually by the Audit and Risk Committee.

## Key personnel

Every critical function has a named primary and at least one named deputy.
Where a function has no deputy, that gap is tracked as a risk with a
remediation plan. Documentation adequate for a competent deputy to operate the
function is part of the definition of done for any critical system.

## Testing

The plan is tested twice yearly. One test is a tabletop exercise; the other is
a live failover in a non-production environment. Findings are recorded with
owners and due dates, and open findings are reported to the Audit and Risk
Committee.
