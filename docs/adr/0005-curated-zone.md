# ADR 0005: The processor writes its own landing objects; a scheduled pipeline compacts them

Status: accepted, 2026-11-17

## Context

Normalised events have to end up queryable in Athena, once each, in a columnar
format, without the processor waiting on anything slow.

## Decision

**Landing.** At the end of each batch the processor writes one gzip JSON-lines object
per `(vendor, event date)` it saw:

```
landing/events/vendor=<v>/dt=<event date>/<hash of the event ids>.jsonl.gz
```

**Compaction.** A Step Functions state machine, started by EventBridge Scheduler,
runs four Lambda tasks:

1. *Compact.* For every partition with landing objects not yet folded in, read the
   partition's existing Parquet file and the new objects, keep one row per event id,
   and write `compacted/events/vendor=<v>/dt=<d>/part-00000.parquet`. A manifest
   object per partition lists the landing keys already folded in.
2. *Update catalog.* List the compacted partitions in S3 and register any the Glue
   table lacks.
3. *Data quality.* Run each SQL file in `src/webhook_platform/sql/dq/` in Athena;
   fail if any returns a non-zero violation count.
4. *Reconcile.* Compare, per vendor, the number of events the processor recorded in
   its ledger with the number of rows Athena counts in the compacted table.

Any failure is caught and published to the alerts topic, and the execution fails.

## Why not Firehose

Firehose is the obvious choice and I tested it first. The partition scheme is what
decided against it. Partitioning by the event's own date means every copy of an event
lands in the same partition no matter when it is processed, which is what lets the
compactor deduplicate one partition at a time and makes its output a function of the
partition's contents. With Firehose that requires dynamic partitioning, which costs
extra per GB and per object, and which the emulator does not implement (it wrote
objects under the literal prefix `!{partitionKeyFromQuery:vendor}`). The alternative,
partitioning by delivery time, puts copies of the same event in different partitions
after a retry or a replay and turns deduplication into a cross-partition problem.

Writing the objects directly also gives deterministic names (a retry of the same
batch overwrites its own object) and no buffering delay. What it gives up is
Firehose's batching across invocations: there is one object per batch per partition,
which is why compaction exists. At much higher volume the trade reverses; see "What
changes at 100x" in the README.

## Why a manifest and not a timestamp watermark

"Fold in objects modified since the last run" can skip an object whose upload started
before the watermark and finished after the listing. A listing is exact about which
keys exist, so the compactor subtracts the keys it has already folded in. Landing
objects are small and expire after 14 days, so the manifest stays small.

## Why compaction merges instead of rebuilding from landing

Landing objects expire. If a late event arrives for a partition whose landing objects
are gone, a rebuild from landing would drop every earlier row. Merging the existing
Parquet file with only the new objects is correct regardless of what has expired.

## Why Lambda with pyarrow and not Athena CTAS or a Glue job

On AWS, `INSERT INTO ... SELECT` or a CTAS would do this in SQL. Neither emulator
runs them (ADR 0001), and I was not willing to ship a compaction path that has never
executed. A Lambda with pyarrow does execute here, is cheap at this volume, and has
an honest limit: a partition is merged in memory, so partition size is bounded by the
function's memory. A Glue Spark job would be a five-minute start-up and a minimum
charge to merge a few megabytes.

## Why Lambda tasks and not the native Athena integration

`athena:startQueryExecution.sync` would remove a function that mostly waits. Neither
emulator implements it. The data-quality step also needs logic the integration does
not have (run a directory of checks, compare each result to zero, write a report),
which in pure Amazon States Language means a Map state, `GetQueryResults` and a
Choice per check. One small function is easier to read and is the path that was
actually run.

## Consequences

- The landing zone is at-least-once; only the `events` table is exactly-once. The
  landing table's description says so.
- Compaction and catalog registration are separate steps on purpose: the catalog step
  takes no input and reconciles Glue with S3, so a run that dies between the two is
  repaired by the next.
- Executions must not overlap. The state machine's timeout is kept below the schedule
  period (840 s against 15 minutes) so they cannot. Overlap would not corrupt data,
  since both would write the same merge, but a slower run could overwrite a fresher
  file and leave the next run to redo it.
- Reconciliation looks at a recent window, not all of history, so its cost does not
  grow with the table. An event that is lost and not noticed within the window is
  not noticed by this check later.
