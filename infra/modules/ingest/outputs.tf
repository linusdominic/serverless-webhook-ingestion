output "api_id" {
  description = "HTTP API id."
  value       = aws_apigatewayv2_api.webhooks.id
}

output "api_endpoint" {
  description = "Base invoke URL of the HTTP API. Vendors post to <api_endpoint>/webhooks/<vendor>."
  value       = aws_apigatewayv2_stage.default.invoke_url
}

output "queue_url" {
  description = "Ingest queue URL."
  value       = aws_sqs_queue.ingest.url
}

output "queue_arn" {
  description = "Ingest queue ARN."
  value       = aws_sqs_queue.ingest.arn
}

output "queue_name" {
  description = "Ingest queue name."
  value       = aws_sqs_queue.ingest.name
}

output "dlq_url" {
  description = "Dead-letter queue URL."
  value       = aws_sqs_queue.dlq.url
}

output "dlq_arn" {
  description = "Dead-letter queue ARN."
  value       = aws_sqs_queue.dlq.arn
}

output "dlq_name" {
  description = "Dead-letter queue name."
  value       = aws_sqs_queue.dlq.name
}

output "receiver_function_name" {
  description = "Receiver function name."
  value       = module.receiver.function_name
}

output "receiver_log_group_name" {
  description = "Receiver log group."
  value       = module.receiver.log_group_name
}

output "signing_secret_names" {
  description = "Secrets Manager names of the per-vendor signing secrets."
  value       = { for vendor, secret in aws_secretsmanager_secret.vendor : vendor => secret.name }
}

output "signing_secret_arns" {
  description = "Secrets Manager ARNs of the per-vendor signing secrets."
  value       = { for vendor, secret in aws_secretsmanager_secret.vendor : vendor => secret.arn }
}
