# The Silver task: a public image on Fargate (ARM64) in the default VPC, with a public IP for egress
# instead of a NAT gateway, as the acquisition repository runs its tasks. It reads landing v3 and
# writes Silver; nothing else in the account is reachable from its role.

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

locals {
  name       = "vlc-housing-silver"
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
  bucket_arn = "arn:${local.partition}:s3:::${var.lake_bucket}"
  glue_arn   = "arn:${local.partition}:glue:${var.region}:${local.account_id}"
}

resource "aws_security_group" "silver" {
  name        = local.name
  description = "Silver task: no inbound, outbound HTTPS to S3, Glue, CloudWatch and the image registry."
  vpc_id      = data.aws_vpc.default.id

  egress {
    description = "HTTPS to AWS APIs and the public image registry"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_ecs_cluster" "silver" {
  name = local.name
}

resource "aws_cloudwatch_log_group" "silver" {
  name              = "/ecs/${local.name}"
  retention_in_days = 30
}

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

# --- Execution role: what ECS needs to start the task. The image is public, so logs only. ----------

resource "aws_iam_role" "execution" {
  name               = "${local.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_iam_policy_document" "execution" {
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.silver.arn}:*"]
  }
}

resource "aws_iam_role_policy" "execution" {
  name   = "write-task-logs"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution.json
}

# --- Task role: what the running Silver job needs. ------------------------------------------------

resource "aws_iam_role" "task" {
  name               = "${local.name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_iam_policy_document" "task" {
  statement {
    sid       = "ReadLandingV3"
    actions   = ["s3:GetObject"]
    resources = ["${local.bucket_arn}/landing/v3/*"]
  }

  statement {
    sid       = "ReadWriteSilver"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"]
    resources = ["${local.bucket_arn}/silver/*"]
  }

  statement {
    sid       = "ListOnlyLandingV3AndSilver"
    actions   = ["s3:ListBucket"]
    resources = [local.bucket_arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["landing/v3/*", "silver/*"]
    }
  }

  # CreateDatabase on this one database only: PyIceberg's create_namespace_if_not_exists tries it
  # and expects AlreadyExists; AccessDenied would stop the run.
  statement {
    sid = "SilverCatalog"
    actions = [
      "glue:GetDatabase",
      "glue:CreateDatabase",
      "glue:GetTable",
      "glue:GetTables",
      "glue:CreateTable",
      "glue:UpdateTable",
    ]
    resources = [
      "${local.glue_arn}:catalog",
      "${local.glue_arn}:database/${aws_glue_catalog_database.silver.name}",
      "${local.glue_arn}:table/${aws_glue_catalog_database.silver.name}/*",
    ]
  }

  # PutMetricData supports no resource-level permission; the namespace condition is the boundary.
  statement {
    sid       = "PublishPendingRuns"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = [local.name]
    }
  }
}

resource "aws_iam_role_policy" "task" {
  name   = "silver-from-landing-v3"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task.json
}

# --- Task definition ------------------------------------------------------------------------------

resource "aws_ecs_task_definition" "silver" {
  family                   = local.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 1024
  memory                   = 2048
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([
    {
      name      = "silver"
      image     = var.silver_image
      essential = true
      command = [
        "--landing", "s3://${var.lake_bucket}/landing/v3",
        "--catalog", "silver",
        "--mode", "incremental",
        "--metrics",
      ]
      environment = [
        { name = "PYICEBERG_CATALOG__SILVER__TYPE", value = "glue" },
        { name = "PYICEBERG_CATALOG__SILVER__GLUE__REGION", value = var.region },
        { name = "AWS_REGION", value = var.region },
        { name = "AWS_DEFAULT_REGION", value = var.region },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.silver.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "silver"
        }
      }
    }
  ])
}
