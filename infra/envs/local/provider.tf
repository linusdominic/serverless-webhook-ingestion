# This root can only reach an emulator. Four things make that so, any one of which
# is sufficient:
#
#   1. var.emulator_endpoint must be a loopback or compose-internal address
#      (validation in variables.tf), and every service the stack uses is pinned to it.
#   2. Credentials are the literal strings "test"/"test", set here, so nothing is read
#      from the environment, ~/.aws or instance metadata.
#   3. Credential validation, account lookup and the metadata API are all skipped, so
#      the provider makes no call to a real AWS endpoint at start-up. (Skipping the
#      account lookup also keeps the provider off S3 Control's account-prefixed
#      hostnames, which an emulator on a single hostname cannot serve.)
#   4. The Terraform container (docker-compose.yml) has no AWS files mounted and sets
#      AWS_ENDPOINT_URL as a catch-all for any service not listed below.

provider "aws" {
  region     = var.region
  access_key = "test"
  secret_key = "test"

  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_region_validation      = true
  skip_requesting_account_id  = true
  s3_use_path_style           = true

  endpoints {
    apigatewayv2   = var.emulator_endpoint
    athena         = var.emulator_endpoint
    cloudwatch     = var.emulator_endpoint
    dynamodb       = var.emulator_endpoint
    glue           = var.emulator_endpoint
    iam            = var.emulator_endpoint
    kms            = var.emulator_endpoint
    lambda         = var.emulator_endpoint
    logs           = var.emulator_endpoint
    s3             = var.emulator_endpoint
    scheduler      = var.emulator_endpoint
    secretsmanager = var.emulator_endpoint
    sfn            = var.emulator_endpoint
    sns            = var.emulator_endpoint
    sqs            = var.emulator_endpoint
    sts            = var.emulator_endpoint
  }

  default_tags {
    tags = {
      Project     = "webhook-ingestion"
      Environment = "local"
      ManagedBy   = "terraform"
      CostCenter  = "data-platform"
    }
  }
}
