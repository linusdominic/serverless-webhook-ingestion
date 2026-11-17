# tflint configuration, shared by every module and root (scripts/tflint.sh).

plugin "terraform" {
  enabled = true
  preset  = "all"
}

plugin "aws" {
  enabled = true
  version = "0.50.0"
  source  = "github.com/terraform-linters/tflint-ruleset-aws"
}

# Modules here are called by relative path from two roots in the same repository;
# pinning a version constraint in each one would only repeat the roots' constraint.
rule "terraform_required_version" {
  enabled = true
}
