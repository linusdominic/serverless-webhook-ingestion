# Everything that persists: KMS keys (kms.tf), buckets (s3.tf), tables (dynamodb.tf).

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
  region     = data.aws_region.current.region
  root_arn   = "arn:${local.partition}:iam::${local.account_id}:root"

  idempotency_ingest_index = "by-ingest-day"
}
