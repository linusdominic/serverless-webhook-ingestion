# Alerts topic, alarms and dashboard.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region
}

resource "aws_sns_topic" "alerts" {
  name              = "${var.name_prefix}-alerts"
  display_name      = "${var.name_prefix} alerts"
  kms_master_key_id = var.data_kms_key_arn
  tags              = var.tags
}

data "aws_iam_policy_document" "alerts_topic" {
  statement {
    sid       = "AlarmsPublish"
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]

    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_sns_topic_policy" "alerts" {
  arn    = aws_sns_topic.alerts.arn
  policy = data.aws_iam_policy_document.alerts_topic.json
}

resource "aws_sns_topic_subscription" "email" {
  for_each = toset(var.alert_email_addresses)

  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = each.value
}

# --- alarms -----------------------------------------------------------------------
#
# Every alarm names the runbook section that says what to do about it.

resource "aws_cloudwatch_metric_alarm" "dlq_not_empty" {
  alarm_name          = "${var.name_prefix}-dlq-not-empty"
  alarm_description   = "A webhook exhausted its receives and is in the dead-letter queue. Runbook: docs/runbook.md#dlq-is-filling"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = var.dlq_name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
  tags                = var.tags
}

resource "aws_cloudwatch_metric_alarm" "queue_age" {
  alarm_name          = "${var.name_prefix}-queue-age"
  alarm_description   = "The oldest message on the ingest queue is older than ${var.queue_age_threshold_seconds}s: the processor is not keeping up or is failing. Runbook: docs/runbook.md#queue-age-is-growing"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateAgeOfOldestMessage"
  dimensions          = { QueueName = var.queue_name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  datapoints_to_alarm = 3
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.queue_age_threshold_seconds
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
  tags                = var.tags
}

resource "aws_cloudwatch_metric_alarm" "function_error_rate" {
  for_each = {
    receiver  = var.receiver_function_name
    processor = var.processor_function_name
  }

  alarm_name          = "${var.name_prefix}-${each.key}-error-rate"
  alarm_description   = "${each.key} error rate above ${var.error_rate_threshold_percent}% over 5 minutes. Runbook: docs/runbook.md#function-error-rate"
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.error_rate_threshold_percent
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]

  metric_query {
    id          = "rate"
    expression  = "IF(invocations > 0, 100 * errors / invocations, 0)"
    label       = "Error rate (%)"
    return_data = true
  }

  metric_query {
    id = "errors"

    metric {
      namespace   = "AWS/Lambda"
      metric_name = "Errors"
      dimensions  = { FunctionName = each.value }
      stat        = "Sum"
      period      = 300
    }
  }

  metric_query {
    id = "invocations"

    metric {
      namespace   = "AWS/Lambda"
      metric_name = "Invocations"
      dimensions  = { FunctionName = each.value }
      stat        = "Sum"
      period      = 300
    }
  }

  tags = var.tags
}

resource "aws_cloudwatch_metric_alarm" "api_5xx" {
  alarm_name          = "${var.name_prefix}-api-5xx"
  alarm_description   = "The webhook API returned 5xx: vendors are being told to retry. Runbook: docs/runbook.md#function-error-rate"
  namespace           = "AWS/ApiGateway"
  metric_name         = "5xx"
  dimensions          = { ApiId = var.api_id }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 3
  datapoints_to_alarm = 2
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
  tags                = var.tags
}

resource "aws_cloudwatch_metric_alarm" "signature_rejections" {
  for_each = var.vendors

  alarm_name          = "${var.name_prefix}-signature-rejections-${each.key}"
  alarm_description   = "${each.key}: bad-signature rejections above ${var.signature_rejection_threshold} in 5 minutes. Usually a rotated secret. Runbook: docs/runbook.md#signature-failures-spike"
  namespace           = var.metrics_namespace
  metric_name         = "RequestsRejected"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = var.signature_rejection_threshold
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]

  dimensions = {
    Function = var.receiver_function_name
    Reason   = "bad_signature"
    Vendor   = each.key
  }

  tags = merge(var.tags, { Vendor = each.key })
}

resource "aws_cloudwatch_metric_alarm" "pipeline_failed" {
  alarm_name          = "${var.name_prefix}-curation-pipeline-failed"
  alarm_description   = "A curation pipeline execution failed or timed out. Runbook: docs/runbook.md#curation-pipeline-failed"
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  metric_query {
    id          = "bad"
    expression  = "failed + timed_out + aborted"
    label       = "Unsuccessful executions"
    return_data = true
  }

  dynamic "metric_query" {
    for_each = { failed = "ExecutionsFailed", timed_out = "ExecutionsTimedOut", aborted = "ExecutionsAborted" }

    content {
      id = metric_query.key

      metric {
        namespace   = "AWS/States"
        metric_name = metric_query.value
        dimensions  = { StateMachineArn = var.state_machine_arn }
        stat        = "Sum"
        period      = 300
      }
    }
  }

  tags = var.tags
}

# --- dashboard --------------------------------------------------------------------

locals {
  app = var.metrics_namespace

  widgets = [
    {
      type = "metric", x = 0, y = 0, width = 12, height = 6
      properties = {
        title  = "API: requests and errors"
        region = local.region
        stat   = "Sum"
        period = 60
        metrics = [
          ["AWS/ApiGateway", "Count", "ApiId", var.api_id],
          [".", "4xx", ".", "."],
          [".", "5xx", ".", "."],
        ]
      }
    },
    {
      type = "metric", x = 12, y = 0, width = 12, height = 6
      properties = {
        title  = "API: latency"
        region = local.region
        period = 60
        metrics = [
          ["AWS/ApiGateway", "Latency", "ApiId", var.api_id, { stat = "p50" }],
          ["...", { stat = "p95" }],
          ["...", { stat = "p99" }],
        ]
      }
    },
    {
      type = "metric", x = 0, y = 6, width = 12, height = 6
      properties = {
        title  = "Receiver: accepted and rejected by reason"
        region = local.region
        stat   = "Sum"
        period = 60
        metrics = [
          [{ expression = "SEARCH('{${local.app},Function,Vendor} MetricName=\"RequestsAccepted\"', 'Sum', 60)", id = "accepted" }],
          [{ expression = "SEARCH('{${local.app},Function,Reason,Vendor} MetricName=\"RequestsRejected\"', 'Sum', 60)", id = "rejected" }],
        ]
      }
    },
    {
      type = "metric", x = 12, y = 6, width = 12, height = 6
      properties = {
        title  = "Queue: depth, age, DLQ"
        region = local.region
        stat   = "Maximum"
        period = 60
        metrics = [
          ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", var.queue_name],
          [".", "ApproximateAgeOfOldestMessage", ".", ".", { yAxis = "right" }],
          [".", "ApproximateNumberOfMessagesVisible", ".", var.dlq_name],
        ]
      }
    },
    {
      type = "metric", x = 0, y = 12, width = 12, height = 6
      properties = {
        title  = "Processor: outcomes"
        region = local.region
        stat   = "Sum"
        period = 60
        metrics = [
          [{ expression = "SEARCH('{${local.app},Function,Outcome,Vendor} MetricName=\"EventsProcessed\"', 'Sum', 60)", id = "processed" }],
          [{ expression = "SEARCH('{${local.app},Function,Vendor} MetricName=\"EventsDuplicate\"', 'Sum', 60)", id = "duplicates" }],
          [{ expression = "SEARCH('{${local.app},Function,Reason,Vendor} MetricName=\"EventsQuarantined\"', 'Sum', 60)", id = "quarantined" }],
        ]
      }
    },
    {
      type = "metric", x = 12, y = 12, width = 12, height = 6
      properties = {
        title  = "Functions: errors and duration"
        region = local.region
        period = 60
        metrics = [
          ["AWS/Lambda", "Errors", "FunctionName", var.receiver_function_name, { stat = "Sum" }],
          ["...", var.processor_function_name, { stat = "Sum" }],
          [".", "Duration", ".", var.receiver_function_name, { stat = "p95", yAxis = "right" }],
          ["...", var.processor_function_name, { stat = "p95", yAxis = "right" }],
        ]
      }
    },
    {
      type = "metric", x = 0, y = 18, width = 24, height = 6
      properties = {
        title  = "Curation pipeline"
        region = local.region
        stat   = "Sum"
        period = 300
        metrics = [
          ["AWS/States", "ExecutionsSucceeded", "StateMachineArn", var.state_machine_arn],
          [".", "ExecutionsFailed", ".", "."],
          [{ expression = "SEARCH('{${local.app},Function,Vendor} MetricName=\"ReconciliationDifference\"', 'Maximum', 300)", id = "recon" }],
        ]
      }
    },
  ]
}

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = "${var.name_prefix}-webhooks"
  dashboard_body = jsonencode({ widgets = local.widgets })
}
