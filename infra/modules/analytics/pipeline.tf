# The curation pipeline: compact -> register partitions -> data quality -> reconcile,
# with any failure published to the alerts topic. Started by EventBridge Scheduler.

locals {
  lambda_retry = [
    {
      ErrorEquals     = ["Lambda.ServiceException", "Lambda.AWSLambdaException", "Lambda.SdkClientException", "Lambda.TooManyRequestsException"]
      IntervalSeconds = 2
      MaxAttempts     = 3
      BackoffRate     = 2
    }
  ]

  catch_all = [
    {
      ErrorEquals = ["States.ALL"]
      ResultPath  = "$.error"
      Next        = "NotifyFailure"
    }
  ]

  pipeline_functions = {
    compactor = module.compactor.function_arn
    catalog   = module.catalog.function_arn
    quality   = module.quality.function_arn
    reconcile = module.reconcile.function_arn
  }

  definition = {
    Comment        = "Compacts landed webhook events to Parquet, updates the catalog, runs data-quality SQL and reconciles counts"
    StartAt        = "Init"
    TimeoutSeconds = var.state_machine_timeout_seconds

    States = {
      Init = {
        Type       = "Pass"
        Result     = { started_at = null }
        ResultPath = "$.compact"
        Next       = "Compact"
      }

      Compact = {
        Type     = "Task"
        Resource = "arn:${local.partition}:states:::lambda:invoke"
        Parameters = {
          FunctionName = local.pipeline_functions.compactor
          Payload      = { "started_at.$" = "$.compact.started_at" }
        }
        ResultSelector = {
          "started_at.$"           = "$.Payload.started_at"
          "partitions_total.$"     = "$.Payload.partitions_total"
          "partitions_rewritten.$" = "$.Payload.partitions_rewritten"
          "more.$"                 = "$.Payload.more"
        }
        ResultPath = "$.compact"
        Retry      = local.lambda_retry
        Catch      = local.catch_all
        Next       = "AnythingLanded"
      }

      # Nothing has ever landed: there is nothing to register, check or reconcile.
      AnythingLanded = {
        Type = "Choice"
        Choices = [
          {
            Variable      = "$.compact.partitions_total"
            NumericEquals = 0
            Next          = "NothingToDo"
          }
        ]
        Default = "UpdateCatalog"
      }

      UpdateCatalog = {
        Type     = "Task"
        Resource = "arn:${local.partition}:states:::lambda:invoke"
        Parameters = {
          FunctionName = local.pipeline_functions.catalog
          Payload      = {}
        }
        ResultSelector = { "registered.$" = "$.Payload.registered" }
        ResultPath     = "$.catalog"
        Retry          = local.lambda_retry
        Catch          = local.catch_all
        Next           = "MoreToCompact"
      }

      # The compactor stops before its timeout and says so; loop until it is done.
      MoreToCompact = {
        Type = "Choice"
        Choices = [
          {
            Variable      = "$.compact.more"
            BooleanEquals = true
            Next          = "Compact"
          }
        ]
        Default = "DataQuality"
      }

      DataQuality = {
        Type     = "Task"
        Resource = "arn:${local.partition}:states:::lambda:invoke"
        Parameters = {
          FunctionName = local.pipeline_functions.quality
          Payload      = {}
        }
        ResultSelector = { "report.$" = "$.Payload.report" }
        ResultPath     = "$.quality"
        Retry          = local.lambda_retry
        Catch          = local.catch_all
        Next           = "Reconcile"
      }

      Reconcile = {
        Type     = "Task"
        Resource = "arn:${local.partition}:states:::lambda:invoke"
        Parameters = {
          FunctionName = local.pipeline_functions.reconcile
          Payload      = { "started_at.$" = "$.compact.started_at" }
        }
        ResultSelector = { "window_end.$" = "$.Payload.window_end", "vendors.$" = "$.Payload.vendors" }
        ResultPath     = "$.reconcile"
        Retry          = local.lambda_retry
        Catch          = local.catch_all
        Next           = "Done"
      }

      NotifyFailure = {
        Type     = "Task"
        Resource = "arn:${local.partition}:states:::sns:publish"
        Parameters = {
          TopicArn    = var.alerts_topic_arn
          Subject     = "${var.name_prefix}: curation pipeline failed"
          "Message.$" = "States.JsonToString($.error)"
        }
        ResultPath = null
        Next       = "Failed"
      }

      Failed = {
        Type  = "Fail"
        Error = "CurationPipelineFailed"
        Cause = "A pipeline step failed. The error was published to the alerts topic."
      }

      NothingToDo = { Type = "Succeed" }
      Done        = { Type = "Succeed" }
    }
  }
}

resource "aws_cloudwatch_log_group" "pipeline" {
  name              = "/aws/vendedlogs/states/${var.name_prefix}-curation"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.data_kms_key_arn
  tags              = var.tags
}

data "aws_iam_policy_document" "assume_states" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

data "aws_iam_policy_document" "pipeline" {
  statement {
    sid       = "InvokePipelineFunctions"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = values(local.pipeline_functions)
  }

  statement {
    sid       = "PublishFailures"
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [var.alerts_topic_arn]
  }

  statement {
    sid       = "PublishToEncryptedTopic"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [var.data_kms_key_arn]
  }

  statement {
    # Step Functions log delivery and X-Ray. These actions do not support
    # resource-level permissions; this is the policy the service documents.
    sid    = "LogDeliveryAndTracing"
    effect = "Allow"
    actions = [
      "logs:CreateLogDelivery",
      "logs:GetLogDelivery",
      "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery",
      "logs:ListLogDeliveries",
      "logs:PutResourcePolicy",
      "logs:DescribeResourcePolicies",
      "logs:DescribeLogGroups",
      "xray:PutTraceSegments",
      "xray:PutTelemetryRecords",
      "xray:GetSamplingRules",
      "xray:GetSamplingTargets",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role" "pipeline" {
  name               = "${var.name_prefix}-curation-pipeline"
  description        = "Execution role for the curation state machine"
  assume_role_policy = data.aws_iam_policy_document.assume_states.json
  tags               = var.tags
}

resource "aws_iam_role_policy" "pipeline" {
  # checkov:skip=CKV_AWS_355: Log delivery and X-Ray actions do not accept a resource; every other statement is scoped.
  # checkov:skip=CKV_AWS_290: The same statement: logs:PutResourcePolicy and the other log-delivery actions are required by Step Functions logging and do not accept a resource.
  name   = "pipeline"
  role   = aws_iam_role.pipeline.id
  policy = data.aws_iam_policy_document.pipeline.json
}

resource "aws_sfn_state_machine" "curation" {
  name       = "${var.name_prefix}-curation"
  role_arn   = aws_iam_role.pipeline.arn
  type       = "STANDARD"
  definition = jsonencode(local.definition)

  logging_configuration {
    log_destination = "${aws_cloudwatch_log_group.pipeline.arn}:*"
    # State input and output here are counts, partition names and query ids: no
    # payloads, no PII. Worth having in the log when a run fails.
    include_execution_data = true
    level                  = "ERROR"
  }

  tracing_configuration {
    enabled = var.tracing_mode == "Active"
  }

  tags = var.tags

  depends_on = [aws_iam_role_policy.pipeline]
}

# --- schedule ---------------------------------------------------------------------

data "aws_iam_policy_document" "assume_scheduler" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

data "aws_iam_policy_document" "scheduler" {
  statement {
    sid       = "StartCurationPipeline"
    effect    = "Allow"
    actions   = ["states:StartExecution"]
    resources = [aws_sfn_state_machine.curation.arn]
  }

  statement {
    sid       = "DecryptSchedulePayload"
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = [var.data_kms_key_arn]
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${var.name_prefix}-curation-schedule"
  description        = "Lets EventBridge Scheduler start the curation state machine"
  assume_role_policy = data.aws_iam_policy_document.assume_scheduler.json
  tags               = var.tags
}

resource "aws_iam_role_policy" "scheduler" {
  name   = "start-execution"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler.json
}

resource "aws_scheduler_schedule" "curation" {
  name                = "${var.name_prefix}-curation"
  description         = "Runs the curation pipeline"
  schedule_expression = var.schedule_expression
  state               = var.schedule_enabled ? "ENABLED" : "DISABLED"
  kms_key_arn         = var.data_kms_key_arn

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_sfn_state_machine.curation.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({})

    retry_policy {
      maximum_retry_attempts = 2
    }
  }
}
