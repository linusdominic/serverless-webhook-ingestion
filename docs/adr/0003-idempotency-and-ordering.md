# ADR 0003: Idempotency and ordering with DynamoDB conditional writes

Status: accepted, 2026-11-17

## Context

Events arrive more than once and in any order. The state table must end up the same
whatever the order and however many copies, and must never show an older status
after a newer one.

## Decision

Two tables.

**Entity state**, one item per payment, call or verification. Every event carries a
version from the vendor: a sequence number, a revision, or (for the vendor that
offers neither) the event time in milliseconds. State is written with one
conditional `UpdateItem`:

```
attribute_not_exists(pk)
OR version < :version
OR (version = :version AND last_event_id < :event_id)
```

State only ever moves to a strictly greater `(version, event_id)`. The final state is
therefore the maximum over the events received, which does not depend on order. The
event id term is a tie-break for vendors that can emit two events with the same
version: something has to win, and it has to be the same one on every replay.

**Idempotency ledger**, one item per `(vendor, event_id)`, with a TTL. The processor
reads it first and skips events it has a record for.

The order of effects for one event is: state update, write to the landing zone,
ledger record. The ledger record is written last and conditionally.

## Why the ledger is written last

The usual pattern claims the key first (a conditional put of an "in progress"
record) and marks it complete at the end. That needs a lease and an expiry to
recover from a crash between the two, and a decision about what a second worker does
when it finds a live lease.

Here every step before the ledger write is safe to repeat: the state update is a
no-op the second time, and a second copy in the landing zone is removed by the
compactor. So nothing needs claiming. If the function dies anywhere, the message
returns to the queue, the retry redoes steps that change nothing, and the ledger
record is written once everything it stands for is durable. The invariant is the
same one the receiver has: nothing is marked done that is not done.

The price is that two workers handling the same event at the same moment both write
it to the landing zone. The compactor deduplicates, and the unit tests cover it.

## Why not Lambda Powertools idempotency

Powertools keys on a hash of the payload or a JMESPath into it, stores the function's
return value, and replays that value to duplicates. It is a good default. It does not
do ordering, so the state condition would exist anyway, and with that in place the
ledger is one `GetItem` and one conditional `PutItem`. Carrying a dependency into
every package to save those two calls was not worth it, and the "ledger last"
ordering above is not how Powertools sequences its record.

## Why not FIFO queues for ordering

See ADR 0002. In short: ordering would need the entity id before parsing, FIFO
deduplication lasts five minutes, and a condition at the write holds even for
replays months later.

## Consequences

- "Latest version wins" is the whole state model. The processor does not reject an
  impossible transition (a payment going from `failed` to `settled`); it records
  what the vendor said. Transition rules belong to the consumer of the state.
- A stale event still costs a write unit: a failed condition is billed.
- The ledger TTL (30 days by default) must exceed the vendor's longest retry
  schedule. After it expires, a very late duplicate is reprocessed; the state
  condition and the compactor still make that harmless, which the forced-replay
  proof demonstrates by bypassing the ledger altogether.
- The ledger read is strongly consistent, at twice the read cost, because the
  duplicates that matter most arrive within milliseconds of each other.
