locals {
  raw_bucket     = "${var.name_prefix}-raw-${local.account_id}"
  curated_bucket = "${var.name_prefix}-curated-${local.account_id}"
  logs_bucket    = "${var.name_prefix}-access-logs-${local.account_id}"
}

# --- access logs ------------------------------------------------------------------

resource "aws_s3_bucket" "access_logs" {
  # checkov:skip=CKV_AWS_18: This is the access log target; logging it to itself would recurse.
  # checkov:skip=CKV_AWS_145: SSE-S3 on purpose. It is the encryption the S3 log delivery service is documented to support for a target bucket.
  # checkov:skip=CKV_AWS_144: Single-region by design. See the README, known limitations.
  # checkov:skip=CKV2_AWS_62: Nothing consumes events from the log bucket.
  # checkov:skip=CKV_AWS_21: Access logs are append-only objects written once by S3; versioning adds cost and no protection.
  bucket        = local.logs_bucket
  force_destroy = !var.deletion_protection
  tags          = merge(var.tags, { DataClass = "access-logs" })
}

resource "aws_s3_bucket_public_access_block" "access_logs" {
  bucket                  = aws_s3_bucket.access_logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id

  rule {
    id     = "expire-access-logs"
    status = "Enabled"

    filter {}

    expiration {
      days = var.access_log_retention_days
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

data "aws_iam_policy_document" "access_logs_bucket" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.access_logs.arn, "${aws_s3_bucket.access_logs.arn}/*"]

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

  statement {
    sid       = "S3LogDelivery"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.access_logs.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["logging.s3.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:${local.partition}:s3:::${local.raw_bucket}", "arn:${local.partition}:s3:::${local.curated_bucket}"]
    }
  }
}

resource "aws_s3_bucket_policy" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id
  policy = data.aws_iam_policy_document.access_logs_bucket.json

  depends_on = [aws_s3_bucket_public_access_block.access_logs]
}

# --- raw archive ------------------------------------------------------------------

resource "aws_s3_bucket" "raw" {
  # checkov:skip=CKV_AWS_144: Single-region by design. See the README, known limitations.
  # checkov:skip=CKV2_AWS_62: The receiver enqueues its own pointer after the put; an S3 notification would be a second, weaker path.
  bucket        = local.raw_bucket
  force_destroy = !var.deletion_protection
  tags          = merge(var.tags, { DataClass = "raw-pii" })
}

resource "aws_s3_bucket_public_access_block" "raw" {
  bucket                  = aws_s3_bucket.raw.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "raw" {
  bucket = aws_s3_bucket.raw.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "raw" {
  bucket = aws_s3_bucket.raw.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "raw" {
  bucket = aws_s3_bucket.raw.id

  rule {
    bucket_key_enabled = true

    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.raw.arn
    }
  }
}

resource "aws_s3_bucket_logging" "raw" {
  bucket        = aws_s3_bucket.raw.id
  target_bucket = aws_s3_bucket.access_logs.id
  target_prefix = "s3/raw/"
}

resource "aws_s3_bucket_lifecycle_configuration" "raw" {
  bucket = aws_s3_bucket.raw.id

  rule {
    id     = "raw-retention"
    status = "Enabled"

    filter {
      prefix = "raw/"
    }

    # No transition to a colder storage class, on purpose. Webhook bodies are a
    # kilobyte or two; Standard-IA and Glacier Instant Retrieval bill every object
    # as at least 128 KB and charge per object moved, so "colder" would cost more.
    # (S3 also skips objects under 128 KB in transitions by default.) If the archive
    # ever needs to be cheaper, the lever is fewer, larger objects, not a class.
    expiration {
      days = var.raw_retention_days
    }

    # The receiver's conditional put never overwrites, so a noncurrent version only
    # exists if someone with admin rights replaced an object by hand. Keep the
    # evidence for a while.
    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }

  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  depends_on = [aws_s3_bucket_versioning.raw]
}

data "aws_iam_policy_document" "raw_bucket" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.raw.arn, "${aws_s3_bucket.raw.arn}/*"]

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

  statement {
    sid       = "DenyPutsNotEncryptedWithTheRawKey"
    effect    = "Deny"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.raw.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "StringNotEquals"
      variable = "s3:x-amz-server-side-encryption-aws-kms-key-id"
      values   = [aws_kms_key.raw.arn]
    }
  }

  statement {
    sid       = "DenyReadsExceptNamedPrincipals"
    effect    = "Deny"
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = ["${aws_s3_bucket.raw.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "ArnNotLike"
      variable = "aws:PrincipalArn"
      values   = concat(var.raw_reader_principal_arns, var.raw_admin_principal_arns)
    }
  }

  statement {
    sid    = "DenyDestructiveActionsExceptAdmins"
    effect = "Deny"
    actions = [
      "s3:DeleteObject",
      "s3:DeleteObjectVersion",
      "s3:PutLifecycleConfiguration",
      "s3:PutBucketPolicy",
      "s3:DeleteBucketPolicy",
      "s3:PutBucketVersioning",
    ]
    resources = [aws_s3_bucket.raw.arn, "${aws_s3_bucket.raw.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "ArnNotLike"
      variable = "aws:PrincipalArn"
      values   = var.raw_admin_principal_arns
    }
  }
}

resource "aws_s3_bucket_policy" "raw" {
  bucket = aws_s3_bucket.raw.id
  policy = data.aws_iam_policy_document.raw_bucket.json

  depends_on = [aws_s3_bucket_public_access_block.raw]
}

# --- curated ----------------------------------------------------------------------

resource "aws_s3_bucket" "curated" {
  # checkov:skip=CKV_AWS_144: Single-region by design. Everything here can be rebuilt from the raw archive.
  # checkov:skip=CKV2_AWS_62: Compaction is scheduled and finds new objects by listing; see ADR 0005.
  bucket        = local.curated_bucket
  force_destroy = !var.deletion_protection
  tags          = merge(var.tags, { DataClass = "derived" })
}

resource "aws_s3_bucket_public_access_block" "curated" {
  bucket                  = aws_s3_bucket.curated.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "curated" {
  bucket = aws_s3_bucket.curated.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "curated" {
  bucket = aws_s3_bucket.curated.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "curated" {
  bucket = aws_s3_bucket.curated.id

  rule {
    bucket_key_enabled = true

    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.data.arn
    }
  }
}

resource "aws_s3_bucket_logging" "curated" {
  bucket        = aws_s3_bucket.curated.id
  target_bucket = aws_s3_bucket.access_logs.id
  target_prefix = "s3/curated/"
}

resource "aws_s3_bucket_lifecycle_configuration" "curated" {
  bucket = aws_s3_bucket.curated.id

  rule {
    id     = "landing-expiry"
    status = "Enabled"

    filter {
      prefix = "landing/"
    }

    expiration {
      days = var.landing_retention_days
    }
  }

  rule {
    id     = "athena-results-expiry"
    status = "Enabled"

    filter {
      prefix = "athena-results/"
    }

    expiration {
      days = 7
    }
  }

  rule {
    id     = "dq-results-expiry"
    status = "Enabled"

    filter {
      prefix = "dq-results/"
    }

    expiration {
      days = 180
    }
  }

  rule {
    # Each compaction replaces a partition's Parquet file, leaving the previous one
    # as a noncurrent version. A week of them is an undo button; more is waste.
    id     = "noncurrent-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 7
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  depends_on = [aws_s3_bucket_versioning.curated]
}

data "aws_iam_policy_document" "curated_bucket" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.curated.arn, "${aws_s3_bucket.curated.arn}/*"]

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

resource "aws_s3_bucket_policy" "curated" {
  bucket = aws_s3_bucket.curated.id
  policy = data.aws_iam_policy_document.curated_bucket.json

  depends_on = [aws_s3_bucket_public_access_block.curated]
}
