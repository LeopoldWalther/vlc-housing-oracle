# What starts Silver: a landing commit's own object event, and a daily catch-up. Both only send a
# message to the queue; neither starts a task directly.

resource "aws_cloudwatch_event_rule" "landing_commit" {
  name        = "${local.name}-landing-commit"
  description = "A landing v3 commit was written: queue a Silver run."
  state       = var.trigger_enabled ? "ENABLED" : "DISABLED"

  event_pattern = jsonencode({
    source      = ["aws.s3"]
    detail-type = ["Object Created"]
    detail = {
      bucket = { name = [var.lake_bucket] }
      object = { key = [{ prefix = "landing/v3/_commits/" }] }
    }
  })
}

resource "aws_cloudwatch_event_target" "landing_commit" {
  rule = aws_cloudwatch_event_rule.landing_commit.name
  arn  = aws_sqs_queue.silver.arn

  sqs_target {
    message_group_id = "silver"
  }
}

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "catch_up" {
  name               = "${local.name}-catch-up"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

data "aws_iam_policy_document" "catch_up" {
  statement {
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.silver.arn]
  }
}

resource "aws_iam_role_policy" "catch_up" {
  name   = "queue-a-silver-run"
  role   = aws_iam_role.catch_up.id
  policy = data.aws_iam_policy_document.catch_up.json
}

# After the acquisition repository's own daily landing catch-up at 05:00 UTC. Content-based
# deduplication only spans five minutes, so the same body every day is fine.
resource "aws_scheduler_schedule" "catch_up" {
  name                         = "${local.name}-catch-up"
  schedule_expression          = "cron(30 5 * * ? *)"
  schedule_expression_timezone = "UTC"
  state                        = var.catch_up_enabled ? "ENABLED" : "DISABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_sqs_queue.silver.arn
    role_arn = aws_iam_role.catch_up.arn
    input    = jsonencode({ start = "daily catch-up" })

    sqs_parameters {
      message_group_id = "silver"
    }
  }
}
