# ---------------------------------------------------------------
# ECS Fargate + ALB for Open WebUI
# Controlled by var.enable_webui — set to true to deploy.
# ---------------------------------------------------------------

# --- ECS Cluster ---

resource "aws_ecs_cluster" "webui" {
  count = var.enable_webui ? 1 : 0
  name  = "sp-ingest-webui"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

# --- ALB ---

resource "aws_lb" "webui" {
  count              = var.enable_webui ? 1 : 0
  name               = "sp-ingest-webui-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.webui_alb.id]
  subnets            = aws_subnet.webui_public[*].id

  tags = { Name = "sp-ingest-webui-alb" }
}

resource "aws_lb_target_group" "webui" {
  count       = var.enable_webui ? 1 : 0
  name        = "sp-ingest-webui-tg"
  port        = 8080
  protocol    = "HTTP"
  vpc_id      = aws_vpc.webui.id
  target_type = "ip"

  health_check {
    path                = "/health"
    port                = "traffic-port"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 30
    timeout             = 5
  }
}

resource "aws_lb_listener" "webui_http" {
  count             = var.enable_webui ? 1 : 0
  load_balancer_arn = aws_lb.webui[0].arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.webui[0].arn
  }
}

# --- ECS Task Definition ---

resource "aws_ecs_task_definition" "webui" {
  count                    = var.enable_webui ? 1 : 0
  family                   = "sp-ingest-webui"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.webui_task_execution[0].arn

  container_definitions = jsonencode([
    {
      name      = "open-webui"
      image     = var.open_webui_image
      essential = true

      portMappings = [
        {
          containerPort = 8080
          protocol      = "tcp"
        },
      ]

      environment = [
        { name = "WEBUI_AUTH", value = "true" },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.webui[0].name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }
    },
  ])
}

# --- ECS Service ---

resource "aws_ecs_service" "webui" {
  count           = var.enable_webui ? 1 : 0
  name            = "sp-ingest-webui"
  cluster         = aws_ecs_cluster.webui[0].id
  task_definition = aws_ecs_task_definition.webui[0].arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.webui_private[*].id
    security_groups  = [aws_security_group.webui_ecs.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.webui[0].arn
    container_name   = "open-webui"
    container_port   = 8080
  }

  depends_on = [aws_lb_listener.webui_http]
}

# --- CloudWatch Log Group ---

resource "aws_cloudwatch_log_group" "webui" {
  count             = var.enable_webui ? 1 : 0
  name              = "/ecs/sp-ingest-webui"
  retention_in_days = 30
}

# --- ECS Task Execution Role ---

resource "aws_iam_role" "webui_task_execution" {
  count = var.enable_webui ? 1 : 0
  name  = "sp-ingest-webui-task-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "webui_task_execution" {
  count      = var.enable_webui ? 1 : 0
  role       = aws_iam_role.webui_task_execution[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}
