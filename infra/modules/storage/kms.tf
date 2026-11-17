# Two keys, because there are two classes of data.
#
#   raw   encrypts the raw archive, the only place unredacted vendor payloads (PII
#         included) exist. Decrypt is granted to the processor and to whoever runs
#         replays. Nobody else can read the archive even with s3:GetObject.
#   data  encrypts everything derived: curated objects, queues, tables, secrets,
#         logs, notifications.

data "aws_iam_policy_document" "raw_key" {
  # checkov:skip=CKV_AWS_109: A key policy applies only to the key it is attached to, so "*" is the only valid resource. This statement delegates to IAM, where access is scoped per role.
  # checkov:skip=CKV_AWS_111: A key policy applies only to the key it is attached to, so "*" is the only valid resource. This statement delegates to IAM, where access is scoped per role.
  # checkov:skip=CKV_AWS_356: A key policy applies only to the key it is attached to, so "*" is the only valid resource. This statement delegates to IAM, where access is scoped per role.
  statement {
    sid       = "DelegateToIam"
    effect    = "Allow"
    actions   = ["kms:*"]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = [local.root_arn]
    }
  }
}

resource "aws_kms_key" "raw" {
  description             = "${var.name_prefix}: raw webhook archive"
  enable_key_rotation     = true
  deletion_window_in_days = var.kms_deletion_window_days
  policy                  = data.aws_iam_policy_document.raw_key.json
  tags                    = merge(var.tags, { DataClass = "raw-pii" })
}

resource "aws_kms_alias" "raw" {
  name          = "alias/${var.name_prefix}-raw"
  target_key_id = aws_kms_key.raw.key_id
}

data "aws_iam_policy_document" "data_key" {
  # checkov:skip=CKV_AWS_109: A key policy applies only to the key it is attached to, so "*" is the only valid resource. This statement delegates to IAM, where access is scoped per role.
  # checkov:skip=CKV_AWS_111: A key policy applies only to the key it is attached to, so "*" is the only valid resource. This statement delegates to IAM, where access is scoped per role.
  # checkov:skip=CKV_AWS_356: A key policy applies only to the key it is attached to, so "*" is the only valid resource. This statement delegates to IAM, where access is scoped per role.
  statement {
    sid       = "DelegateToIam"
    effect    = "Allow"
    actions   = ["kms:*"]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = [local.root_arn]
    }
  }

  statement {
    sid       = "CloudWatchLogsEncryptsLogGroups"
    effect    = "Allow"
    actions   = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"]
    resources = ["*"]

    principals {
      type        = "Service"
      identifiers = ["logs.${local.region}.amazonaws.com"]
    }

    condition {
      test     = "ArnLike"
      variable = "kms:EncryptionContext:aws:logs:arn"
      values   = ["arn:${local.partition}:logs:${local.region}:${local.account_id}:log-group:*"]
    }
  }

  statement {
    sid       = "AlarmsPublishToEncryptedTopic"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey*"]
    resources = ["*"]

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
}

resource "aws_kms_key" "data" {
  description             = "${var.name_prefix}: curated data, queues, tables, secrets, logs"
  enable_key_rotation     = true
  deletion_window_in_days = var.kms_deletion_window_days
  policy                  = data.aws_iam_policy_document.data_key.json
  tags                    = merge(var.tags, { DataClass = "derived" })
}

resource "aws_kms_alias" "data" {
  name          = "alias/${var.name_prefix}-data"
  target_key_id = aws_kms_key.data.key_id
}
