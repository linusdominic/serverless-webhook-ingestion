variable "name_prefix" {
  description = "Prefix for resource names, <project>-<environment>."
  type        = string
}

variable "queue_arn" {
  description = "Ingest queue ARN."
  type        = string
}

variable "visibility_timeout_seconds" {
  description = "Visibility timeout of the ingest queue. Used only to check it against the function timeout."
  type        = number
}

variable "raw_bucket_name" {
  description = "Raw archive bucket."
  type        = string
}

variable "raw_bucket_arn" {
  description = "Raw archive bucket ARN."
  type        = string
}

variable "raw_kms_key_arn" {
  description = "KMS key for the raw archive."
  type        = string
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

variable "state_table_name" {
  description = "Entity state table."
  type        = string
}

variable "state_table_arn" {
  description = "Entity state table ARN."
  type        = string
}

variable "idempotency_table_name" {
  description = "Idempotency ledger table."
  type        = string
}

variable "idempotency_table_arn" {
  description = "Idempotency ledger table ARN."
  type        = string
}

variable "idempotency_ttl_days" {
  description = "Days an idempotency record is kept. Must exceed the longest vendor retry schedule."
  type        = number
  default     = 30

  validation {
    condition     = var.idempotency_ttl_days >= 3
    error_message = "idempotency_ttl_days must be at least 3; vendors retry for days."
  }
}

variable "package_path" {
  description = "Path to core.zip."
  type        = string
}

variable "lambda_architecture" {
  description = "CPU architecture of the Lambda package."
  type        = string
}

variable "processor_memory_mb" {
  description = "Processor memory."
  type        = number
  default     = 512
}

variable "processor_timeout_seconds" {
  description = "Processor timeout."
  type        = number
  default     = 30
}

variable "batch_size" {
  description = "Maximum messages per processor invocation."
  type        = number
  default     = 50

  validation {
    condition     = var.batch_size >= 1 && var.batch_size <= 10000
    error_message = "batch_size must be between 1 and 10000."
  }
}

variable "batching_window_seconds" {
  description = "How long Lambda gathers messages before invoking. Required to be at least 1 when batch_size exceeds 10."
  type        = number
  default     = 5
}

variable "max_concurrency" {
  description = "Upper bound on concurrent processor invocations from the queue. Protects DynamoDB and caps cost during a backlog."
  type        = number
  default     = 10

  validation {
    condition     = var.max_concurrency >= 2 && var.max_concurrency <= 1000
    error_message = "max_concurrency must be between 2 and 1000."
  }
}

variable "secret_recovery_window_days" {
  description = "Days a deleted secret can be restored. 0 deletes immediately; only for throwaway environments."
  type        = number
  default     = 30
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention."
  type        = number
  default     = 365
}

variable "tracing_mode" {
  description = "X-Ray tracing mode for the processor."
  type        = string
  default     = "Active"
}

variable "tags" {
  description = "Tags added to every resource in this module."
  type        = map(string)
  default     = {}
}
