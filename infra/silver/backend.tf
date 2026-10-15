terraform {
  required_version = ">= 1.10, < 2.0"

  # Partial configuration: the state bucket is account-specific and never committed. Copy
  # backend.hcl.example to backend.tfvars (gitignored) and run
  # `terraform init -backend-config=backend.tfvars`.
  backend "s3" {
    key          = "vlc-housing-oracle/silver/terraform.tfstate"
    region       = "eu-central-1"
    encrypt      = true
    use_lockfile = true
  }
}
