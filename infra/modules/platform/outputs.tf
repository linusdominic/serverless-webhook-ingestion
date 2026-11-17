output "name_prefix" {
  description = "Prefix of every resource name."
  value       = local.name_prefix
}

output "api_id" {
  description = "HTTP API id."
  value       = module.ingest.api_id
}

output "api_endpoint" {
  description = "Base URL vendors post to: <api_endpoint>webhooks/<vendor>."
  value       = module.ingest.api_endpoint
}

output "raw_bucket" {
  description = "Raw archive bucket."
  value       = module.storage.raw_bucket_name
}

output "curated_bucket" {
  description = "Curated bucket."
  value       = module.storage.curated_bucket_name
}

output "raw_kms_key_arn" {
  description = "KMS key of the raw archive."
  value       = module.storage.raw_kms_key_arn
}

output "data_kms_key_arn" {
  description = "KMS key of derived data."
  value       = module.storage.data_kms_key_arn
}

output "queue_url" {
  description = "Ingest queue URL."
  value       = module.ingest.queue_url
}

output "dlq_url" {
  description = "Dead-letter queue URL."
  value       = module.ingest.dlq_url
}

output "state_table" {
  description = "Entity state table."
  value       = module.storage.state_table_name
}

output "idempotency_table" {
  description = "Idempotency ledger table."
  value       = module.storage.idempotency_table_name
}

output "signing_secret_names" {
  description = "Per-vendor signing secret names."
  value       = module.ingest.signing_secret_names
}

output "pii_pepper_secret_name" {
  description = "PII tokenisation key secret name."
  value       = module.processing.pii_pepper_secret_name
}

output "glue_database" {
  description = "Glue database."
  value       = module.analytics.glue_database_name
}

output "landing_table" {
  description = "Landing table."
  value       = module.analytics.landing_table_name
}

output "compacted_table" {
  description = "Compacted table."
  value       = module.analytics.compacted_table_name
}

output "athena_workgroup" {
  description = "Athena workgroup."
  value       = module.analytics.athena_workgroup_name
}

output "state_machine_arn" {
  description = "Curation state machine ARN."
  value       = module.analytics.state_machine_arn
}

output "alerts_topic_arn" {
  description = "Alerts topic ARN."
  value       = module.observability.alerts_topic_arn
}

output "dlq_alarm_name" {
  description = "DLQ depth alarm name."
  value       = module.observability.dlq_alarm_name
}

output "alarm_names" {
  description = "All alarm names."
  value       = module.observability.alarm_names
}

output "dashboard_name" {
  description = "CloudWatch dashboard name."
  value       = module.observability.dashboard_name
}

output "function_names" {
  description = "Function names by role."
  value = merge(module.analytics.function_names, {
    receiver  = module.ingest.receiver_function_name
    processor = module.processing.processor_function_name
  })
}

output "log_group_names" {
  description = "Function log groups by role."
  value = merge(module.analytics.log_group_names, {
    receiver  = module.ingest.receiver_log_group_name
    processor = module.processing.processor_log_group_name
  })
}

output "event_source_mapping_uuid" {
  description = "SQS event source mapping id."
  value       = module.processing.event_source_mapping_uuid
}
