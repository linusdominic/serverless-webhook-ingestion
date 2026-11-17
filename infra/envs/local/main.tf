# Emulator root. Same platform module as envs/aws, with timings shortened so that a
# poison message reaches the DLQ in seconds instead of a quarter of an hour.

module "platform" {
  source = "../../modules/platform"

  environment            = "local"
  core_package_path      = "${var.build_dir}/core.zip"
  compactor_package_path = "${var.build_dir}/compactor.zip"
  lambda_architecture    = var.lambda_architecture

  # The emulator does not evaluate IAM. These ARNs only have to be well-formed.
  raw_admin_principal_arns = ["arn:aws:iam::000000000000:role/break-glass-admin"]

  # Throwaway environment: everything can be destroyed, secrets vanish immediately.
  deletion_protection         = false
  secret_recovery_window_days = 0

  # Shortened timings. envs/aws uses the module defaults.
  processor_timeout_seconds  = 20
  visibility_timeout_seconds = 25
  max_receive_count          = 3
  batch_size                 = 25
  batching_window_seconds    = 1
  processor_max_concurrency  = 4

  schedule_expression           = var.schedule_expression
  state_machine_timeout_seconds = 55
  reconcile_margin_seconds      = 0
  projection_start_date         = "2026-01-01"

  # The emulator has no X-Ray.
  tracing_mode = "PassThrough"
}

# A queue subscribed to the alerts topic, so the verifier can read what an on-call
# engineer would have been sent. Exists only in this root.
resource "aws_sqs_queue" "alert_sink" {
  # checkov:skip=CKV_AWS_27: Emulator-only test fixture holding alarm notifications, no customer data.
  name                      = "webhook-ingestion-local-alert-sink"
  message_retention_seconds = 3600
}

data "aws_iam_policy_document" "alert_sink" {
  statement {
    effect    = "Allow"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.alert_sink.arn]

    principals {
      type        = "Service"
      identifiers = ["sns.amazonaws.com"]
    }

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [module.platform.alerts_topic_arn]
    }
  }
}

resource "aws_sqs_queue_policy" "alert_sink" {
  queue_url = aws_sqs_queue.alert_sink.id
  policy    = data.aws_iam_policy_document.alert_sink.json
}

resource "aws_sns_topic_subscription" "alert_sink" {
  topic_arn            = module.platform.alerts_topic_arn
  protocol             = "sqs"
  endpoint             = aws_sqs_queue.alert_sink.arn
  raw_message_delivery = true
}
