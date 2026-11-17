output "webhook_base_url" {
  description = "Give vendors <webhook_base_url>webhooks/<vendor>."
  value       = module.platform.api_endpoint
}

output "signing_secret_names" {
  description = "Secrets to populate with each vendor's signing keys before go-live (docs/runbook.md)."
  value       = module.platform.signing_secret_names
}

output "pii_pepper_secret_name" {
  description = "Secret to populate once with the PII tokenisation key."
  value       = module.platform.pii_pepper_secret_name
}

output "raw_bucket" {
  description = "Raw archive bucket."
  value       = module.platform.raw_bucket
}

output "curated_bucket" {
  description = "Curated bucket."
  value       = module.platform.curated_bucket
}

output "glue_database" {
  description = "Glue database to query in Athena."
  value       = module.platform.glue_database
}

output "athena_workgroup" {
  description = "Athena workgroup to query with."
  value       = module.platform.athena_workgroup
}

output "dashboard_name" {
  description = "CloudWatch dashboard."
  value       = module.platform.dashboard_name
}

output "alarm_names" {
  description = "CloudWatch alarms."
  value       = module.platform.alarm_names
}
