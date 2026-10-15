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
