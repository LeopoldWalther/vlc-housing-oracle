# The text labeller (ADR 0005): a second task definition on the Silver cluster and image, started
# when a Silver task ends with exit code 0. Each run collects the message batches that have ended
# and submits the pending sale descriptions as one new batch; it never waits for a batch.
#
# The Claude API key is a SecureString in the Parameter Store that the operator creates by hand.
# Terraform only names it: ECS reads it into the task's environment as ANTHROPIC_API_KEY, so its
# value is in no state, argument or log.

locals {
  labeller_name        = "${local.name}-labeller"
  label_answers        = "enrichment/v1/text_label"
  label_batches        = "enrichment/v1/text_label_batch"
  label_key_parameter  = "arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter/${trimprefix(var.label_api_key_parameter, "/")}"
  labeller_definitions = "arn:${local.partition}:ecs:${var.region}:${local.account_id}:task-definition/${local.labeller_name}:*"
}

# --- Execution role: logs and the one parameter, nothing else. --------------------------------------

resource "aws_iam_role" "labeller_execution" {
  name               = "${local.labeller_name}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

# The parameter is encrypted with the account's default key for the Parameter Store, which
# needs no kms:Decrypt grant of its own.
data "aws_iam_policy_document" "labeller_execution" {
  statement {
    sid       = "WriteTaskLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.silver.arn}:*"]
  }

  statement {
    sid       = "ReadTheApiKey"
    actions   = ["ssm:GetParameters"]
    resources = [local.label_key_parameter]
  }
}

resource "aws_iam_role_policy" "labeller_execution" {
  name   = "logs-and-api-key"
  role   = aws_iam_role.labeller_execution.id
  policy = data.aws_iam_policy_document.labeller_execution.json
}

# --- Task role: read Silver, write the answers and the batch records. -------------------------------

resource "aws_iam_role" "labeller_task" {
  name               = "${local.labeller_name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_iam_policy_document" "labeller_task" {
  statement {
    sid       = "ReadSilver"
    actions   = ["s3:GetObject"]
    resources = ["${local.bucket_arn}/silver/*"]
  }

  statement {
    sid       = "ReadWriteAnswersAndBatchRecords"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${local.bucket_arn}/${local.label_answers}/*", "${local.bucket_arn}/${local.label_batches}/*"]
  }

  statement {
    sid       = "ListOnlySilverAnswersAndBatchRecords"
    actions   = ["s3:ListBucket"]
    resources = [local.bucket_arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["silver/*", "${local.label_answers}/*", "${local.label_batches}/*"]
    }
  }

  statement {
    sid     = "ReadTheSilverCatalog"
    actions = ["glue:GetDatabase", "glue:GetTable", "glue:GetTables"]
    resources = [
      "${local.glue_arn}:catalog",
      "${local.glue_arn}:database/${aws_glue_catalog_database.silver.name}",
      "${local.glue_arn}:table/${aws_glue_catalog_database.silver.name}/*",
    ]
  }
}

resource "aws_iam_role_policy" "labeller_task" {
  name   = "text-labels-from-silver"
  role   = aws_iam_role.labeller_task.id
  policy = data.aws_iam_policy_document.labeller_task.json
}

# --- Task definition --------------------------------------------------------------------------------

resource "aws_ecs_task_definition" "labeller" {
  family                   = local.labeller_name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 1024
  execution_role_arn       = aws_iam_role.labeller_execution.arn
  task_role_arn            = aws_iam_role.labeller_task.arn

  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([
    {
      name       = "labeller"
      image      = var.silver_image
      essential  = true
      entryPoint = ["python", "-m", "src.enrichment.text_labels"]
      command = [
        "--catalog", "silver",
        "--labels", "s3://${var.lake_bucket}/${local.label_answers}",
        "--batches", "s3://${var.lake_bucket}/${local.label_batches}",
        "--model-id", var.label_model_id,
        "--max-texts", tostring(var.label_max_texts),
      ]
      environment = [
        { name = "PYICEBERG_CATALOG__SILVER__TYPE", value = "glue" },
        { name = "PYICEBERG_CATALOG__SILVER__GLUE__REGION", value = var.region },
        { name = "AWS_REGION", value = var.region },
        { name = "AWS_DEFAULT_REGION", value = var.region },
      ]
      secrets = [
        { name = "ANTHROPIC_API_KEY", valueFrom = local.label_key_parameter },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.silver.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "labeller"
        }
      }
    }
  ])
}

# --- Start rule: a Silver task that ended with exit code 0. -------------------------------------------

# The group names the Silver family: a rule on the cluster alone would let a finished labeller
# start the next one without end.
resource "aws_cloudwatch_event_rule" "labeller" {
  name        = local.labeller_name
  description = "A Silver task succeeded: run the text labeller."
  state       = var.labeller_enabled ? "ENABLED" : "DISABLED"

  event_pattern = jsonencode({
    source      = ["aws.ecs"]
    detail-type = ["ECS Task State Change"]
    detail = {
      clusterArn = [aws_ecs_cluster.silver.arn]
      group      = ["family:${aws_ecs_task_definition.silver.family}"]
      lastStatus = ["STOPPED"]
      stopCode   = ["EssentialContainerExited"]
      containers = { exitCode = [0] }
    }
  })
}

data "aws_iam_policy_document" "events_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "labeller_start" {
  name               = "${local.labeller_name}-start"
  assume_role_policy = data.aws_iam_policy_document.events_assume.json
}

data "aws_iam_policy_document" "labeller_start" {
  statement {
    sid       = "StartTheLabeller"
    actions   = ["ecs:RunTask"]
    resources = [local.labeller_definitions]
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [aws_ecs_cluster.silver.arn]
    }
  }

  statement {
    sid       = "HandTheLabellerItsRoles"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.labeller_task.arn, aws_iam_role.labeller_execution.arn]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "labeller_start" {
  name   = "run-the-labeller"
  role   = aws_iam_role.labeller_start.id
  policy = data.aws_iam_policy_document.labeller_start.json
}

resource "aws_cloudwatch_event_target" "labeller" {
  rule     = aws_cloudwatch_event_rule.labeller.name
  arn      = aws_ecs_cluster.silver.arn
  role_arn = aws_iam_role.labeller_start.arn

  ecs_target {
    task_definition_arn = aws_ecs_task_definition.labeller.arn
    launch_type         = "FARGATE"
    task_count          = 1

    network_configuration {
      subnets          = data.aws_subnets.default.ids
      security_groups  = [aws_security_group.silver.id]
      assign_public_ip = true
    }
  }
}
