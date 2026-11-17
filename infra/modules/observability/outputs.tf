output "alerts_topic_arn" {
  description = "SNS topic every alarm and the curation pipeline publish to."
  value       = aws_sns_topic.alerts.arn
}

output "alarm_names" {
  description = "Names of the CloudWatch alarms."
  value = concat(
    [
      aws_cloudwatch_metric_alarm.dlq_not_empty.alarm_name,
      aws_cloudwatch_metric_alarm.queue_age.alarm_name,
      aws_cloudwatch_metric_alarm.api_5xx.alarm_name,
      aws_cloudwatch_metric_alarm.pipeline_failed.alarm_name,
    ],
    [for a in aws_cloudwatch_metric_alarm.function_error_rate : a.alarm_name],
    [for a in aws_cloudwatch_metric_alarm.signature_rejections : a.alarm_name],
  )
}

output "dlq_alarm_name" {
  description = "Name of the DLQ depth alarm."
  value       = aws_cloudwatch_metric_alarm.dlq_not_empty.alarm_name
}

output "dashboard_name" {
  description = "CloudWatch dashboard name."
  value       = aws_cloudwatch_dashboard.main.dashboard_name
}
