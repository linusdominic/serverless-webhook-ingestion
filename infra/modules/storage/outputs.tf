output "raw_bucket_name" {
  description = "Raw archive bucket."
  value       = aws_s3_bucket.raw.id
}

output "raw_bucket_arn" {
  description = "Raw archive bucket ARN."
  value       = aws_s3_bucket.raw.arn
}

output "curated_bucket_name" {
  description = "Curated bucket: landing, compacted, quarantine, query results."
  value       = aws_s3_bucket.curated.id
}

output "curated_bucket_arn" {
  description = "Curated bucket ARN."
  value       = aws_s3_bucket.curated.arn
}

output "access_logs_bucket_name" {
  description = "S3 server access log bucket."
  value       = aws_s3_bucket.access_logs.id
}

output "raw_kms_key_arn" {
  description = "KMS key for the raw archive."
  value       = aws_kms_key.raw.arn
}

output "data_kms_key_arn" {
  description = "KMS key for derived data, queues, tables, secrets and logs."
  value       = aws_kms_key.data.arn
}

output "state_table_name" {
  description = "Entity state table."
  value       = aws_dynamodb_table.state.name
}

output "state_table_arn" {
  description = "Entity state table ARN."
  value       = aws_dynamodb_table.state.arn
}

output "idempotency_table_name" {
  description = "Idempotency ledger table."
  value       = aws_dynamodb_table.idempotency.name
}

output "idempotency_ingest_index_name" {
  description = "Index on the ledger keyed by vendor and ingest date, read by reconciliation."
  value       = local.idempotency_ingest_index
}

output "idempotency_table_arn" {
  description = "Idempotency ledger table ARN."
  value       = aws_dynamodb_table.idempotency.arn
}
