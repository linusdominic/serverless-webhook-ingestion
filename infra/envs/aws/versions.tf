# NEVER APPLIED. This root has been through `terraform fmt`, `validate`, tflint and
# checkov, and it calls exactly the module the emulator root applies. It has not been
# planned or applied against an AWS account. docs/deploying-to-aws.md lists what a
# first real deploy would need and what is most likely to need adjusting.

terraform {
  required_version = "~> 1.17"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.73"
    }
  }

  # Remote state. Left commented because nothing has been deployed: there is no state
  # bucket to point at. For a real deploy, create the bucket out of band (versioned,
  # SSE-KMS, public access blocked), then uncomment and `terraform init`.
  #
  # use_lockfile stores the lock next to the state object using S3 conditional writes,
  # so no DynamoDB lock table is needed.
  #
  # backend "s3" {
  #   bucket       = "<org>-terraform-state-<account-id>"
  #   key          = "webhook-ingestion/<environment>/terraform.tfstate"
  #   region       = "us-east-1"
  #   encrypt      = true
  #   kms_key_id   = "alias/terraform-state"
  #   use_lockfile = true
  # }
}
