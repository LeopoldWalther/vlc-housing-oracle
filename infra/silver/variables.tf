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

variable "census_sections" {
  description = "The census-section vintage the Silver task reads for listing_location (ADR 0004): s3://<lake bucket>/reference/v1/census_sections/vintage=<date>. Empty: the task gets no --census-sections and writes no listing_location."
  type        = string
  default     = ""

  validation {
    condition     = var.census_sections == "" || can(regex("^s3://${var.lake_bucket}/reference/v1/census_sections/vintage=[0-9]{4}-[0-9]{2}-[0-9]{2}$", var.census_sections))
    error_message = "census_sections must be empty or one vintage directory under reference/v1/census_sections/ of the lake bucket, without a trailing slash."
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

variable "label_model_id" {
  description = "The Claude API model of the text labeller's label version (ADR 0005), for example claude-haiku-5-5. Set it in terraform.tfvars, which git ignores."
  type        = string

  validation {
    condition     = length(var.label_model_id) > 0
    error_message = "label_model_id must name the model of the current label version."
  }
}

variable "label_api_key_parameter" {
  description = "Name of the Parameter Store SecureString that holds the Claude API key, created by hand. Terraform reads no value of it. Set it in terraform.tfvars, which git ignores."
  type        = string

  validation {
    condition     = can(regex("^/[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*$", var.label_api_key_parameter))
    error_message = "label_api_key_parameter must be a parameter name starting with a slash."
  }
}

variable "label_max_texts" {
  description = "Most descriptions the labeller submits in one batch: the per-run cap on cost."
  type        = number
  default     = 2000

  validation {
    condition     = var.label_max_texts >= 1 && var.label_max_texts <= 100000
    error_message = "label_max_texts must be between 1 and the batch limit of 100000."
  }
}

variable "labeller_enabled" {
  description = "Whether a succeeded Silver task starts the text labeller. Off until the first batch of one text is checked."
  type        = bool
  default     = false
}
