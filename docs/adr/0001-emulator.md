# ADR 0001: Verify against Floci, not MiniStack

Status: accepted, 2026-11-17

## Context

The repository has to run for free on a laptop and in CI, and it has to exercise real
service behaviour rather than mocks. LocalStack's community image now requires an
account and token, which rules it out for something a stranger should be able to
clone and run. Two MIT-licensed emulators were candidates: Floci
(`floci/floci:2.5.0`) and MiniStack (`ministackorg/ministack:1.5.41-full`).

I ran the same timed script against both, covering every service the design needs,
before writing any of the platform. What follows is what the script observed on
arm64 macOS with Docker 29. It is a record of two specific versions, not a review of
either project.

## What worked on both

- KMS keys; S3 versioning, default encryption, bucket policies and lifecycle rules
  (stored, not evaluated); S3 conditional writes (`If-None-Match: *` returned 412 on
  an existing key).
- Secrets Manager; SNS to SQS delivery; IAM roles and policies (stored, not enforced).
- DynamoDB conditional writes with numeric comparison,
  `ReturnValuesOnConditionCheckFailure`, and transactions that roll back.
- SQS redrive: with `maxReceiveCount = 3`, a message that kept failing was received
  three times and then moved to the DLQ.
- Lambda SQS event source mapping with `ReportBatchItemFailures`: in a batch of ten,
  the eight good messages were processed exactly once and only the two failing ones
  were retried.
- API Gateway HTTP API with a Lambda proxy integration (payload format 2.0): bodies
  arrived byte-identical for UTF-8 JSON with unusual whitespace, for 256 arbitrary
  bytes (base64-encoded by the gateway), and for an empty body.
- Step Functions: Task (`lambda:invoke`) with Retry and Catch, `sns:publish` and Fail.
  (Pass and Choice were first exercised later, on Floci, by the real state machine.)
- EventBridge Scheduler starting a state machine on `rate(1 minute)`.
- A CloudWatch alarm on a custom metric went to ALARM and notified SNS.
- Athena `SELECT` over Glue tables, both JSON and Parquet, executed by DuckDB.

## What did not work on either

| Behaviour | Observation | Consequence here |
|---|---|---|
| `athena:startQueryExecution.sync` from Step Functions | Floci: "Unsupported resource". MiniStack: `States.Runtime` | The pipeline calls Athena from a Lambda that starts the query and polls |
| Athena `CREATE TABLE AS` to Parquet | Both: "WITH clause is not supported for tables in a duckdb catalog" | Compaction is a Lambda using pyarrow |
| Athena partition metadata | Both read every file under the table location; registered partitions and partition projection made no difference to results | Catalog registration is exercised (the calls happen and are checked), but pruning is not verified |
| Trino-only SQL functions | `date_format`, `from_iso8601_timestamp` failed on both: the engine is DuckDB | Data-quality SQL is restricted to syntax that means the same in both engines |
| Embedded metric format | Neither turned EMF log lines into metrics | Application metrics are emitted and checked for shape; alarms on them are not evaluated |
| Invalid UTF-8 in an `application/json` body | Both altered the bytes before the Lambda saw them | Such a request fails signature verification; documented as a limitation of HTTP APIs |

## Where they differed

| | Floci 2.5.0 | MiniStack 1.5.41 |
|---|---|---|
| Lambda execution | One container per execution environment from `public.ecr.aws/lambda/python:3.12` (Python 3.12.16), memory limit set from the function's `MemorySize` | Default executor ran the handler under the emulator's own Python 3.13.18, whatever runtime was declared. A Docker executor exists; in a short test the SQS-triggered function's writes did not arrive and I did not pursue the configuration |
| SQS metrics in CloudWatch | Published (`ApproximateNumberOfMessagesVisible`, `ApproximateAgeOfOldestMessage`, ...). An alarm on DLQ depth evaluated and fired | None; the same alarm stayed `INSUFFICIENT_DATA` |
| Lambda metrics in CloudWatch | None | `Invocations`, `Errors`, `Duration`, `Throttles` |
| Firehose to S3 | Buffered: 300 records became one GZIP object after 6 s with a 5 s interval. Dynamic partitioning not implemented: objects were written under the literal prefix `!{partitionKeyFromQuery:vendor}` | No buffering: one uncompressed object per record, immediately. Dynamic partition keys applied |
| S3 `HeadObject` encryption fields | Reported only when the put named SSE-KMS explicitly | Reported from the bucket default |
| Athena on an empty table | A Glue table with no objects under its location made every query fail, including `SELECT 1` and queries in other databases | Returned zero rows |
| Athena DDL (`MSCK REPAIR`, `ALTER TABLE ADD PARTITION`) | Accepted | Parser error |
| Trivial handler through the HTTP API | 201 req/s at 8 clients, 131 req/s at 32 | 721 req/s at 8, 1,087 req/s at 32 (in-process executor) |

## Decision

Floci 2.5.0, with its DuckDB sidecar pinned to `floci/floci-duck:0.4.1`.

This repository is about the receive and process path, and that is where Floci is
closer to the real thing: the functions run in the actual Lambda runtime image for
the declared runtime and architecture, with the declared memory limit, and the
queue-depth and queue-age alarms are evaluated rather than merely created. MiniStack
is faster and its Athena is more forgiving, but speed is not what is being proved.

## Consequences

- The emulator needs the Docker socket to start function containers. It is mounted
  into the emulator container and nothing else.
- Floci's Athena reads JSON tables with DuckDB's `read_json_auto`, which infers
  column types from a sample of files instead of taking them from the Glue schema.
  During development a query over the JSON landing table failed once enough files
  existed: one vendor's event ids are UUID-shaped, DuckDB inferred a UUID column from
  the files it sampled, and then could not read the other vendors' ids. The pipeline's
  SQL therefore reads only the Parquet table, whose types travel with the files. The
  landing table is still defined in Terraform and applied, but no query against it is
  part of the proof.
- A fresh deployment has empty tables, and on Floci an empty table breaks Athena. The
  state machine checks whether anything has landed before it runs any SQL. That check
  is also what one would want on AWS (it avoids paying for scans of nothing), so it is
  not emulator-specific code.
- `terraform plan` after a successful apply is not empty on the emulator. It does
  not return the Athena workgroup's result-encryption settings or the Glue database's
  tags, so those two resources show as in-place updates, along with three IAM
  policies whose documents are computed from the database. Applying again is harmless.
- The Terraform provider is configured with `skip_requesting_account_id`. With the
  account id known, the provider calls S3 Control on an account-prefixed hostname
  (`000000000000.floci`), which does not resolve. The Glue resources therefore set
  `catalog_id` explicitly.
- The Lambda base image tag `public.ecr.aws/lambda/python:3.12` is chosen by the
  emulator and moves. The run recorded in the README used Python 3.12.16.
- Everything the emulator does not evaluate is listed in the README under "Emulator
  versus real AWS". Nothing in that list is claimed as verified.
