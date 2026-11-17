variable "name_prefix" {
  description = "Prefix for resource names, <project>-<environment>."
  type        = string
}

variable "vendors" {
  description = "Vendor names, used as the enum for partition projection."
  type        = set(string)
}

variable "curated_bucket_name" {
  description = "Curated bucket."
  type        = string
}

variable "curated_bucket_arn" {
  description = "Curated bucket ARN."
  type        = string
}

variable "data_kms_key_arn" {
  description = "KMS key for derived data."
  type        = string
}

variable "idempotency_table_name" {
  description = "Idempotency ledger table, read by reconciliation."
  type        = string
}

variable "idempotency_table_arn" {
  description = "Idempotency ledger table ARN."
  type        = string
}

variable "idempotency_ingest_index_name" {
  description = "Name of the ledger index keyed by vendor and ingest date."
  type        = string
}

variable "alerts_topic_arn" {
  description = "SNS topic that receives pipeline failures."
  type        = string
}

variable "core_package_path" {
  description = "Path to core.zip."
  type        = string
}

variable "compactor_package_path" {
  description = "Path to compactor.zip (core plus pyarrow for the target architecture)."
  type        = string
}

variable "lambda_architecture" {
  description = "CPU architecture of the Lambda packages."
  type        = string
}

variable "schedule_expression" {
  description = "EventBridge Scheduler expression for the curation pipeline."
  type        = string
  default     = "rate(15 minutes)"

  validation {
    condition     = can(regex("^(rate|cron|at)\\(.+\\)$", var.schedule_expression))
    error_message = "schedule_expression must be a rate(), cron() or at() expression."
  }
}

variable "schedule_enabled" {
  description = "Whether the schedule fires."
  type        = bool
  default     = true
}

variable "state_machine_timeout_seconds" {
  description = "Hard limit on one pipeline execution. Keep it below the schedule period so executions cannot overlap."
  type        = number
  default     = 840
}

variable "compactor_memory_mb" {
  description = "Compactor memory. A partition is merged in memory, so this bounds partition size."
  type        = number
  default     = 1024
}

variable "compactor_timeout_seconds" {
  description = "Compactor timeout."
  type        = number
  default     = 300
}

variable "reconcile_margin_seconds" {
  description = "Events ingested within this many seconds before compaction started are left out of reconciliation, to allow for work in flight."
  type        = number
  default     = 900
}

variable "reconcile_lookback_hours" {
  description = "How far back each reconciliation looks. Longer catches slower faults and reads more."
  type        = number
  default     = 48

  validation {
    condition     = var.reconcile_lookback_hours >= 1 && var.reconcile_lookback_hours <= 720
    error_message = "reconcile_lookback_hours must be between 1 and 720."
  }
}

variable "projection_start_date" {
  description = "Earliest event date the landing table's partition projection covers, YYYY-MM-DD."
  type        = string

  validation {
    condition     = can(regex("^\\d{4}-\\d{2}-\\d{2}$", var.projection_start_date))
    error_message = "projection_start_date must be YYYY-MM-DD."
  }
}

variable "athena_bytes_scanned_cutoff" {
  description = "Per-query scan limit in bytes for the workgroup. A guard against an accidental full scan."
  type        = number
  default     = 10737418240
}

variable "deletion_protection" {
  description = "Refuse to delete a workgroup that still has query history."
  type        = bool
  default     = true
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention."
  type        = number
  default     = 365
}

variable "tracing_mode" {
  description = "X-Ray tracing mode for the functions."
  type        = string
  default     = "Active"
}

variable "tags" {
  description = "Tags added to every resource in this module."
  type        = map(string)
  default     = {}
}
