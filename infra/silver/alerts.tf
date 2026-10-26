# Failure mail: a Silver task that stopped badly, a dead-lettered start, or runs left pending.

resource "aws_sns_topic" "alerts" {
  name = "${local.name}-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

data "aws_iam_policy_document" "alerts" {
  statement {
    sid       = "StoppedTaskRule"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_cloudwatch_event_rule.task_failed.arn]
    }
  }

  statement {
    sid       = "Alarms"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]
    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_sns_topic_policy" "alerts" {
  arn    = aws_sns_topic.alerts.arn
  policy = data.aws_iam_policy_document.alerts.json
}

# The same pattern the acquisition repository uses for its own tasks: a non-zero exit, or a task
# that stopped for any reason other than its container exiting (it never started).
resource "aws_cloudwatch_event_rule" "task_failed" {
  name        = "${local.name}-task-failed"
  description = "A Silver task stopped without succeeding."

  event_pattern = jsonencode({
    source      = ["aws.ecs"]
    detail-type = ["ECS Task State Change"]
    detail = {
      clusterArn = [aws_ecs_cluster.silver.arn]
      lastStatus = ["STOPPED"]
      "$or" = [
        { containers = { exitCode = [{ "anything-but" = 0 }] } },
        { stopCode = [{ "anything-but" = "EssentialContainerExited" }] },
      ]
    }
  })
}

# The mail is plain text naming the task, its exit code and where its log is, instead of the raw
# ECS event; a missing exit code means the container never ran.
resource "aws_cloudwatch_event_target" "task_failed" {
  rule = aws_cloudwatch_event_rule.task_failed.name
  arn  = aws_sns_topic.alerts.arn

  input_transformer {
    input_paths = {
      task       = "$.detail.taskArn"
      started_by = "$.detail.startedBy"
      stopped_at = "$.detail.stoppedAt"
      exit_code  = "$.detail.containers[0].exitCode"
      reason     = "$.detail.stoppedReason"
    }
    # A JSON string, built by hand: jsonencode would escape the <placeholders>.
    input_template = "\"${join("\\n", [
      "A Silver task failed.",
      "",
      "Task: <task>",
      "Started by: <started_by>",
      "Stopped at (UTC): <stopped_at>",
      "Exit code: <exit_code>",
      "Reason: <reason>",
      "",
      "Exit codes: 1 = landing or a table schema was refused (the log names the table and run); 137 = out of memory; 139 = the process crashed; none = the container never started.",
      "Log: CloudWatch log group ${aws_cloudwatch_log_group.silver.name}, stream silver/silver/ plus the last part of the task ARN.",
      "The queue retries the run up to three times; after that the dead-letter alarm follows. Runbook: docs/silver-rebuild.md.",
    ])}\""
  }
}

resource "aws_cloudwatch_metric_alarm" "dead_letter" {
  alarm_name          = "${local.name}-dead-letter"
  alarm_description   = "Three Silver starts in a row failed. Read the task log, fix, purge the dead-letter queue; never redrive it."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.dead_letter.name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "pending_runs" {
  alarm_name          = "${local.name}-pending-runs"
  alarm_description   = "Landing runs have waited for Silver for six hours."
  namespace           = local.name
  metric_name         = "PendingRuns"
  statistic           = "Minimum"
  period              = 3600
  evaluation_periods  = 6
  datapoints_to_alarm = 6
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}
