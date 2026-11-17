# Deploying to AWS

**This has not been done.** `infra/envs/aws` has been formatted, validated, linted
and scanned. It has never been planned or applied against an AWS account, and no
part of this repository has run on real AWS. Everything on this page is what I
expect a first real deployment to need, written from the AWS documentation and from
what the emulator does and does not check. Treat it as a plan to be corrected by
reality, not as a procedure that has worked.

## What a first deployment needs

1. **An account and a deployment role.** The root pins `allowed_account_ids`, so a
   plan with credentials for another account stops immediately. The deployment role's
   ARN must be in `raw_admin_principal_arns`, or the raw bucket policy will deny the
   role that created it the next time it tries to change that policy.
2. **Remote state.** Create the state bucket out of band, then uncomment the `s3`
   backend in `versions.tf`. It uses S3-native locking (`use_lockfile`), so there is
   no DynamoDB lock table.
3. **Packages built for the target architecture.** `make build LAMBDA_ARCH=arm64`.
   `core.zip` is pure Python and architecture-independent; `compactor.zip` carries
   pyarrow wheels for `manylinux_2_28` on the chosen architecture and is about 32 MB,
   under the 50 MB direct-upload limit.
4. **Variables.** Copy `terraform.tfvars.example`.
5. **Secret values**, after the first apply: each vendor's signing keys and the PII
   pepper (see the runbook). Until then the receiver answers 5xx.
6. **Confirm the alert email subscriptions.**

## What I expect to need fixing

The emulator accepts a great deal that AWS will check. In rough order of likelihood:

- **IAM and resource policies.** Nothing here has ever been evaluated. The per-function
  policies were written from each function's actual calls and the service
  authorization reference, but a missing action (for example `kms:Decrypt` in a place
  only AWS notices, or a Glue or Athena permission the engine needs on the caller's
  behalf) is the most probable first failure. The raw bucket's deny statements deserve
  particular care: they are written to lock out everyone but a short list, and a
  mistake in that list locks out the deployer too.
- **KMS key policies.** The data key grants CloudWatch Logs and CloudWatch alarms the
  use they need. Whether every encrypted integration (SNS from alarms, Scheduler with
  a customer key, Step Functions log delivery) works first time is untested.
- **Athena.** The data-quality SQL has only run on DuckDB. It was written in the
  subset I believe is common to Trino, and has not been run on Athena. Partition
  projection on the landing table and registered partitions on the events table have
  been applied to a catalog that ignores them.
- **Step Functions.** The definition ran end to end on the emulator's interpreter.
  Logging configuration and X-Ray were applied but do nothing there.
- **Reserved concurrency.** Left unset. New accounts have a low concurrency limit, and
  reserving capacity fails unless the account limit has been raised.
- **API Gateway.** The emulator serves the API under a path on one host. Throttling
  settings, access-log delivery and the `$default` stage's real invoke URL are
  untested.
- **Timing.** Every duration measured in this repository is an emulator on a laptop.
  Cold starts, concurrency scaling and SQS polling behave differently on AWS.

## Hardening left out

Known gaps, deliberately not built, that I would want before production:

- **Code signing** for Lambda packages (AWS Signer), enforced by a code signing config.
- **A custom domain** with a certificate, so vendors are not configured with an
  `execute-api` URL, and **WAF**, which for an HTTP API means CloudFront in front.
- **Object Lock** on the raw bucket if the retention requirement is regulatory rather
  than operational. The conditional put already prevents overwrites by the receiver;
  Object Lock would prevent them by anyone.
- **Cross-region copy** of the raw bucket. The stack is single-region.
- **An alarm on `unknown_event_type` quarantines**, and on API throttling.
- **Automated deployment.** CI here deploys to the emulator only. A real pipeline
  needs OIDC to a deploy role, a plan on pull request and an apply on merge.

## Cost estimate

**An estimate, not a bill.** Built by hand from public us-east-1 list prices as I
understand them at the time of writing, with the assumptions stated. Nothing has run
on AWS, so no line here is a measurement. Free tiers are ignored. Check current
prices and use the AWS Pricing Calculator before relying on any of it.

Assumptions: 1,000,000 accepted webhooks a month (about one every 2.6 seconds on
average), 1.5 KB bodies, arm64 functions, the 15-minute pipeline schedule (2,880 runs
a month), 14-day landing retention, 365-day log retention, and guessed durations of
50 ms for the receiver at 256 MB and 30 ms per event for the processor at 512 MB.

| Item | Basis | USD / month |
|---|---|---:|
| API Gateway HTTP API | 1M requests at $1.00 per million | 1.00 |
| Lambda requests | about 1.1M at $0.20 per million | 0.22 |
| Lambda duration | about 50,000 GB-seconds at $0.0000133334 | 0.67 |
| SQS | about 1.85M requests (sends, batched receives and deletes, idle long polls) at $0.40 per million | 0.74 |
| DynamoDB writes | about 4 write units per event (state item and its index, ledger item and its index) at $0.625 per million | 2.50 |
| DynamoDB reads | 1 strongly consistent read per event at $0.125 per million | 0.13 |
| DynamoDB storage | about 8 GB after a year at $0.25 per GB | 2.00 |
| S3 raw archive, requests | 1M PUT at $0.005 per thousand, 1M GET at $0.0004 per thousand | 5.40 |
| S3 raw archive, storage | 18 GB after a year at $0.023 per GB (126 GB, $2.90, after seven) | 0.41 |
| S3 curated, landing writes | about 0.5M PUT (small batches at this rate) | 2.50 |
| S3 curated, compactor listing | about 230 LIST calls per run, 2,880 runs | 3.31 |
| S3 curated, everything else | Parquet rewrites, reads, storage | 0.30 |
| KMS | 2 keys at $1.00, about 200k requests at $0.03 per 10k | 2.60 |
| Secrets Manager | 4 secrets at $0.40, about 50k calls | 1.85 |
| CloudWatch Logs | about 2.3 GB ingested at $0.50; about 27 GB stored at steady state at $0.03 | 1.95 |
| CloudWatch custom metrics | 35 to 60 metric-and-dimension combinations at $0.30 | 10.50 to 18.00 |
| CloudWatch alarms and dashboard | 13 alarm metrics at $0.10; one dashboard at $3.00 | 4.30 |
| Athena | 8 queries per run, billed at the 10 MB minimum each: 0.23 TB at $5.00 | 1.13 |
| Step Functions | 8 transitions per run at $0.025 per thousand | 0.58 |
| X-Ray, SNS, Scheduler, Glue catalog | small | 0.50 |
| **Total** | | **about 43 to 50** |

What the table says that is worth knowing before building anything like this:

- **About half of it does not depend on traffic.** Keys, secrets, alarms, the dashboard,
  custom metrics and a pipeline that runs every 15 minutes whether or not anything
  arrived come to roughly $20 to $28 with no webhooks at all.
- **Custom metrics are the largest line.** Every distinct combination of metric name
  and dimension values is billed. Per-vendor, per-reason counters are cheap to write
  in embedded metric format and add up. If the bill mattered more than the breakdown,
  I would keep the vendor dimension, drop the reason dimension from the metric, and
  get reasons from Logs Insights when needed.
- **The archive's cost is the PUT, not the storage.** A million 1.5 KB objects cost
  $5.00 to write and about 3 cents a month to keep. This is also why the raw bucket
  has no transition to a colder storage class: infrequent-access and Glacier classes
  bill every object as at least 128 KB and charge per object moved, which for
  kilobyte objects costs more than leaving them in Standard.
- **Listing is what the home-made compactor costs.** Finding new landing objects by
  listing the prefix is simple and is $3 a month at this volume; it is the first thing
  that stops scaling (README, "What changes at 100x").
- **Athena's 10 MB minimum** makes the data-quality checks cost the same however small
  the table is. Running them hourly instead of every 15 minutes would cut that line
  by three quarters.
