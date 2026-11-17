provider "aws" {
  region = var.region

  # A plan or apply with credentials for any other account stops here.
  allowed_account_ids = [var.aws_account_id]

  default_tags {
    tags = {
      Project     = "webhook-ingestion"
      Environment = var.environment
      ManagedBy   = "terraform"
      CostCenter  = var.cost_center
      Owner       = var.owner
      Repository  = "serverless-webhook-ingestion"
    }
  }
}
