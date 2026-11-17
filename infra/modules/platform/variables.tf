variable "project" {
  description = "Project name, first part of every resource name."
  type        = string
  default     = "webhook-ingestion"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,24}$", var.project))
    error_message = "project must be 3-25 characters: lower-case letters, digits and hyphens, starting with a letter."
  }
}

variable "environment" {
  description = "Environment name, second part of every resource name."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{1,9}$", var.environment))
    error_message = "environment must be 2-10 lower-case letters and digits, starting with a letter."
  }
}

variable "vendors" {
  description = "Vendors accepted by the receiver."
  type        = set(string)
  default     = ["meridianpay", "callwright", "veritybureau"]
}

variable "core_package_path" {
  description = "Path to build/core.zip."
  type        = string
}

variable "compactor_package_path" {
  description = "Path to build/compactor.zip."
  type        = string
}

variable "lambda_architecture" {
  description = "CPU architecture the packages were built for. Only compactor.zip is architecture-specific."
  type        = string

  validation {
    condition     = contains(["arm64", "x86_64"], var.lambda_architecture)
    error_message = "lambda_architecture must be arm64 or x86_64."
  }
}

variable "raw_operator_principal_arns" {
  description = "IAM principals, besides the processor, allowed to read the raw archive (the people who run replays)."
  type        = list(string)
  default     = []
}

variable "raw_admin_principal_arns" {
  description = "IAM principals allowed to delete from the raw archive or change its policy."
  type        = list(string)
}

variable "raw_retention_days" {
  description = "Days raw payloads are kept."
  type        = number
  default     = 2557
}

variable "deletion_protection" {
  description = "Protect stateful resources from deletion."
  type        = bool
  default     = true
}

variable "secret_recovery_window_days" {
  description = "Recovery window for deleted secrets."
  type        = number
  default     = 30
}

variable "signature_tolerance_seconds" {
  description = "Accepted clock difference on signed timestamps."
  type        = number
  default     = 300
}

variable "receiver_timeout_seconds" {
  description = "Receiver timeout."
  type        = number
  default     = 10
}

variable "receiver_reserved_concurrency" {
  description = "Reserved concurrency for the receiver, or null."
  type        = number
  default     = null
}

variable "processor_timeout_seconds" {
  description = "Processor timeout."
  type        = number
  default     = 30
}

variable "visibility_timeout_seconds" {
  description = "Ingest queue visibility timeout."
  type        = number
  default     = 180
}

variable "max_receive_count" {
  description = "Receives before a message goes to the DLQ."
  type        = number
  default     = 5
}

variable "batch_size" {
  description = "Processor batch size."
  type        = number
  default     = 50
}

variable "batching_window_seconds" {
  description = "Processor batching window."
  type        = number
  default     = 5
}

variable "processor_max_concurrency" {
  description = "Maximum concurrent processor invocations."
  type        = number
  default     = 10
}

variable "idempotency_ttl_days" {
  description = "Days idempotency records are kept."
  type        = number
  default     = 30
}

variable "schedule_expression" {
  description = "Schedule for the curation pipeline."
  type        = string
  default     = "rate(15 minutes)"
}

variable "schedule_enabled" {
  description = "Whether the curation schedule fires."
  type        = bool
  default     = true
}

variable "state_machine_timeout_seconds" {
  description = "Limit on one curation execution."
  type        = number
  default     = 840
}

variable "reconcile_margin_seconds" {
  description = "Recent window excluded from reconciliation."
  type        = number
  default     = 900
}

variable "projection_start_date" {
  description = "Earliest event date covered by the landing table's partition projection, YYYY-MM-DD."
  type        = string
}

variable "queue_age_threshold_seconds" {
  description = "Queue age alarm threshold."
  type        = number
  default     = 300
}

variable "alert_email_addresses" {
  description = "Email subscribers for alerts."
  type        = list(string)
  default     = []
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention."
  type        = number
  default     = 365
}

variable "tracing_mode" {
  description = "X-Ray tracing mode."
  type        = string
  default     = "Active"
}

variable "tags" {
  description = "Tags added to every resource, in addition to the provider's default tags."
  type        = map(string)
  default     = {}
}
