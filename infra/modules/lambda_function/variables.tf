variable "name" {
  description = "Function name. Also names the role and the log group."
  type        = string

  validation {
    condition     = can(regex("^[a-zA-Z0-9-_]{1,64}$", var.name))
    error_message = "name must be 1-64 characters of letters, digits, hyphens or underscores."
  }
}

variable "description" {
  description = "What the function does, shown in the console."
  type        = string
}

variable "handler" {
  description = "Python handler, module.function."
  type        = string
}

variable "package_path" {
  description = "Path to the deployment zip built by scripts/build_lambdas.py."
  type        = string
}

variable "runtime" {
  description = "Lambda runtime identifier."
  type        = string
  default     = "python3.12"
}

variable "architecture" {
  description = "CPU architecture the package was built for."
  type        = string

  validation {
    condition     = contains(["arm64", "x86_64"], var.architecture)
    error_message = "architecture must be arm64 or x86_64."
  }
}

variable "memory_mb" {
  description = "Memory in MB. Also scales CPU."
  type        = number
  default     = 256

  validation {
    condition     = var.memory_mb >= 128 && var.memory_mb <= 10240
    error_message = "memory_mb must be between 128 and 10240."
  }
}

variable "timeout_seconds" {
  description = "Function timeout."
  type        = number
  default     = 10

  validation {
    condition     = var.timeout_seconds >= 1 && var.timeout_seconds <= 900
    error_message = "timeout_seconds must be between 1 and 900."
  }
}

variable "environment" {
  description = "Environment variables. Names and identifiers only, never secret values."
  type        = map(string)
  default     = {}
}

variable "policy_json" {
  description = "IAM policy document granting exactly what this function's code calls."
  type        = string
}

variable "kms_key_arn" {
  description = "KMS key for environment variables and the log group."
  type        = string
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention."
  type        = number
  default     = 365
}

variable "reserved_concurrency" {
  description = "Reserved concurrent executions. null leaves the function on the account's unreserved pool."
  type        = number
  default     = null
}

variable "tracing_mode" {
  description = "X-Ray tracing mode."
  type        = string
  default     = "Active"

  validation {
    condition     = contains(["Active", "PassThrough"], var.tracing_mode)
    error_message = "tracing_mode must be Active or PassThrough."
  }
}

variable "tags" {
  description = "Tags added to every resource in this module."
  type        = map(string)
  default     = {}
}
