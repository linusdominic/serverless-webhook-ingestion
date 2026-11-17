variable "aws_account_id" {
  description = "The one account this root may be applied to."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id)) && var.aws_account_id != "000000000000"
    error_message = "aws_account_id must be a real 12-digit account id."
  }
}

variable "region" {
  description = "AWS region."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name."
  type        = string

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be dev, staging or prod."
  }
}

variable "owner" {
  description = "Team that owns the stack, for the Owner cost-allocation tag."
  type        = string
  default     = "data-platform"
}

variable "cost_center" {
  description = "Value of the CostCenter cost-allocation tag."
  type        = string
  default     = "data-platform"
}

variable "lambda_architecture" {
  description = "Architecture compactor.zip was built for. arm64 (Graviton) is cheaper per GB-second; build with `make build LAMBDA_ARCH=arm64`."
  type        = string
  default     = "arm64"
}

variable "build_dir" {
  description = "Directory holding core.zip and compactor.zip."
  type        = string
  default     = "../../../build"
}

variable "alert_email_addresses" {
  description = "Who is told when an alarm fires or the curation pipeline fails."
  type        = list(string)

  validation {
    condition     = length(var.alert_email_addresses) > 0
    error_message = "At least one alert recipient is required. An alarm nobody receives is not an alarm."
  }
}

variable "raw_admin_principal_arns" {
  description = "IAM role ARNs allowed to delete from the raw archive or change its policy: the deployment role and a break-glass role."
  type        = list(string)

  validation {
    condition     = length(var.raw_admin_principal_arns) > 0 && alltrue([for arn in var.raw_admin_principal_arns : can(regex("^arn:aws[a-z-]*:iam::[0-9]{12}:role/.+$", arn))])
    error_message = "raw_admin_principal_arns must be a non-empty list of IAM role ARNs. Without the deployment role in it, Terraform locks itself out of the bucket policy."
  }
}

variable "raw_operator_principal_arns" {
  description = "IAM role ARNs allowed to read raw payloads besides the processor: the people who run replays."
  type        = list(string)
  default     = []
}

variable "raw_retention_days" {
  description = "Days raw payloads are kept. Set from the lender's record-retention policy."
  type        = number
  default     = 2557
}

variable "projection_start_date" {
  description = "Earliest event date queryable through the landing table, YYYY-MM-DD. Use the go-live date."
  type        = string
}

variable "receiver_reserved_concurrency" {
  description = "Reserved concurrency for the receiver. Leave null on a new account: reserving needs an account concurrency limit above the default floor."
  type        = number
  default     = null
}
