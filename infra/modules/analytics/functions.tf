# The four steps of the curation pipeline. Each has its own role.

# --- compactor --------------------------------------------------------------------

data "aws_iam_policy_document" "compactor" {
  statement {
    sid       = "ListLandingAndCompacted"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [var.curated_bucket_arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["landing/events/*", "compacted/*"]
    }
  }

  statement {
    sid       = "ReadLanding"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${var.curated_bucket_arn}/landing/events/*"]
  }

  statement {
    sid       = "ReadAndWriteCompacted"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${var.curated_bucket_arn}/compacted/*"]
  }

  statement {
    sid       = "UseDataKey"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [var.data_kms_key_arn]
  }
}

module "compactor" {
  source = "../lambda_function"

  name         = "${var.name_prefix}-compactor"
  description  = "Folds landing JSON into one deduplicated Parquet file per partition"
  handler      = "webhook_platform.handlers.compactor.handler"
  package_path = var.compactor_package_path
  architecture = var.lambda_architecture

  memory_mb          = var.compactor_memory_mb
  timeout_seconds    = var.compactor_timeout_seconds
  tracing_mode       = var.tracing_mode
  log_retention_days = var.log_retention_days
  kms_key_arn        = var.data_kms_key_arn
  policy_json        = data.aws_iam_policy_document.compactor.json

  environment = merge(local.common_environment, {
    CURATED_BUCKET      = var.curated_bucket_name
    CURATED_KMS_KEY_ARN = var.data_kms_key_arn
  })

  tags = var.tags
}

# --- catalog ----------------------------------------------------------------------

data "aws_iam_policy_document" "catalog" {
  statement {
    sid       = "ListCompactedPartitions"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [var.curated_bucket_arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["compacted/events/*"]
    }
  }

  statement {
    sid     = "RegisterPartitions"
    effect  = "Allow"
    actions = ["glue:GetTable", "glue:GetPartitions", "glue:BatchCreatePartition"]
    resources = [
      "${local.glue_arn_prefix}:catalog",
      "${local.glue_arn_prefix}:database/${aws_glue_catalog_database.curated.name}",
      "${local.glue_arn_prefix}:table/${aws_glue_catalog_database.curated.name}/${aws_glue_catalog_table.compacted.name}",
    ]
  }

  statement {
    sid       = "DecryptEnvironment"
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = [var.data_kms_key_arn]
  }
}

module "catalog" {
  source = "../lambda_function"

  name         = "${var.name_prefix}-catalog"
  description  = "Registers compacted partitions in the Glue Data Catalog"
  handler      = "webhook_platform.handlers.catalog.handler"
  package_path = var.core_package_path
  architecture = var.lambda_architecture

  memory_mb          = 256
  timeout_seconds    = 60
  tracing_mode       = var.tracing_mode
  log_retention_days = var.log_retention_days
  kms_key_arn        = var.data_kms_key_arn
  policy_json        = data.aws_iam_policy_document.catalog.json

  environment = merge(local.common_environment, {
    CURATED_BUCKET       = var.curated_bucket_name
    GLUE_DATABASE        = aws_glue_catalog_database.curated.name
    GLUE_COMPACTED_TABLE = aws_glue_catalog_table.compacted.name
  })

  tags = var.tags
}

# --- athena access shared by quality and reconcile --------------------------------

data "aws_iam_policy_document" "athena_query" {
  statement {
    sid    = "RunQueriesInWorkgroup"
    effect = "Allow"
    actions = [
      "athena:StartQueryExecution",
      "athena:GetQueryExecution",
      "athena:GetQueryResults",
      "athena:StopQueryExecution",
    ]
    resources = [aws_athena_workgroup.curated.arn]
  }

  statement {
    sid       = "ReadCatalog"
    effect    = "Allow"
    actions   = ["glue:GetDatabase", "glue:GetTable", "glue:GetPartitions", "glue:GetPartition"]
    resources = local.glue_read_resources
  }

  statement {
    sid       = "LocateAndListCuratedBucket"
    effect    = "Allow"
    actions   = ["s3:GetBucketLocation", "s3:ListBucket"]
    resources = [var.curated_bucket_arn]
  }

  statement {
    sid     = "ReadCuratedData"
    effect  = "Allow"
    actions = ["s3:GetObject"]
    resources = [
      "${var.curated_bucket_arn}/landing/events/*",
      "${var.curated_bucket_arn}/compacted/events/*",
    ]
  }

  statement {
    sid       = "ReadAndWriteQueryResults"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload"]
    resources = ["${var.curated_bucket_arn}/athena-results/*"]
  }

  statement {
    sid       = "UseDataKey"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [var.data_kms_key_arn]
  }
}

# --- quality ----------------------------------------------------------------------

data "aws_iam_policy_document" "quality" {
  source_policy_documents = [data.aws_iam_policy_document.athena_query.json]

  statement {
    sid       = "WriteQualityReports"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${var.curated_bucket_arn}/dq-results/*"]
  }
}

module "quality" {
  source = "../lambda_function"

  name         = "${var.name_prefix}-quality"
  description  = "Runs the data-quality SQL checks in Athena and fails on any violation"
  handler      = "webhook_platform.handlers.quality.handler"
  package_path = var.core_package_path
  architecture = var.lambda_architecture

  memory_mb          = 256
  timeout_seconds    = 300
  tracing_mode       = var.tracing_mode
  log_retention_days = var.log_retention_days
  kms_key_arn        = var.data_kms_key_arn
  policy_json        = data.aws_iam_policy_document.quality.json

  environment = merge(local.common_environment, {
    CURATED_BUCKET      = var.curated_bucket_name
    CURATED_KMS_KEY_ARN = var.data_kms_key_arn
    GLUE_DATABASE       = aws_glue_catalog_database.curated.name
    ATHENA_WORKGROUP    = aws_athena_workgroup.curated.name
  })

  tags = var.tags
}

# --- reconcile --------------------------------------------------------------------

data "aws_iam_policy_document" "reconcile" {
  source_policy_documents = [data.aws_iam_policy_document.athena_query.json]

  statement {
    # Query on the one index, nothing on the table itself.
    sid       = "CountLedgerByIngestDay"
    effect    = "Allow"
    actions   = ["dynamodb:Query"]
    resources = ["${var.idempotency_table_arn}/index/${var.idempotency_ingest_index_name}"]
  }
}

module "reconcile" {
  source = "../lambda_function"

  name         = "${var.name_prefix}-reconcile"
  description  = "Compares the processor's ledger with the compacted table, per vendor"
  handler      = "webhook_platform.handlers.reconcile.handler"
  package_path = var.core_package_path
  architecture = var.lambda_architecture

  memory_mb          = 256
  timeout_seconds    = 300
  tracing_mode       = var.tracing_mode
  log_retention_days = var.log_retention_days
  kms_key_arn        = var.data_kms_key_arn
  policy_json        = data.aws_iam_policy_document.reconcile.json

  environment = merge(local.common_environment, {
    GLUE_DATABASE            = aws_glue_catalog_database.curated.name
    ATHENA_WORKGROUP         = aws_athena_workgroup.curated.name
    IDEMPOTENCY_TABLE        = var.idempotency_table_name
    IDEMPOTENCY_INGEST_INDEX = var.idempotency_ingest_index_name
    RECONCILE_MARGIN_SECONDS = tostring(var.reconcile_margin_seconds)
    RECONCILE_LOOKBACK_HOURS = tostring(var.reconcile_lookback_hours)
  })

  tags = var.tags
}
