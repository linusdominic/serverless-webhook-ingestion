variable "name_prefix" {
  description = "Prefix for resource names, <project>-<environment>."
  type        = string
}

variable "data_kms_key_arn" {
  description = "KMS key for the alerts topic."
  type        = string
}

variable "vendors" {
  description = "Vendor names; one signature-rejection alarm each."
  type        = set(string)
}

variable "api_id" {
  description = "HTTP API id."
  type        = string
}

variable "queue_name" {
  description = "Ingest queue name."
  type        = string
}

variable "dlq_name" {
  description = "Dead-letter queue name."
  type        = string
}

variable "receiver_function_name" {
  description = "Receiver function name."
  type        = string
}

variable "processor_function_name" {
  description = "Processor function name."
  type        = string
}

variable "state_machine_arn" {
  description = "Curation state machine ARN."
  type        = string
}

variable "metrics_namespace" {
  description = "Namespace of the application's embedded-format metrics."
  type        = string
  default     = "WebhookIngestion"
}

variable "queue_age_threshold_seconds" {
  description = "Alarm when the oldest queued message is older than this."
  type        = number
  default     = 300
}

variable "error_rate_threshold_percent" {
  description = "Alarm when a function's error rate over five minutes exceeds this."
  type        = number
  default     = 1
}

variable "signature_rejection_threshold" {
  description = "Alarm when one vendor has this many bad-signature rejections in five minutes."
  type        = number
  default     = 20
}

variable "alert_email_addresses" {
  description = "Email subscriptions on the alerts topic. Each address must confirm."
  type        = list(string)
  default     = []
}

variable "tags" {
  description = "Tags added to every resource in this module."
  type        = map(string)
  default     = {}
}
