output "platform" {
  description = "Everything the demo tools need to find the deployed stack."
  value       = module.platform
}

output "alert_sink_queue_url" {
  description = "Queue that receives a copy of every alert."
  value       = aws_sqs_queue.alert_sink.url
}

output "lambda_architecture" {
  description = "Architecture the functions were deployed for."
  value       = var.lambda_architecture
}
