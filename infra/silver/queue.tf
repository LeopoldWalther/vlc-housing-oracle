# The serialized trigger (FEATURE-004, review H1): every start of Silver goes through one FIFO queue
# with one message group, consumed by one Lambda. A FIFO group is delivered in order and to at most
# one consumer at a time, so two Silver tasks never overlap; the consumer also adopts a task that is
# already running rather than starting a second. The message body is never read: the task finds its
# work by the set difference between landing's commits and its ingest log.

resource "aws_sqs_queue" "dead_letter" {
  name                        = "${local.name}-dead-letter.fifo"
  fifo_queue                  = true
  content_based_deduplication = true
  message_retention_seconds   = 1209600
}

# Three failed deliveries in a row mean a defect, not bad luck. The dead-letter queue stops the retry
# loop and raises an alarm; it is a circuit breaker, not a buffer: purge it after the fix, never
# redrive it, and the next start finds the same pending runs.
resource "aws_sqs_queue" "silver" {
  name                        = "${local.name}.fifo"
  fifo_queue                  = true
  content_based_deduplication = true
  # Longer than the consumer's 15-minute timeout, so a running invocation keeps its batch.
  visibility_timeout_seconds = 960
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dead_letter.arn
    maxReceiveCount     = 3
  })
}

data "aws_iam_policy_document" "queue" {
  statement {
    sid       = "CommitEventsFromTheRule"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.silver.arn]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_cloudwatch_event_rule.landing_commit.arn]
    }
  }
}

resource "aws_sqs_queue_policy" "silver" {
  queue_url = aws_sqs_queue.silver.id
  policy    = data.aws_iam_policy_document.queue.json
}

# --- The consumer -------------------------------------------------------------------------------

data "archive_file" "consumer" {
  type        = "zip"
  source_file = "${path.module}/../../src/silver/consumer.py"
  # Inside .terraform, which git ignores: the zip is a build output, never committed.
  output_path = "${path.module}/.terraform/silver-consumer.zip"
}

resource "aws_cloudwatch_log_group" "consumer" {
  name              = "/aws/lambda/${local.name}-consumer"
  retention_in_days = 30
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "consumer" {
  name               = "${local.name}-consumer"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "consumer" {
  statement {
    sid       = "StartTheSilverTask"
    actions   = ["ecs:RunTask"]
    resources = ["arn:${local.partition}:ecs:${var.region}:${local.account_id}:task-definition/${aws_ecs_task_definition.silver.family}:*"]
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [aws_ecs_cluster.silver.arn]
    }
  }

  statement {
    sid       = "SeeTheSilverTasks"
    actions   = ["ecs:ListTasks", "ecs:DescribeTasks"]
    resources = ["*"]
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [aws_ecs_cluster.silver.arn]
    }
  }

  statement {
    sid       = "HandTheTaskItsRoles"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.task.arn, aws_iam_role.execution.arn]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }

  statement {
    sid       = "ConsumeTheQueue"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes", "sqs:ChangeMessageVisibility"]
    resources = [aws_sqs_queue.silver.arn]
  }

  statement {
    sid       = "WriteConsumerLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.consumer.arn}:*"]
  }
}

resource "aws_iam_role_policy" "consumer" {
  name   = "start-and-wait-for-silver"
  role   = aws_iam_role.consumer.id
  policy = data.aws_iam_policy_document.consumer.json
}

resource "aws_lambda_function" "consumer" {
  function_name    = "${local.name}-consumer"
  role             = aws_iam_role.consumer.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "consumer.handler"
  filename         = data.archive_file.consumer.output_path
  source_code_hash = data.archive_file.consumer.output_base64sha256
  memory_size      = 128
  timeout          = 900
  # Belt and braces: the FIFO group already serializes. An account whose concurrency limit is 10
  # refuses any reservation; set consumer_reserved_concurrency = null there.
  reserved_concurrent_executions = var.consumer_reserved_concurrency

  environment {
    variables = {
      SILVER_CLUSTER          = aws_ecs_cluster.silver.arn
      SILVER_TASK_DEFINITION  = aws_ecs_task_definition.silver.arn
      SILVER_TASK_FAMILY      = aws_ecs_task_definition.silver.family
      SILVER_SUBNETS          = join(",", data.aws_subnets.default.ids)
      SILVER_SECURITY_GROUP   = aws_security_group.silver.id
      SILVER_ASSIGN_PUBLIC_IP = "ENABLED"
    }
  }

  depends_on = [aws_cloudwatch_log_group.consumer, aws_iam_role_policy.consumer]
}

# No batching window: FIFO event source mappings do not support one (review R2-M2), and messages
# that arrive while a task runs wait behind the in-flight batch anyway.
resource "aws_lambda_event_source_mapping" "consumer" {
  event_source_arn = aws_sqs_queue.silver.arn
  function_name    = aws_lambda_function.consumer.arn
  batch_size       = 10
  enabled          = var.consumer_enabled
}
