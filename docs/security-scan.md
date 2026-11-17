# Static security scan

`make security-scan` runs checkov 3.4.5 over `infra/` (every module and both roots).

Result of the run recorded in the README: **533 checks passed, 0 failed, 68 skipped.**
The 68 are the suppressions below, counted once per resource instance in each of the
two roots (six functions share three suppressions, for example).

The rule for this repository is that the scan must come back with no failed checks,
and that a finding is either fixed or suppressed in place with its reason. Nothing is
suppressed globally or by configuration file.

## Findings that were fixed

| Check | Finding | Fix |
|---|---|---|
| CKV_AWS_285 | State machine log did not include execution data | Enabled. State input and output are counts, partition names and query ids; no payloads |
| CKV_AWS_300 | Raw bucket had no bucket-wide rule to abort incomplete multipart uploads (the existing one was scoped to a prefix) | Added a rule with an empty filter |
| CKV_AWS_338 | The emulator root shortened log retention to 7 days | Removed the override; both roots keep logs 365 days |

## Findings that are suppressed, and why

| Check | Where | Reason |
|---|---|---|
| CKV2_AWS_57 | `aws_secretsmanager_secret.vendor` in `modules/ingest/main.tf` | These are vendor-issued HMAC secrets. Rotation is initiated by the vendor, not by a rotation Lambda; the secret holds current and previous keys to make that seamless. |
| CKV2_AWS_57 | `aws_secretsmanager_secret.pii_pepper` in `modules/processing/main.tf` | Rotating this key changes every token and breaks joins against existing curated data. It is rotated deliberately with a re-tokenising replay, not on a timer. |
| CKV2_AWS_62 | `aws_s3_bucket.access_logs` in `modules/storage/s3.tf` | Nothing consumes events from the log bucket. |
| CKV2_AWS_62 | `aws_s3_bucket.curated` in `modules/storage/s3.tf` | Compaction is scheduled and finds new objects by listing; see ADR 0005. |
| CKV2_AWS_62 | `aws_s3_bucket.raw` in `modules/storage/s3.tf` | The receiver enqueues its own pointer after the put; an S3 notification would be a second, weaker path. |
| CKV_AWS_109 | `aws_iam_policy_document.data_key` in `modules/storage/kms.tf` | A key policy applies only to the key it is attached to, so "*" is the only valid resource. This statement delegates to IAM, where access is scoped per role. |
| CKV_AWS_109 | `aws_iam_policy_document.raw_key` in `modules/storage/kms.tf` | A key policy applies only to the key it is attached to, so "*" is the only valid resource. This statement delegates to IAM, where access is scoped per role. |
| CKV_AWS_111 | `aws_iam_policy_document.data_key` in `modules/storage/kms.tf` | A key policy applies only to the key it is attached to, so "*" is the only valid resource. This statement delegates to IAM, where access is scoped per role. |
| CKV_AWS_111 | `aws_iam_policy_document.raw_key` in `modules/storage/kms.tf` | A key policy applies only to the key it is attached to, so "*" is the only valid resource. This statement delegates to IAM, where access is scoped per role. |
| CKV_AWS_116 | `aws_lambda_function.this` in `modules/lambda_function/main.tf` | A function DLQ only receives failed asynchronous invocations. None of these functions is invoked asynchronously: API Gateway and Step Functions call synchronously, and the SQS consumer's failures go to the queue's own DLQ through its redrive policy. |
| CKV_AWS_117 | `aws_lambda_function.this` in `modules/lambda_function/main.tf` | The functions call AWS service APIs and nothing else. There is no VPC resource to reach, and a VPC would add NAT gateways or seven interface endpoints to do the same thing. |
| CKV_AWS_144 | `aws_s3_bucket.access_logs` in `modules/storage/s3.tf` | Single-region by design. See the README, known limitations. |
| CKV_AWS_144 | `aws_s3_bucket.curated` in `modules/storage/s3.tf` | Single-region by design. Everything here can be rebuilt from the raw archive. |
| CKV_AWS_144 | `aws_s3_bucket.raw` in `modules/storage/s3.tf` | Single-region by design. See the README, known limitations. |
| CKV_AWS_145 | `aws_s3_bucket.access_logs` in `modules/storage/s3.tf` | SSE-S3 on purpose. It is the encryption the S3 log delivery service is documented to support for a target bucket. |
| CKV_AWS_18 | `aws_s3_bucket.access_logs` in `modules/storage/s3.tf` | This is the access log target; logging it to itself would recurse. |
| CKV_AWS_21 | `aws_s3_bucket.access_logs` in `modules/storage/s3.tf` | Access logs are append-only objects written once by S3; versioning adds cost and no protection. |
| CKV_AWS_27 | `aws_sqs_queue.alert_sink` in `envs/local/main.tf` | Emulator-only test fixture holding alarm notifications, no customer data. |
| CKV_AWS_272 | `aws_lambda_function.this` in `modules/lambda_function/main.tf` | Code signing is not configured. It belongs to the release pipeline of whoever deploys this (an AWS Signer profile and a signing step); docs/deploying-to-aws.md lists it as work to do before production. |
| CKV_AWS_290 | `aws_iam_role_policy.pipeline` in `modules/analytics/pipeline.tf` | The same statement: logs:PutResourcePolicy and the other log-delivery actions are required by Step Functions logging and do not accept a resource. |
| CKV_AWS_309 | `aws_apigatewayv2_route.webhook` in `modules/ingest/main.tf` | Requests are authenticated by a per-vendor HMAC over the body, checked in the receiver. An HTTP API authorizer cannot see the body, so it cannot do this check. |
| CKV_AWS_355 | `aws_iam_role_policy.pipeline` in `modules/analytics/pipeline.tf` | Log delivery and X-Ray actions do not accept a resource; every other statement is scoped. |
| CKV_AWS_356 | `aws_iam_policy_document.data_key` in `modules/storage/kms.tf` | A key policy applies only to the key it is attached to, so "*" is the only valid resource. This statement delegates to IAM, where access is scoped per role. |
| CKV_AWS_356 | `aws_iam_policy_document.raw_key` in `modules/storage/kms.tf` | A key policy applies only to the key it is attached to, so "*" is the only valid resource. This statement delegates to IAM, where access is scoped per role. |

## What a static scan does not tell you

checkov reads the Terraform. It cannot say whether the policies do what they are meant
to: whether the receiver is in fact unable to read the archive, or whether a role
outside the reader list is in fact denied. The emulator does not evaluate IAM or
bucket policies either. Those properties are designed and reviewed, not tested; the
README's "Emulator versus real AWS" section says so.
