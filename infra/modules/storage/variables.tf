variable "name_prefix" {
  description = "Prefix for resource names, <project>-<environment>."
  type        = string
}

variable "raw_reader_principal_arns" {
  description = "IAM principals allowed to read raw payloads. Everyone else is denied by the bucket policy, whatever their identity policy says."
  type        = list(string)

  validation {
    condition     = length(var.raw_reader_principal_arns) > 0
    error_message = "At least one principal must be able to read the raw archive, or it cannot be processed or replayed."
  }
}

variable "raw_admin_principal_arns" {
  description = "IAM principals allowed to delete raw objects or change the raw bucket's policy (break-glass and the deployment role)."
  type        = list(string)
}

variable "raw_retention_days" {
  description = "Days before raw objects are deleted. Set from the record-retention policy that applies to the lender."
  type        = number
  default     = 2557

  validation {
    condition     = var.raw_retention_days >= 365
    error_message = "raw_retention_days must be at least 365. The archive is the only copy of what vendors sent."
  }
}

variable "landing_retention_days" {
  description = "Days before landing objects are deleted. The compactor lists the landing zone on every run, so this bounds both how long a stalled compactor can go unnoticed and how much each run has to list."
  type        = number
  default     = 14

  validation {
    condition     = var.landing_retention_days >= 7
    error_message = "Keep landing objects at least 7 days so a stalled compactor can be noticed and fixed before data expires."
  }
}

variable "access_log_retention_days" {
  description = "Days before S3 server access logs are deleted."
  type        = number
  default     = 90
}

variable "kms_deletion_window_days" {
  description = "Waiting period before a scheduled KMS key deletion takes effect."
  type        = number
  default     = 30

  validation {
    condition     = var.kms_deletion_window_days >= 7 && var.kms_deletion_window_days <= 30
    error_message = "kms_deletion_window_days must be between 7 and 30."
  }
}

variable "deletion_protection" {
  description = "Protect tables from deletion and refuse to destroy non-empty buckets. true everywhere except throwaway environments."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Tags added to every resource in this module."
  type        = map(string)
  default     = {}
}
