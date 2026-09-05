# API Versioning and Deprecation Policy

Owner: Rosalind Achterberg, Principal Engineer
Audience: Engineering, Support, Customer Success
Version: 2.0
Effective: 1 March 2026

## Versioning scheme

The public API is versioned in the URL path, currently at v2. A version
increment is reserved for changes that would break a conforming client. The
version is not incremented for additive changes.

Within a version, the API is expected to evolve. Clients must tolerate
additive change: unknown response fields must be ignored, new enum values must
not cause a hard failure, and field ordering must not be relied upon. This
expectation is stated in the developer documentation and in the contract.

## What counts as breaking

Removing a field, renaming a field, narrowing a type, adding a required
request parameter, changing a default, changing an error code for an existing
condition, tightening validation on previously accepted input, and reducing a
rate limit are all breaking.

Adding an optional request parameter, adding a response field, adding a new
endpoint, adding a new enum value to a field documented as extensible, and
relaxing validation are not breaking.

Changing pagination behaviour is breaking, including changing the default page
size. Two integrators are known to depend on the current default.

## Deprecation process

A deprecation is announced at least one hundred and eighty days before the
capability is withdrawn. Enterprise customers receive twelve months for any
change requiring code modification on their side.

Announcement means all of: a changelog entry, a Deprecation response header on
affected endpoints carrying the sunset date, an email to the technical contact
on record, and an entry in the deprecation register.

During the deprecation window the capability continues to work unchanged.
Degrading a deprecated capability before its sunset date is not permitted.

## Sunset

On the sunset date the endpoint returns HTTP 410 Gone with a body naming the
replacement. The 410 response is retained for at least twelve months so that a
client that failed to migrate receives a diagnosable answer rather than a 404.

Usage is monitored throughout the deprecation window. If a customer is still
calling a deprecated endpoint thirty days before sunset, Customer Success
contacts them directly. Sunset may be deferred for a named customer, once, by
up to ninety days.

## Emergency changes

A breaking change may be made without notice only where continuing to serve
the existing behaviour would expose customer data, violate a legal obligation,
or perpetuate a security vulnerability that cannot be mitigated otherwise.

Such a change requires CTO and General Counsel approval, and affected
customers must be notified within twenty-four hours with an explanation.

## Client libraries

Official client libraries follow semantic versioning independently of the API
version. A new major version of a client library does not imply a new API
version, and vice versa.

Client libraries are supported for eighteen months from release. Security
fixes are backported to supported versions; other fixes are not.

## Documentation

Every endpoint carries documentation generated from the same schema that
validates requests, so documentation drift is structurally prevented rather
than managed by review. Hand-written prose supplements the generated reference
but never restates it.
