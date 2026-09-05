# Information Security Policy

Owner: Marcus Bellweather, Chief Information Security Officer
Classification: Internal
Version: 7.0
Review cycle: Annual, next review 14 August 2026

## 1. Governance

The Chief Information Security Officer owns this policy and reports to the
Audit and Risk Committee on a quarterly basis. Day-to-day operation of the
security programme is delegated to the Security Engineering team, currently
five engineers reporting to the Head of Security Engineering.

Every employee is responsible for compliance. Line managers are accountable
for the compliance of their teams and for ensuring that leavers are
de-provisioned on their final working day.

## 2. Data classification

All information assets must carry one of four classifications. Public
information may be disclosed without restriction. Internal information is
available to all employees but must not be shared outside the company.
Confidential information is restricted to named individuals or defined roles
and requires encryption at rest and in transit. Restricted information is the
highest tier, covering customer source documents, authentication material,
and personal data of special category under UK GDPR.

Restricted information may only be processed in the production environment
and may never be copied to a local workstation, a personal cloud account, or
a non-production environment. Synthetic or anonymised data must be used for
all development and testing.

The default classification for any document with no explicit label is
Internal. Authors are responsible for upgrading the classification where the
content warrants it.

## 3. Access control

Access is granted on the principle of least privilege and is reviewed
quarterly. Standing administrative access to production is not permitted.
Engineers requiring production access must request it through the just-in-time
access system, which grants a time-boxed credential valid for a maximum of
four hours.

Multi-factor authentication is mandatory for all systems that support it.
Hardware security keys are required for any account with administrative
privileges over production infrastructure, source control, or the identity
provider. Time-based one-time passwords delivered by application are
acceptable for all other accounts. SMS-delivered codes are prohibited as a
second factor.

Shared accounts are prohibited. Where a vendor system genuinely cannot support
individual accounts, the exception must be registered in the exception
register and reviewed every six months.

## 4. Passwords and secrets

Passwords must be a minimum of fourteen characters. Complexity rules beyond
length are not enforced, in line with NCSC guidance, but passwords must not
appear in any known breach corpus. The password manager is the only approved
location for storing credentials.

Application secrets must be stored in the secrets manager and injected at
runtime. Secrets must never be committed to source control, written to log
output, or transmitted over instant messaging. A secret that has been exposed
must be rotated within four hours of discovery, regardless of assessed
likelihood of compromise.

Automated secret scanning runs on every push to every repository. A detected
secret blocks the merge and raises an alert to the Security Engineering
on-call.

## 5. Endpoint security

Company-managed laptops must have full-disk encryption enabled, the endpoint
detection agent running, and the operating system within two minor versions
of current. Devices failing these checks lose network access after a
seven-day grace period.

Personal devices may access email and calendar only, through the managed
application container. Personal devices may not access source control, the
production environment, or any system holding Restricted information.

Removable storage media are blocked by default. An exception requires
CISO approval and is granted for a maximum of thirty days.

## 6. Incident response

Any suspected security incident must be reported to the Security Engineering
on-call immediately through the dedicated incident channel, and never by email
alone. Reporting is expected within one hour of suspicion, and there is no
penalty for a report that turns out to be a false alarm.

Incidents are triaged into four severities. A severity one incident involves
confirmed unauthorised access to Restricted information or a complete outage
of a customer-facing service, and requires the incident commander to convene
a response bridge within fifteen minutes. Severity two covers suspected
unauthorised access or a partial outage, with a bridge convened within one
hour. Severity three and four are handled during business hours.

Regulatory notification decisions are made by the General Counsel, not by the
engineering team. Where UK GDPR notification obligations apply, the
seventy-two hour clock starts at the point the company becomes aware of the
breach, not at the point the investigation concludes.

## 7. Third parties and vendors

Any vendor processing company or customer data must complete a security
review before contract signature. The review covers their certification
status, subprocessor list, data residency, breach notification terms, and
deletion guarantees. Vendors handling Restricted information must hold a
current ISO 27001 certificate or a SOC 2 Type II report no more than twelve
months old.

Vendor reviews are re-performed annually for Restricted-tier vendors and
every two years for all others.

## 8. Training and awareness

All employees complete security induction training within their first five
working days and refresher training annually. Engineers additionally complete
secure development training annually. Simulated phishing exercises run
quarterly; an employee who reports the simulation is recorded as a pass, and
an employee who submits credentials is enrolled in a short remedial module.

## 9. Exceptions

Any deviation from this policy requires a registered exception with a named
owner, a stated business justification, a compensating control, and an expiry
date no more than twelve months in the future. Exceptions are reviewed by the
CISO monthly and reported to the Audit and Risk Committee quarterly.
