# Glue Data Catalog and Athena workgroup.

locals {
  database_name   = replace("${var.name_prefix}_curated", "-", "_")
  landing_table   = "landing_events"
  compacted_table = "events"

  landing_location   = "s3://${var.curated_bucket_name}/landing/events/"
  compacted_location = "s3://${var.curated_bucket_name}/compacted/events/"
}

resource "aws_glue_catalog_database" "curated" {
  name        = local.database_name
  catalog_id  = local.account_id
  description = "Normalised vendor webhook events. No raw PII: phone numbers and SSN fragments are keyed tokens."
  tags        = var.tags
}

# Landing: gzip JSON lines written by the processor. Partition projection, so a new
# partition is queryable the moment its first object lands and nothing on the hot
# path has to call Glue.
resource "aws_glue_catalog_table" "landing" {
  name          = local.landing_table
  database_name = aws_glue_catalog_database.curated.name
  catalog_id    = local.account_id
  description   = "At-least-once landing zone. May contain duplicates; query the events table instead."
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    classification                = "json"
    compressionType               = "gzip"
    "projection.enabled"          = "true"
    "projection.vendor.type"      = "enum"
    "projection.vendor.values"    = join(",", sort(tolist(var.vendors)))
    "projection.dt.type"          = "date"
    "projection.dt.format"        = "yyyy-MM-dd"
    "projection.dt.range"         = "${var.projection_start_date},NOW+1DAYS"
    "projection.dt.interval"      = "1"
    "projection.dt.interval.unit" = "DAYS"
    "storage.location.template"   = "${local.landing_location}vendor=$${vendor}/dt=$${dt}/"
  }

  partition_keys {
    name = "vendor"
    type = "string"
  }

  partition_keys {
    name = "dt"
    type = "string"
  }

  storage_descriptor {
    location      = local.landing_location
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"

    ser_de_info {
      serialization_library = "org.openx.data.jsonserde.JsonSerDe"
    }

    dynamic "columns" {
      for_each = [
        ["event_id", "string"],
        ["event_type", "string"],
        ["entity_type", "string"],
        ["entity_id", "string"],
        ["loan_application_id", "string"],
        ["occurred_at", "string"],
        ["version", "bigint"],
        ["status", "string"],
        ["attributes_json", "string"],
        ["ingested_at", "string"],
        ["archive_key", "string"],
        ["correlation_id", "string"],
      ]

      content {
        name = columns.value[0]
        type = columns.value[1]
      }
    }
  }
}

# Compacted: one Parquet file per (vendor, event date), one row per event.
# Partitions are registered by the catalog function after each compaction.
resource "aws_glue_catalog_table" "compacted" {
  name          = local.compacted_table
  database_name = aws_glue_catalog_database.curated.name
  catalog_id    = local.account_id
  description   = "Deduplicated events, one row per (vendor, event_id)."
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    classification = "parquet"
  }

  partition_keys {
    name = "vendor"
    type = "string"
  }

  partition_keys {
    name = "dt"
    type = "string"
  }

  storage_descriptor {
    location      = local.compacted_location
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
    }

    dynamic "columns" {
      for_each = [
        ["event_id", "string"],
        ["event_type", "string"],
        ["entity_type", "string"],
        ["entity_id", "string"],
        ["loan_application_id", "string"],
        ["occurred_at", "timestamp"],
        ["version", "bigint"],
        ["status", "string"],
        ["attributes_json", "string"],
        ["ingested_at", "timestamp"],
        ["archive_key", "string"],
        ["correlation_id", "string"],
      ]

      content {
        name = columns.value[0]
        type = columns.value[1]
      }
    }
  }

  lifecycle {
    # Partitions are data, added by the pipeline. Terraform owns the schema only.
    ignore_changes = [parameters["transient_lastDdlTime"]]
  }
}

resource "aws_athena_workgroup" "curated" {
  name          = "${var.name_prefix}-curated"
  description   = "Queries over curated webhook events"
  force_destroy = !var.deletion_protection

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true
    bytes_scanned_cutoff_per_query     = var.athena_bytes_scanned_cutoff

    result_configuration {
      output_location = "s3://${var.curated_bucket_name}/athena-results/"

      encryption_configuration {
        encryption_option = "SSE_KMS"
        kms_key_arn       = var.data_kms_key_arn
      }
    }
  }

  tags = var.tags
}
