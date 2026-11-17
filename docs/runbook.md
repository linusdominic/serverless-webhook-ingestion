# Runbook

For whoever is on call. Each alarm's description links to a section here.

Commands are written for AWS (`aws ...`) because that is where this would be
operated. The same procedures were exercised on the emulator through the Makefile
targets noted in each section; the `aws` commands themselves have not been run
against a real account.

Names below use `$P` for the resource prefix, for example `webhook-ingestion-prod`.

## First, in every case

1. Open the dashboard `$P-webhooks`. The top row says whether vendors are being
   answered; the middle rows say whether work is flowing.
2. Remember what is and is not at risk. Once a request has been answered 202 its
   body is in S3 and will not be lost by anything below. A request that got a 5xx or
   no answer is in the vendor's retry queue and will come back. The archive is the
   recovery path for everything downstream of it.

## DLQ is filling

Alarm: `$P-dlq-not-empty`. A message was received `maxReceiveCount` times and failed
each time. Messages stay in the DLQ for 14 days.

**Find out what and why.**

```sh
make redrive ARGS=--dry-run            # emulator: lists vendor and archive key per message
aws sqs receive-message --queue-url "$DLQ_URL" --max-number-of-messages 10 --visibility-timeout 0
```

Each message is a pointer: vendor, archive key, digest, correlation id. Search the
processor's log group for the archive key or the message id:

```
fields @timestamp, error_type, error, receive_count
| filter message = "processing failed"
| stats count() by error_type, error
```

**Decide.**

| What the log says | Meaning | Action |
|---|---|---|
| `ThrottlingException`, `ProvisionedThroughputExceededException`, timeouts, 5xx from S3/DynamoDB/KMS | A dependency was unavailable for longer than the retries lasted | Confirm it has recovered, then redrive |
| `AccessDenied`, `KMS.*Exception` | A permission or key policy changed | Fix the policy, then redrive |
| `archived object does not match the pointer digest`, `NoSuchKey` | The archive object was altered or removed | Stop. This is an integrity incident, not an operational one. Check S3 access logs and object versions for the key |
| A Python exception type (`Inexact`, `KeyError`, `ValueError`, ...) | A payload hit a case the code does not handle | Read the payload (below), fix the code so it is processed or quarantined, deploy, then redrive |

**Read the payload** (needs a role on the raw bucket's reader list and `kms:Decrypt`
on the raw key; this is PII, do not paste it anywhere):

```sh
aws s3 cp "s3://$RAW_BUCKET/<archive key>" - | head -c 2000
```

**Redrive** once the cause is fixed. Redriving before that sends the messages round
the same loop.

```sh
make redrive                           # emulator
aws sqs start-message-move-task --source-arn "$DLQ_ARN" --destination-arn "$QUEUE_ARN"
```

Redrive is safe to repeat: reprocessing an event that already succeeded is a no-op.

The demo's three poison messages are this case. They carry a 45-digit amount, which
passes schema validation and cannot be stored in DynamoDB. The fix is a bound on
`amount_minor` in `normalize.py` so the payload is quarantined as `schema_invalid`.

## Signature failures spike

Alarm: `$P-signature-rejections-<vendor>`. More than the threshold of `bad_signature`
rejections for one vendor in five minutes. The vendor is receiving 401s and will
retry, so nothing is lost yet; the clock is the vendor's retry window (days).

**Most likely: the vendor rotated their signing secret.**

1. Check the rejection reasons for the vendor on the dashboard. `bad_signature` (or
   `unknown_key` for veritybureau) on essentially every request from one vendor, with
   other vendors healthy, is a rotation.
2. Get the new secret from the vendor's portal. Add it as the first key and keep the
   old one:

   ```sh
   aws secretsmanager put-secret-value --secret-id "$P/vendors/<vendor>" \
     --secret-string '{"keys":[{"id":"2027-01","secret":"<new>"},{"id":"2026-11","secret":"<old>"}]}'
   ```

3. Nothing needs deploying. Each receiver environment re-reads the secret on its next
   failed verification (at most once every 30 seconds per vendor) and at the latest
   after its five-minute cache expires.
4. Watch `RequestsAccepted` for the vendor recover. The vendor's retries of the
   rejected deliveries arrive re-signed with the new secret and are accepted.
5. When the vendor confirms the old secret is retired, remove it from the list.

**If it is not a rotation.**

- `timestamp_out_of_tolerance` on many requests: clock skew at the vendor, or their
  retry queue is delivering requests signed more than five minutes ago. Authentic,
  rejected by design. Talk to the vendor; widening `signature_tolerance_seconds`
  widens the replay window for everyone.
- `bad_signature` from a few source addresses, other traffic from the vendor fine:
  someone is probing. Nothing is archived or processed for a rejected request. Check
  the API access log for source IPs and consider a WAF rule; HTTP APIs need
  CloudFront in front to attach one.
- `missing_header` or `malformed_header` on all of a vendor's traffic: the vendor
  changed their signing scheme, or a proxy in between is stripping headers.

## Queue age is growing

Alarm: `$P-queue-age`. The oldest message has been waiting more than five minutes.
Accepted webhooks are safe; state and the curated tables are falling behind.

1. **Is the processor failing or just slow?** Look at processor errors. If every
   invocation fails (a bad deploy, a revoked permission), messages cycle until they
   reach the DLQ. Roll back or fix; see "Function error rate".
2. **Is it throttled?** `Throttles` on the processor, or `ThrottledRequests` on either
   table. On-demand tables double their previous peak instantly and then need time;
   a burst far beyond anything seen before can throttle briefly.
3. **Is it simply a backlog?** A vendor replaying a day of events is normal. The
   consumer is capped at `processor_max_concurrency` (default 10) to protect the
   tables. If the backlog is legitimate and the tables are comfortable, raise it:

   ```sh
   aws lambda update-event-source-mapping --uuid "$ESM_UUID" --scaling-config MaximumConcurrency=50
   ```

   and put it back in Terraform afterwards, or the next apply reverts it.
4. **Is one message poisoning batches?** It should not: failures are reported per
   message. If `ProcessingErrors` is high with few distinct messages, check that the
   event source mapping still has `ReportBatchItemFailures`.

To stop consumption entirely during an incident (messages are kept for four days):

```sh
aws lambda update-event-source-mapping --uuid "$ESM_UUID" --no-enabled
```

## Function error rate

Alarms: `$P-receiver-error-rate`, `$P-processor-error-rate`, `$P-api-5xx`.

**Receiver.** A receiver error is a 5xx to the vendor. Nothing was acknowledged, so
nothing is lost, but the vendor's patience is finite.

- Right after a deploy: roll back first, investigate second.
- `receiver failed` in the log with `AccessDenied`, `KMS...` or `ResourceNotFound`:
  the raw bucket, the queue, a key or a secret is not reachable. A vendor secret with
  no value (never populated after `terraform apply`) fails this way for that vendor
  only.
- S3 or SQS unavailable: nothing to do but wait; the design relies on vendor retries
  for exactly this.

**Processor.** Errors here are retries, then DLQ. See "DLQ is filling".

## Curation pipeline failed

Alarm: `$P-curation-pipeline-failed`, and a message on the alerts topic with the
failing step's error. The state table is unaffected; the Athena tables are stale or
suspect.

Open the failed execution in Step Functions; the failed state names the step.

| Step | Typical cause | Action |
|---|---|---|
| Compact | Out of memory on a very large partition; a corrupt landing object | Raise `compactor_memory_mb`. For a corrupt object, move it aside; the events are still in the archive and can be replayed |
| UpdateCatalog | Glue throttling or a permission change | Re-run; the step is idempotent and takes no input |
| DataQuality | A check found violations. The error lists the checks and the report's S3 key | Treat as a data incident. Read the report under `dq-results/`, query the rows, find the processor or compactor change that produced them |
| Reconcile | Ledger and compacted counts differ for a vendor | See below |

**Reconciliation mismatch.** The error carries both counts per vendor.

- `compacted < ledger`: events the processor finished are missing from the table.
  Check that compaction ran for those partitions and that no landing objects expired
  before being folded in (a compactor that was down for longer than
  `landing_retention_days`). Recover with a forced replay of the affected hours.
- `compacted > ledger`: events landed but were never marked processed. Usually a
  message that failed after its landing write and is in retry or in the DLQ. Clear
  the DLQ and the next run reconciles.

Re-run the pipeline by hand at any time; it is idempotent:

```sh
aws stepfunctions start-execution --state-machine-arn "$STATE_MACHINE_ARN" --input '{}'
```

## Replay procedure

Use when state or the curated tables need rebuilding from the archive: after fixing a
normaliser bug, after recovering a table, or to backfill a new consumer.

Replay lists archive objects for the vendors and hours you choose and enqueues a
pointer for each. It does not go back through the receiver.

1. **Scope it.** Vendor and hour range, UTC. Start with a dry run:

   ```sh
   uv run python -m webhook_tools.replay --vendor meridianpay --from 2026-11-17T09 --to 2026-11-17T11 --dry-run
   ```

2. **Choose the mode.**
   - Default: events the ledger already has are skipped. Use this to reprocess
     payloads that were quarantined or dead-lettered before a fix. It changes nothing
     for events that succeeded.
   - `--ignore-idempotency`: every event is processed again. Use this after a
     normaliser change that should alter curated output, or when the ledger itself was
     lost. State cannot move backwards (the ordering condition still applies), and the
     compactor keeps one row per event.
3. **Run it**, then watch queue depth return to zero and wait for the next pipeline
   run.
4. **Check.** Reconciliation in the next pipeline run is the check that the tables
   agree with the ledger.

What replay cannot do: change state to an *older* version, or change a row the
compactor already has (it keeps the first copy of each event). After a normaliser fix
that should rewrite existing rows, delete the affected partitions' Parquet files and
manifests under `compacted/` first, then force-replay those hours.

The emulator tooling (`src/webhook_tools/replay.py`) is built on the local sandbox
and refuses any endpoint that is not loopback. Running a replay on AWS means the same
logic with real credentials and a role on the raw bucket's reader list; that variant
is not in this repository.

## Onboarding a vendor

1. Add the vendor to `vendors.py` (signing scheme) and `normalize.py` (mapping), with
   tests, and to the `vendors` variable. Deploy. Terraform creates an empty secret.
2. Put the signing key in the secret (`{"keys":[{"id":...,"secret":...}]}`).
3. Give the vendor `<webhook_base_url>webhooks/<vendor>`.

Until step 2, that vendor's requests fail with a 5xx and are retried by the vendor.

## One-time setup after the first apply

- Populate each vendor secret (above).
- Populate the PII pepper once, with at least 32 random bytes, and never casually
  again: `{"pepper": "<64 hex characters>"}`. Changing it changes every token.
- Confirm the alert email subscriptions.
