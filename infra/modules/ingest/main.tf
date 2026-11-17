# Receive path: HTTP API -> receiver Lambda -> raw bucket + ingest queue.

locals {
  receiver_name = "${var.name_prefix}-receiver"
  secret_prefix = "${var.name_prefix}/vendors/"
}

# --- vendor signing secrets -------------------------------------------------------
#
# Terraform creates the containers. It never sees a value: values are written by
# whoever onboards the vendor (docs/runbook.md), so no secret is ever in state.

resource "aws_secretsmanager_secret" "vendor" {
  # checkov:skip=CKV2_AWS_57: These are vendor-issued HMAC secrets. Rotation is initiated by the vendor, not by a rotation Lambda; the secret holds current and previous keys to make that seamless.
  for_each = var.vendors

  name                    = "${local.secret_prefix}${each.key}"
  description             = "Webhook signing keys for ${each.key}: {\"keys\":[{\"id\":...,\"secret\":...}]}, current first"
  kms_key_id              = var.data_kms_key_arn
  recovery_window_in_days = var.secret_recovery_window_days
  tags                    = merge(var.tags, { Vendor = each.key })
}

# --- queue ------------------------------------------------------------------------

resource "aws_sqs_queue" "dlq" {
  name                              = "${var.name_prefix}-ingest-dlq"
  message_retention_seconds         = 1209600
  kms_master_key_id                 = var.data_kms_key_arn
  kms_data_key_reuse_period_seconds = 300
  tags                              = var.tags
}

resource "aws_sqs_queue" "ingest" {
  name                              = "${var.name_prefix}-ingest"
  visibility_timeout_seconds        = var.visibility_timeout_seconds
  message_retention_seconds         = var.queue_retention_seconds
  receive_wait_time_seconds         = 20
  kms_master_key_id                 = var.data_kms_key_arn
  kms_data_key_reuse_period_seconds = 300

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = var.max_receive_count
  })

  tags = var.tags
}

resource "aws_sqs_queue_redrive_allow_policy" "dlq" {
  queue_url = aws_sqs_queue.dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.ingest.arn]
  })
}

data "aws_iam_policy_document" "queue_tls" {
  for_each = { ingest = aws_sqs_queue.ingest.arn, dlq = aws_sqs_queue.dlq.arn }

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["sqs:*"]
    resources = [each.value]

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

resource "aws_sqs_queue_policy" "ingest" {
  queue_url = aws_sqs_queue.ingest.id
  policy    = data.aws_iam_policy_document.queue_tls["ingest"].json
}

resource "aws_sqs_queue_policy" "dlq" {
  queue_url = aws_sqs_queue.dlq.id
  policy    = data.aws_iam_policy_document.queue_tls["dlq"].json
}

# --- receiver ---------------------------------------------------------------------

data "aws_iam_policy_document" "receiver" {
  statement {
    sid       = "ReadVendorSigningSecrets"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [for s in aws_secretsmanager_secret.vendor : s.arn]
  }

  statement {
    sid       = "ArchiveRawPayloads"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${var.raw_bucket_arn}/raw/*"]
  }

  statement {
    # Write-only on the raw key: the receiver can encrypt new objects and cannot
    # decrypt anything already archived.
    sid       = "EncryptRawPayloads"
    effect    = "Allow"
    actions   = ["kms:GenerateDataKey"]
    resources = [var.raw_kms_key_arn]
  }

  statement {
    sid       = "EnqueuePointers"
    effect    = "Allow"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.ingest.arn]
  }

  statement {
    # Decrypt covers the signing secrets and the function's own environment;
    # GenerateDataKey is what SendMessage needs on an encrypted queue.
    sid       = "UseDataKey"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [var.data_kms_key_arn]
  }
}

module "receiver" {
  source = "../lambda_function"

  name         = local.receiver_name
  description  = "Verifies vendor webhook signatures, archives the raw body, enqueues a pointer"
  handler      = "webhook_platform.handlers.receiver.handler"
  package_path = var.package_path
  architecture = var.lambda_architecture

  memory_mb            = var.receiver_memory_mb
  timeout_seconds      = var.receiver_timeout_seconds
  reserved_concurrency = var.receiver_reserved_concurrency
  tracing_mode         = var.tracing_mode
  log_retention_days   = var.log_retention_days
  kms_key_arn          = var.data_kms_key_arn
  policy_json          = data.aws_iam_policy_document.receiver.json

  environment = {
    SERVICE_NAME                = var.name_prefix
    METRICS_NAMESPACE           = "WebhookIngestion"
    RAW_BUCKET                  = var.raw_bucket_name
    RAW_KMS_KEY_ARN             = var.raw_kms_key_arn
    QUEUE_URL                   = aws_sqs_queue.ingest.url
    SIGNING_SECRET_PREFIX       = local.secret_prefix
    SIGNATURE_TOLERANCE_SECONDS = tostring(var.signature_tolerance_seconds)
    MAX_BODY_BYTES              = tostring(var.max_body_bytes)
  }

  tags = var.tags
}

# --- HTTP API ---------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "api_access" {
  name              = "/aws/apigateway/${var.name_prefix}-webhooks"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.data_kms_key_arn
  tags              = var.tags
}

resource "aws_apigatewayv2_api" "webhooks" {
  name          = "${var.name_prefix}-webhooks"
  description   = "Vendor webhook intake"
  protocol_type = "HTTP"
  tags          = var.tags
}

resource "aws_apigatewayv2_integration" "receiver" {
  api_id                 = aws_apigatewayv2_api.webhooks.id
  integration_type       = "AWS_PROXY"
  integration_method     = "POST"
  integration_uri        = module.receiver.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "webhook" {
  # checkov:skip=CKV_AWS_309: Requests are authenticated by a per-vendor HMAC over the body, checked in the receiver. An HTTP API authorizer cannot see the body, so it cannot do this check.
  api_id    = aws_apigatewayv2_api.webhooks.id
  route_key = "POST /webhooks/{vendor}"
  target    = "integrations/${aws_apigatewayv2_integration.receiver.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.webhooks.id
  name        = "$default"
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_access.arn
    # No query string, no headers, no body: nothing a vendor sends is logged here.
    format = jsonencode({
      requestId      = "$context.requestId"
      requestTime    = "$context.requestTime"
      routeKey       = "$context.routeKey"
      status         = "$context.status"
      responseLength = "$context.responseLength"
      latencyMs      = "$context.responseLatency"
      integrationMs  = "$context.integrationLatency"
      sourceIp       = "$context.identity.sourceIp"
      error          = "$context.integrationErrorMessage"
    })
  }

  default_route_settings {
    detailed_metrics_enabled = true
    throttling_rate_limit    = var.throttle_rate_per_second
    throttling_burst_limit   = var.throttle_burst
  }

  tags = var.tags
}

resource "aws_lambda_permission" "api" {
  statement_id  = "AllowWebhookApi"
  action        = "lambda:InvokeFunction"
  function_name = module.receiver.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.webhooks.execution_arn}/*/POST/webhooks/{vendor}"
}
