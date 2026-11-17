# ADR 0002: Archive the raw request, then acknowledge, then process

Status: accepted, 2026-11-17

## Context

A vendor that receives a 2xx considers the event delivered and will not send it
again. If the receiver acknowledges and then loses the event (a parser bug, a
throttled table, a bad deploy), the event is gone and nobody finds out until a
borrower calls about a payment that never showed up.

## Decision

The receiver does three things and nothing else: verify the signature, write the
exact request body to S3, enqueue a pointer to it. It returns 202 only after both
writes succeed. It does not parse the body.

Processing happens later, from the queue, by a separate function that reads the body
back from S3.

## Why not process inline

Processing inline (verify, parse, update state, respond) is fewer moving parts and
gives the vendor a truthful "we handled it". It also means every downstream fault is
visible to the vendor as a 5xx, the receiver's latency is the sum of everything it
calls, and a payload that trips a bug is retried by the vendor until they give up,
after which it exists nowhere. With the archive first, the same bug leaves the bytes
in S3 and a message in the DLQ; the fix is deployed and the message is redriven.

## Why a pointer and not the payload on the queue

- The archive is the single source of truth. Replay reads the same objects the first
  attempt read, so "replay" and "first delivery" are one code path from the queue on.
- SQS messages have a size cap (1 MiB); vendor payloads are not guaranteed to fit.
- PII stays in one bucket under one KMS key. A queue, a DLQ and whoever can read
  either never hold a payload.

The cost is one S3 GET per event in the processor.

## Why the key is a content hash under the signed timestamp

`raw/vendor=<v>/dt=<date>/hour=<hh>/<sha256>.body`, written with `If-None-Match: *`.

A network-level retransmission carries the same bytes and the same signed timestamp,
maps to the same key, and the conditional put makes it a no-op: no second object, no
second version. The partition comes from the signed timestamp rather than the
receive time because it is authenticated, it is the same for a retransmission, and
it lets a verifier that knows what was sent compute the key exactly.

A vendor retry that is re-signed in a later hour produces a second object with the
same content hash in a different partition. That is accepted: the archive records
deliveries, and deduplication by event id is the processor's job.

## Why HTTP API, standard SQS, and Lambda

- HTTP API rather than REST API: about 70% cheaper per request and lower latency, and none of
  the REST-only features (usage plans, request validation, API keys) apply to
  machine-to-machine webhooks authenticated by HMAC. The one REST feature that would
  help is forcing every body to base64 with a `*/*` binary media type, which makes
  byte-exactness independent of content type. HTTP APIs decide by content type, so a
  JSON body that is not valid UTF-8 is altered before the function sees it and fails
  verification. That fails closed and is documented; if a vendor ever needed it, the
  ingest module is where a REST API would be swapped in.
- SQS standard rather than FIFO: ordering by message group would need the entity id,
  which is inside the body the receiver refuses to parse, and FIFO deduplication only
  covers five minutes while vendors retry for days. Ordering and deduplication are
  enforced at the write instead (ADR 0003), where they hold for any delivery order.
- SQS rather than Kinesis or EventBridge: one consumer, per-message acknowledgement,
  a native DLQ and a visibility timeout are exactly SQS. Kinesis would add shards and
  per-shard ordering nobody is relying on, and a poison record blocks its shard.
  EventBridge is for fan-out by content, and the content is not parsed here.
- Lambda rather than a container service: load is bursty and usually near zero, the
  receiver is stateless, and there is nothing to keep warm that justifies a
  minimum of two always-on tasks and a load balancer.

## Consequences

- Two S3 requests per event (one PUT, one GET) that an inline design would not make.
  At a million events a month the PUTs are the largest per-event line in the cost
  estimate.
- A structurally broken payload is acknowledged. That is intended: it is authentic,
  it is evidence, and it ends in quarantine with an alarm rather than in a vendor's
  retry queue (ADR 0004).
