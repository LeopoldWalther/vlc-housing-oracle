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

resource "aws_cloudwatch_event_target" "task_failed" {
  rule = aws_cloudwatch_event_rule.task_failed.name
  arn  = aws_sns_topic.alerts.arn
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
