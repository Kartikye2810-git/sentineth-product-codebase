# Customer Support Escalation Runbook

Owner: Beatrix Ndlovu, Head of Customer Support
Audience: Support engineers, tier one and tier two
Version: 5.1
Last updated: 3 July 2026

## 1. Purpose

This runbook defines how a customer issue moves from first contact to
resolution, and specifically when and how it leaves Support and enters
Engineering. It does not cover billing disputes, which follow the Finance
dispute process.

## 2. Tiers and response targets

Tier one handles first contact for all channels. The first response target is
one hour during business hours and four hours outside them, measured from
ticket creation.

Tier two handles issues requiring product knowledge beyond the documented
troubleshooting steps, and owns all reproduction work. Tier two picks up an
escalated ticket within four business hours.

Engineering escalation is the final tier and is reserved for issues that tier
two has reproduced and cannot resolve with configuration or documented
workarounds.

## 3. Priority definitions

Priority one means a complete loss of service for a customer in production,
or a confirmed data integrity problem affecting any customer. Target
resolution is four hours. Priority one tickets page the engineering on-call
directly at any hour.

Priority two means a major feature is unusable with no workaround, or a
degradation affecting more than twenty per cent of a customer's users. Target
resolution is one business day.

Priority three means a feature is impaired but a workaround exists. Target
resolution is five business days.

Priority four covers cosmetic issues, documentation errors, and feature
requests. There is no resolution target; these are reviewed at the monthly
product triage.

A customer's own assessment of priority is an input, not a decision. Support
sets the priority against these definitions and explains the reasoning if it
differs from the customer's view.

## 4. Common error codes

Error code ERR-4012 indicates an authentication failure caused by an expired
API key. Resolution is customer self-service key rotation; direct the customer
to the key management page. Do not escalate.

Error code ERR-4019 indicates the request was authenticated but the API key
lacks the scope required for the endpoint. Verify the key's scopes against the
endpoint's documented requirement before escalating.

Error code ERR-4029 indicates rate limiting. Check the customer's plan limit
and their trailing usage. Escalate to tier two only if the customer's usage is
demonstrably below their limit.

Error code ERR-5031 indicates an internal failure in the payments service.
This is never a customer configuration problem. Escalate to Engineering at
priority one if the rate exceeds ten per cent of the customer's requests, or
priority two otherwise.

Error code ERR-5044 indicates a timeout in document ingestion. Check the
document size first: documents above the two hundred megabyte limit produce
this code rather than a clear size error, which is a known defect tracked as
BUG-3318. If the document is under the limit, escalate to tier two.

## 5. Escalating to engineering

An escalation must contain, at minimum, the customer identifier, the
organisation identifier, a precise timestamp range in UTC, the exact error
code or message, the request identifier from at least one failing request,
and the reproduction steps tier two followed.

An escalation missing the request identifier will be returned to Support.
This is not bureaucracy: without the request identifier the engineer cannot
locate the corresponding log lines, and the investigation cost roughly
triples.

Escalations are raised in the engineering escalation queue. Priority one
escalations additionally page the on-call and require a verbal handover.

## 6. Customer communication during an incident

During a declared incident, Support does not communicate root cause
hypotheses to customers. Only the status page and the incident commander's
approved updates may be quoted. This rule exists because an early hypothesis
communicated as fact is very difficult to retract.

Update affected customers at least every sixty minutes during a priority one
incident, even when there is nothing new to report. A message saying the
investigation continues is better than silence.

## 7. Post-incident follow-up

Every priority one ticket receives a written follow-up within five business
days of resolution, summarising what happened, what was done, and what will
change. The follow-up is drafted by Support and reviewed by the engineer who
resolved the issue.

Customers on the enterprise tier are additionally entitled to a postmortem
document on request for any priority one incident affecting them, which is
provided within ten business days.

## 8. Metrics

Support reports four metrics monthly: first response time against target,
resolution time against target by priority, escalation rate as a proportion
of tickets, and the proportion of escalations returned for missing
information. The last of these is a measure of Support's own quality and is
reviewed with the tier two leads.
