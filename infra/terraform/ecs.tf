resource "aws_ecs_cluster" "main" {
  name = var.project
}

resource "aws_cloudwatch_log_group" "app" {
  for_each          = toset(["pipeline", "web"])
  name              = "/ecs/${var.project}/${each.key}"
  retention_in_days = 30
}

locals {
  rds_ca = "/etc/ssl/rds-global-bundle.pem" # baked into both images
  logs = { for k, g in aws_cloudwatch_log_group.app : k => {
    logDriver = "awslogs"
    options = {
      "awslogs-group"         = g.name
      "awslogs-region"        = var.region
      "awslogs-stream-prefix" = k
    }
  } }
}

# ------------------------------------------------------------ pipeline

resource "aws_ecs_task_definition" "pipeline" {
  family                   = "${var.project}-pipeline"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.pipeline_cpu
  memory                   = var.pipeline_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.pipeline_task.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }
  container_definitions = jsonencode([{
    name      = "pipeline"
    image     = "${aws_ecr_repository.repo["pipeline"].repository_url}:${var.image_tag}"
    essential = true
    command   = ["python", "-m", "src.poll"]
    environment = [
      { name = "PGSSLMODE", value = "verify-full" },
      { name = "PGSSLROOTCERT", value = local.rds_ca },
    ]
    secrets = [
      { name = "DATABASE_URL", valueFrom = aws_secretsmanager_secret.database_url.arn },
      { name = "GROQ_API_KEY", valueFrom = aws_secretsmanager_secret.groq_api_key.arn },
    ]
    logConfiguration = local.logs["pipeline"]
  }])
}

# Daily refresh cycle (Phase 9) - the cloud equivalent of scripts/schedule.sh.
resource "aws_scheduler_schedule" "pipeline" {
  name                         = "${var.project}-daily"
  schedule_expression          = var.pipeline_schedule
  schedule_expression_timezone = var.pipeline_schedule_timezone
  flexible_time_window { mode = "OFF" }

  target {
    arn      = aws_ecs_cluster.main.arn
    role_arn = aws_iam_role.scheduler.arn
    ecs_parameters {
      task_definition_arn = aws_ecs_task_definition.pipeline.arn_without_revision
      launch_type         = "FARGATE"
      network_configuration {
        subnets          = aws_subnet.public[*].id
        security_groups  = [aws_security_group.pipeline.id]
        assign_public_ip = true
      }
    }
    retry_policy {
      maximum_retry_attempts = 1
    }
  }
}

# ----------------------------------------------------------------- web

resource "aws_ecs_task_definition" "web" {
  family                   = "${var.project}-web"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.web_task.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }
  container_definitions = jsonencode([{
    name         = "web"
    image        = "${aws_ecr_repository.repo["web"].repository_url}:${var.image_tag}"
    essential    = true
    portMappings = [{ containerPort = 3000, protocol = "tcp" }]
    environment  = [{ name = "DB_SSL_CA_FILE", value = local.rds_ca }]
    secrets = [
      { name = "DATABASE_URL", valueFrom = aws_secretsmanager_secret.database_url.arn },
    ]
    logConfiguration = local.logs["web"]
  }])
}

resource "aws_lb" "web" {
  name               = var.project
  load_balancer_type = "application"
  internal           = false
  subnets            = aws_subnet.public[*].id
  security_groups    = [aws_security_group.alb.id]
}

resource "aws_lb_target_group" "web" {
  name                 = "${var.project}-web"
  vpc_id               = aws_vpc.main.id
  target_type          = "ip"
  port                 = 3000
  protocol             = "HTTP"
  deregistration_delay = 15
  health_check {
    path                = "/api/health" # also checks the DB answers
    matcher             = "200"
    interval            = 30
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.web.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.web.arn
  }
}

resource "aws_ecs_service" "web" {
  name            = "web"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.web.arn
  desired_count   = var.web_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.web.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.web.arn
    container_name   = "web"
    container_port   = 3000
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  depends_on = [aws_lb_listener.http]
}
