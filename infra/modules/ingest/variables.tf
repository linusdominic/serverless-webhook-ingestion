variable "name_prefix" {
  description = "Prefix for resource names, <project>-<environment>."
  type        = string
}

variable "vendors" {
  description = "Vendor names. Each gets a signing secret; each must have an entry in src/webhook_platform/vendors.py."
  type        = set(string)

  validation {
    condition     = length(var.vendors) > 0 && alltrue([for v in var.vendors : can(regex("^[a-z][a-z0-9]{2,31}$", v))])
    error_message = "vendors must be non-empty; names are 3-32 lower-case letters and digits, starting with a letter."
  }
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

variable "data_kms_key_arn" {
  description = "KMS key for queues, secrets and logs."
  type        = string
}

variable "package_path" {
  description = "Path to core.zip."
  type        = string
}

variable "lambda_architecture" {
  description = "CPU architecture of the Lambda package."
  type        = string
}

variable "receiver_memory_mb" {
  description = "Receiver memory."
  type        = number
  default     = 256
}

variable "receiver_timeout_seconds" {
  description = "Receiver timeout. Keep it well under the shortest vendor delivery timeout."
  type        = number
  default     = 10
}

variable "receiver_reserved_concurrency" {
  description = "Reserved concurrency for the receiver. null uses the unreserved pool."
  type        = number
  default     = null
}

variable "signature_tolerance_seconds" {
  description = "Maximum difference between a signed timestamp and the receiver's clock."
  type        = number
  default     = 300

  validation {
    condition     = var.signature_tolerance_seconds >= 30 && var.signature_tolerance_seconds <= 900
    error_message = "signature_tolerance_seconds must be between 30 and 900. Shorter rejects honest clock skew; longer widens the replay window."
  }
}

variable "max_body_bytes" {
  description = "Largest accepted request body."
  type        = number
  default     = 1048576
}

variable "visibility_timeout_seconds" {
  description = "Queue visibility timeout. AWS guidance for a Lambda consumer is at least six times the function timeout."
  type        = number
}

variable "max_receive_count" {
  description = "Receives before a message moves to the dead-letter queue."
  type        = number
  default     = 5

  validation {
    condition     = var.max_receive_count >= 2 && var.max_receive_count <= 20
    error_message = "max_receive_count must be between 2 and 20."
  }
}

variable "queue_retention_seconds" {
  description = "Message retention on the ingest queue."
  type        = number
  default     = 345600
}

variable "throttle_rate_per_second" {
  description = "Steady-state request rate limit on the API stage."
  type        = number
  default     = 200
}

variable "throttle_burst" {
  description = "Burst limit on the API stage."
  type        = number
  default     = 400
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
  description = "X-Ray tracing mode for the receiver."
  type        = string
  default     = "Active"
}

variable "tags" {
  description = "Tags added to every resource in this module."
  type        = map(string)
  default     = {}
}
