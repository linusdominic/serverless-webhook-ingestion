# Process path: ingest queue -> processor Lambda -> state table, landing zone, ledger.

locals {
  processor_name = "${var.name_prefix}-processor"
}

# Key for PII tokens. Created empty; the value is written out of band, once, and
# never rotated casually: rotating it changes every token.
resource "aws_secretsmanager_secret" "pii_pepper" {
  # checkov:skip=CKV2_AWS_57: Rotating this key changes every token and breaks joins against existing curated data. It is rotated deliberately with a re-tokenising replay, not on a timer.
  name                    = "${var.name_prefix}/pii-pepper"
  description             = "HMAC key for PII tokenisation: {\"pepper\": \"<32+ random bytes, encoded>\"}"
  kms_key_id              = var.data_kms_key_arn
  recovery_window_in_days = var.secret_recovery_window_days
  tags                    = var.tags
}

data "aws_iam_policy_document" "processor" {
  statement {
    sid       = "ConsumeIngestQueue"
    effect    = "Allow"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [var.queue_arn]
  }

  statement {
    sid       = "ReadArchivedPayloads"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${var.raw_bucket_arn}/raw/*"]
  }

  statement {
    # Read-only on the raw key. The processor is the one function that sees PII.
    sid       = "DecryptArchivedPayloads"
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = [var.raw_kms_key_arn]
  }

  statement {
    sid     = "WriteLandingAndQuarantine"
    effect  = "Allow"
    actions = ["s3:PutObject"]
    resources = [
      "${var.curated_bucket_arn}/landing/events/*",
      "${var.curated_bucket_arn}/quarantine/*",
    ]
  }

  statement {
    sid       = "UpdateEntityState"
    effect    = "Allow"
    actions   = ["dynamodb:UpdateItem"]
    resources = [var.state_table_arn]
  }

  statement {
    sid       = "ReadAndWriteLedger"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem"]
    resources = [var.idempotency_table_arn]
  }

  statement {
    sid       = "ReadPiiPepper"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.pii_pepper.arn]
  }

  statement {
    # Queue messages, the pepper, table items, curated objects, own environment.
    sid       = "UseDataKey"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [var.data_kms_key_arn]
  }
}

module "processor" {
  source = "../lambda_function"

  name         = local.processor_name
  description  = "Normalises archived webhooks, applies ordered state updates, writes the curated landing zone"
  handler      = "webhook_platform.handlers.processor.handler"
  package_path = var.package_path
  architecture = var.lambda_architecture

  memory_mb          = var.processor_memory_mb
  timeout_seconds    = var.processor_timeout_seconds
  tracing_mode       = var.tracing_mode
  log_retention_days = var.log_retention_days
  kms_key_arn        = var.data_kms_key_arn
  policy_json        = data.aws_iam_policy_document.processor.json

  environment = {
    SERVICE_NAME         = var.name_prefix
    METRICS_NAMESPACE    = "WebhookIngestion"
    RAW_BUCKET           = var.raw_bucket_name
    CURATED_BUCKET       = var.curated_bucket_name
    CURATED_KMS_KEY_ARN  = var.data_kms_key_arn
    STATE_TABLE          = var.state_table_name
    IDEMPOTENCY_TABLE    = var.idempotency_table_name
    IDEMPOTENCY_TTL_DAYS = tostring(var.idempotency_ttl_days)
    PII_PEPPER_SECRET_ID = aws_secretsmanager_secret.pii_pepper.name
  }

  tags = var.tags
}

resource "aws_lambda_event_source_mapping" "ingest" {
  event_source_arn                   = var.queue_arn
  function_name                      = module.processor.function_arn
  batch_size                         = var.batch_size
  maximum_batching_window_in_seconds = var.batching_window_seconds

  # Per-message failure reporting. Without it one failed message returns the whole
  # batch to the queue.
  function_response_types = ["ReportBatchItemFailures"]

  scaling_config {
    maximum_concurrency = var.max_concurrency
  }

  lifecycle {
    precondition {
      condition     = var.visibility_timeout_seconds >= var.processor_timeout_seconds + var.batching_window_seconds
      error_message = "The queue's visibility timeout must cover the batching window plus the processor timeout, or messages reappear while still being processed."
    }
  }

  tags = var.tags
}
