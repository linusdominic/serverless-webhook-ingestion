# One function, one role, one log group. The role can write to its own log group and
# do what var.policy_json says. Nothing is shared between functions.

data "aws_iam_policy_document" "assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/aws/lambda/${var.name}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
  tags              = var.tags
}

resource "aws_iam_role" "this" {
  name               = var.name
  description        = "Execution role for the ${var.name} function"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "logging" {
  statement {
    sid       = "WriteOwnLogGroup"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.this.arn}:*"]
  }

  dynamic "statement" {
    for_each = var.tracing_mode == "Active" ? [1] : []

    content {
      sid     = "SendTraces"
      effect  = "Allow"
      actions = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
      # X-Ray write actions do not support resource-level permissions.
      resources = ["*"]
    }
  }
}

resource "aws_iam_role_policy" "logging" {
  name   = "logging"
  role   = aws_iam_role.this.id
  policy = data.aws_iam_policy_document.logging.json
}

resource "aws_iam_role_policy" "function" {
  name   = "function"
  role   = aws_iam_role.this.id
  policy = var.policy_json
}

resource "aws_lambda_function" "this" {
  # checkov:skip=CKV_AWS_116: A function DLQ only receives failed asynchronous invocations. None of these functions is invoked asynchronously: API Gateway and Step Functions call synchronously, and the SQS consumer's failures go to the queue's own DLQ through its redrive policy.
  # checkov:skip=CKV_AWS_117: The functions call AWS service APIs and nothing else. There is no VPC resource to reach, and a VPC would add NAT gateways or seven interface endpoints to do the same thing.
  # checkov:skip=CKV_AWS_272: Code signing is not configured. It belongs to the release pipeline of whoever deploys this (an AWS Signer profile and a signing step); docs/deploying-to-aws.md lists it as work to do before production.
  function_name = var.name
  description   = var.description
  role          = aws_iam_role.this.arn

  runtime       = var.runtime
  handler       = var.handler
  architectures = [var.architecture]
  memory_size   = var.memory_mb
  timeout       = var.timeout_seconds

  filename         = var.package_path
  source_code_hash = filebase64sha256(var.package_path)

  kms_key_arn                    = var.kms_key_arn
  reserved_concurrent_executions = var.reserved_concurrency

  environment {
    variables = var.environment
  }

  tracing_config {
    mode = var.tracing_mode
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.this.name
  }

  tags = var.tags

  # The role must be able to log before the first invocation.
  depends_on = [aws_iam_role_policy.logging, aws_iam_role_policy.function]
}
