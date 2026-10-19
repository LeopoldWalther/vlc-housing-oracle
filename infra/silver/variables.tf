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

variable "alert_email" {
  description = "Address that receives Silver failure mail. Set it in terraform.tfvars, which git ignores."
  type        = string
}

variable "trigger_enabled" {
  description = "Whether a landing commit queues a Silver run. Off until the first backfill is done."
  type        = bool
  default     = false
}

variable "catch_up_enabled" {
  description = "Whether the daily catch-up queues a Silver run."
  type        = bool
  default     = false
}

variable "consumer_enabled" {
  description = "Whether the queue consumer starts Silver tasks. Turn it off before a manual backfill, on after."
  type        = bool
  default     = true
}

variable "consumer_reserved_concurrency" {
  description = "Reserved concurrency of the queue consumer; null where the account's concurrency limit is 10."
  type        = number
  default     = 1
  nullable    = true
}
