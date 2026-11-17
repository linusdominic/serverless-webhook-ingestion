variable "emulator_endpoint" {
  description = "Endpoint of the AWS emulator. Must be loopback or the compose service name; anything else is refused."
  type        = string
  default     = "http://floci:4566"

  validation {
    condition     = can(regex("^http://(localhost|127\\.0\\.0\\.1|floci|host\\.docker\\.internal):[0-9]{2,5}$", var.emulator_endpoint))
    error_message = "emulator_endpoint must be http://localhost:<port>, http://127.0.0.1:<port>, http://floci:<port> or http://host.docker.internal:<port>. This root never talks to real AWS."
  }
}

variable "region" {
  description = "Region name the emulator reports."
  type        = string
  default     = "us-east-1"
}

variable "lambda_architecture" {
  description = "Architecture compactor.zip was built for. The emulator runs functions on the Docker host's architecture."
  type        = string
}

variable "build_dir" {
  description = "Directory holding core.zip and compactor.zip."
  type        = string
  default     = "../../../build"
}

variable "schedule_expression" {
  description = "Curation schedule. Short, so the demo can wait for a scheduled run."
  type        = string
  default     = "rate(1 minute)"
}
