terraform {
  # Shared bucket/endpoint settings come from backend.hcl; ./cluster derives
  # the state key from cluster_name in the selected tfvars file.
  # COS HMAC credentials must come from the environment, never this file.
  backend "s3" {}
}
