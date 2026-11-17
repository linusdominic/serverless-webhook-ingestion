# Curate and query: Glue catalog and Athena workgroup (catalog.tf), the four pipeline
# functions (functions.tf), the state machine and its schedule (pipeline.tf).

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
  region     = data.aws_region.current.region

  glue_arn_prefix = "arn:${local.partition}:glue:${local.region}:${local.account_id}"
  glue_read_resources = [
    "${local.glue_arn_prefix}:catalog",
    "${local.glue_arn_prefix}:database/${aws_glue_catalog_database.curated.name}",
    "${local.glue_arn_prefix}:table/${aws_glue_catalog_database.curated.name}/${aws_glue_catalog_table.landing.name}",
    "${local.glue_arn_prefix}:table/${aws_glue_catalog_database.curated.name}/${aws_glue_catalog_table.compacted.name}",
  ]

  common_environment = {
    SERVICE_NAME      = var.name_prefix
    METRICS_NAMESPACE = "WebhookIngestion"
  }
}
