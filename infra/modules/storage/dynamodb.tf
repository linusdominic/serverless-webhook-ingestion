# Current state per entity. Written only by conditional updates (see ordering.py).
resource "aws_dynamodb_table" "state" {
  name                        = "${var.name_prefix}-entity-state"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "pk"
  deletion_protection_enabled = var.deletion_protection

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "loan_application_id"
    type = "S"
  }

  # "Everything we know about this application": payment status, last call outcome,
  # verification result. One Query instead of three GetItems with guessed keys.
  global_secondary_index {
    name            = "by-loan-application"
    projection_type = "ALL"

    key_schema {
      attribute_name = "loan_application_id"
      key_type       = "HASH"
    }

    key_schema {
      attribute_name = "pk"
      key_type       = "RANGE"
    }
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.data.arn
  }

  tags = merge(var.tags, { DataClass = "derived" })
}

# One item per processed (vendor, event_id). The key is a single string so writes
# spread across partitions; a vendor name as the partition key would put every write
# for a vendor on the same one.
resource "aws_dynamodb_table" "idempotency" {
  name                        = "${var.name_prefix}-idempotency"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "pk"
  deletion_protection_enabled = var.deletion_protection

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "ingest_day"
    type = "S"
  }

  attribute {
    name = "ingested_at"
    type = "S"
  }

  # Reconciliation counts recent records per vendor. Keyed by "<vendor>#<date>" so a
  # count reads one day's index entries instead of scanning the table, and so no
  # single key takes every write for a vendor forever.
  global_secondary_index {
    name            = local.idempotency_ingest_index
    projection_type = "KEYS_ONLY"

    key_schema {
      attribute_name = "ingest_day"
      key_type       = "HASH"
    }

    key_schema {
      attribute_name = "ingested_at"
      key_type       = "RANGE"
    }
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.data.arn
  }

  tags = merge(var.tags, { DataClass = "derived" })
}
