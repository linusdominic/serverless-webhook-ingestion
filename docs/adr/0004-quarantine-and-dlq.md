# ADR 0004: Quarantine what cannot work, dead-letter what should have

Status: accepted, 2026-11-17

## Context

Some messages fail in the processor. They fail for two different kinds of reason and
the difference matters to whoever is on call.

1. The payload is the problem and will be the problem on every attempt: it is not
   JSON, it lacks a required field, or it is an event type this code has never heard
   of.
2. Something else went wrong: S3 or DynamoDB returned an error, the function timed
   out, or the code hit a case nobody anticipated.

## Decision

**Quarantine** the first kind. The normaliser raises `PermanentError` with a reason
(`malformed_json`, `schema_invalid`, `unknown_event_type`). The processor writes a
small pointer object to `quarantine/vendor=<v>/reason=<r>/<sha256>.json`, emits a
metric, and reports the message as handled. No retries are spent.

**Dead-letter** the second kind. Any other exception is reported through the partial
batch response, the message returns to the queue, and after `maxReceiveCount`
receives SQS moves it to the DLQ. An alarm fires when the DLQ is not empty.

The quarantine object is a pointer (archive key, reason, a detail string that never
contains payload values). The payload stays in the raw bucket and only there.

## Why not send everything to the DLQ

A DLQ that fills up every time a vendor ships a new event type teaches people to
ignore the DLQ alarm. Keeping it for the unexpected means a message there is always
worth a human's attention. It also saves the retries: an unparseable body is not
going to parse on the fifth attempt.

## Why not reject malformed payloads at the receiver with a 400

Because "malformed" is a judgement made by our parser, and the parser can be wrong.
A vendor adds a field, changes a number to a string, or sends a new event type; a
receiver that rejects it tells the vendor to retry something that will never pass,
and when they stop retrying the event is lost. Authentic payloads are accepted and
kept. If the normaliser was at fault, it is fixed and the quarantined payloads are
replayed from the archive.

## The demo's poison message

The load generator sends three payment events whose amount is a 45-digit integer.
The schema says "non-negative integer", so they pass validation. DynamoDB numbers
hold 38 significant digits, so serialising the state update raises, every time. They
are received three times and land in the DLQ. This is deliberate: the validation does
not special-case it, because the point is to exercise the path for failures nobody
predicted. The real-world sequel is in the runbook: find the cause, add a bound to
the schema so such a payload is quarantined, deploy, redrive.

## Consequences

- An unknown event type is silent data loss from the consumer's point of view until
  someone looks. The `EventsQuarantined` metric is on the dashboard by reason; a
  production deployment should alarm on `unknown_event_type` specifically.
- Schema validation is structural, not exhaustive. Anything it lets through that the
  stores cannot hold becomes a DLQ message, as the poison shows.
- With the partial batch response, one failing message does not block or re-run the
  rest of its batch. The time for a message to reach the DLQ is
  `maxReceiveCount x visibility timeout`: 75 seconds in the emulator root, 15 minutes
  with the production-shaped defaults.
