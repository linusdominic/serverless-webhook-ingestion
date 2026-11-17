# The whole platform. Both roots (envs/local, envs/aws) call this module and nothing
# else, so the emulator exercises the same wiring a real account would get.

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  name_prefix = "${var.project}-${var.environment}"

  # Roles are named deterministically so the raw bucket policy can name its readers
  # without a dependency cycle between storage and the modules that create the roles.
  processor_role_arn = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/${local.name_prefix}-processor"
}

module "storage" {
  source = "../storage"

  name_prefix               = local.name_prefix
  raw_reader_principal_arns = concat([local.processor_role_arn], var.raw_operator_principal_arns)
  raw_admin_principal_arns  = var.raw_admin_principal_arns
  raw_retention_days        = var.raw_retention_days
  deletion_protection       = var.deletion_protection
  tags                      = var.tags
}

module "ingest" {
  source = "../ingest"

  name_prefix                   = local.name_prefix
  vendors                       = var.vendors
  raw_bucket_name               = module.storage.raw_bucket_name
  raw_bucket_arn                = module.storage.raw_bucket_arn
  raw_kms_key_arn               = module.storage.raw_kms_key_arn
  data_kms_key_arn              = module.storage.data_kms_key_arn
  package_path                  = var.core_package_path
  lambda_architecture           = var.lambda_architecture
  receiver_timeout_seconds      = var.receiver_timeout_seconds
  receiver_reserved_concurrency = var.receiver_reserved_concurrency
  signature_tolerance_seconds   = var.signature_tolerance_seconds
  visibility_timeout_seconds    = var.visibility_timeout_seconds
  max_receive_count             = var.max_receive_count
  secret_recovery_window_days   = var.secret_recovery_window_days
  log_retention_days            = var.log_retention_days
  tracing_mode                  = var.tracing_mode
  tags                          = var.tags
}

module "processing" {
  source = "../processing"

  name_prefix                 = local.name_prefix
  queue_arn                   = module.ingest.queue_arn
  visibility_timeout_seconds  = var.visibility_timeout_seconds
  raw_bucket_name             = module.storage.raw_bucket_name
  raw_bucket_arn              = module.storage.raw_bucket_arn
  raw_kms_key_arn             = module.storage.raw_kms_key_arn
  curated_bucket_name         = module.storage.curated_bucket_name
  curated_bucket_arn          = module.storage.curated_bucket_arn
  data_kms_key_arn            = module.storage.data_kms_key_arn
  state_table_name            = module.storage.state_table_name
  state_table_arn             = module.storage.state_table_arn
  idempotency_table_name      = module.storage.idempotency_table_name
  idempotency_table_arn       = module.storage.idempotency_table_arn
  idempotency_ttl_days        = var.idempotency_ttl_days
  package_path                = var.core_package_path
  lambda_architecture         = var.lambda_architecture
  processor_timeout_seconds   = var.processor_timeout_seconds
  batch_size                  = var.batch_size
  batching_window_seconds     = var.batching_window_seconds
  max_concurrency             = var.processor_max_concurrency
  secret_recovery_window_days = var.secret_recovery_window_days
  log_retention_days          = var.log_retention_days
  tracing_mode                = var.tracing_mode
  tags                        = var.tags
}

module "analytics" {
  source = "../analytics"

  name_prefix                   = local.name_prefix
  vendors                       = var.vendors
  curated_bucket_name           = module.storage.curated_bucket_name
  curated_bucket_arn            = module.storage.curated_bucket_arn
  data_kms_key_arn              = module.storage.data_kms_key_arn
  idempotency_table_name        = module.storage.idempotency_table_name
  idempotency_table_arn         = module.storage.idempotency_table_arn
  idempotency_ingest_index_name = module.storage.idempotency_ingest_index_name
  alerts_topic_arn              = module.observability.alerts_topic_arn
  core_package_path             = var.core_package_path
  compactor_package_path        = var.compactor_package_path
  lambda_architecture           = var.lambda_architecture
  schedule_expression           = var.schedule_expression
  schedule_enabled              = var.schedule_enabled
  state_machine_timeout_seconds = var.state_machine_timeout_seconds
  reconcile_margin_seconds      = var.reconcile_margin_seconds
  projection_start_date         = var.projection_start_date
  deletion_protection           = var.deletion_protection
  log_retention_days            = var.log_retention_days
  tracing_mode                  = var.tracing_mode
  tags                          = var.tags
}

module "observability" {
  source = "../observability"

  name_prefix                 = local.name_prefix
  data_kms_key_arn            = module.storage.data_kms_key_arn
  vendors                     = var.vendors
  api_id                      = module.ingest.api_id
  queue_name                  = module.ingest.queue_name
  dlq_name                    = module.ingest.dlq_name
  receiver_function_name      = module.ingest.receiver_function_name
  processor_function_name     = module.processing.processor_function_name
  state_machine_arn           = module.analytics.state_machine_arn
  queue_age_threshold_seconds = var.queue_age_threshold_seconds
  alert_email_addresses       = var.alert_email_addresses
  tags                        = var.tags
}
