terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0, < 6.0"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      project    = "vlc-housing-oracle"
      layer      = "silver"
      managed_by = "terraform"
    }
  }
}
