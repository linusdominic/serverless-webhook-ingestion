# Real-AWS root. NEVER APPLIED; see versions.tf.
#
# Everything not set here uses the platform module's defaults, which are the
# production-shaped values: 30 s processor timeout, 180 s visibility timeout,
# five receives before the DLQ, a 15-minute curation schedule, 365-day log
# retention, deletion protection on, X-Ray tracing on.

module "platform" {
  source = "../../modules/platform"

  environment            = var.environment
  core_package_path      = "${var.build_dir}/core.zip"
  compactor_package_path = "${var.build_dir}/compactor.zip"
  lambda_architecture    = var.lambda_architecture

  raw_admin_principal_arns    = var.raw_admin_principal_arns
  raw_operator_principal_arns = var.raw_operator_principal_arns
  raw_retention_days          = var.raw_retention_days

  receiver_reserved_concurrency = var.receiver_reserved_concurrency
  projection_start_date         = var.projection_start_date
  alert_email_addresses         = var.alert_email_addresses
}
