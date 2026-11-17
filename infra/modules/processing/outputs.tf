output "processor_function_name" {
  description = "Processor function name."
  value       = module.processor.function_name
}

output "processor_role_arn" {
  description = "Processor execution role ARN."
  value       = module.processor.role_arn
}

output "processor_log_group_name" {
  description = "Processor log group."
  value       = module.processor.log_group_name
}

output "pii_pepper_secret_name" {
  description = "Secrets Manager name of the PII tokenisation key."
  value       = aws_secretsmanager_secret.pii_pepper.name
}

output "event_source_mapping_uuid" {
  description = "Identifier of the SQS event source mapping, for disabling the consumer during an incident."
  value       = aws_lambda_event_source_mapping.ingest.uuid
}
