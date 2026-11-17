terraform {
  required_version = "~> 1.17"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.73"
    }
  }

  # Local state on purpose: this root only ever talks to a throwaway emulator.
  backend "local" {
    path = "terraform.tfstate"
  }
}
