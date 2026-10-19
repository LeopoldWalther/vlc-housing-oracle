variable "lake_bucket" {
  description = "Existing private bucket that holds landing/v1; Silver's warehouse is its silver/ prefix. Set it in terraform.tfvars, which git ignores."
  type        = string

  validation {
    condition     = length(var.lake_bucket) > 0
    error_message = "lake_bucket must name the existing private bucket."
  }
}

variable "region" {
  description = "AWS region of the lake bucket; the catalog lives beside it."
  type        = string
  default     = "eu-central-1"
}

variable "silver_image" {
  description = "The Silver image to run, pinned to one commit: ghcr.io/<owner>/vlc-housing-oracle-silver:<sha>. A new image runs only after it is set here and applied."
  type        = string

  validation {
    condition     = can(regex("^ghcr\\.io/[a-z0-9._-]+/vlc-housing-oracle-silver(:[0-9a-f]{7,40}|@sha256:[0-9a-f]{64})$", var.silver_image))
    error_message = "silver_image must name the Silver image by commit SHA or digest, never latest."
  }
}
