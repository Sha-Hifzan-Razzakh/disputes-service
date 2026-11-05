locals {
  app_env = merge({
    APP_ENV                   = var.env
    APP_CHECKPOINT_AUTO_SETUP = "false"
    APP_TOOLS_ENABLED         = "false" # kill switch on until start logs show no pin mismatch (docs/rollout.md)
    APP_MCP_PAYMENTS_URL      = "http://payments:8101/mcp"
    APP_MCP_NETWORK_URL       = "http://network-gateway:8102/mcp"
    APP_MODEL_PROVIDER        = "rules"
    APP_DB_POOL_SIZE          = "8"
    APP_DB_MAX_OVERFLOW       = "4"
    APP_MAX_CONCURRENT_RUNS   = "8"
    APP_OTEL_EXPORTER         = "none" # set "otlp" once an ADOT collector sidecar is added (issue)
    LANGGRAPH_STRICT_MSGPACK  = "true"
  }, var.app_settings)
  app_secrets = [for k, name in local.secret_names : {
    name      = name
    valueFrom = aws_secretsmanager_secret.app[k].arn
  } if k != "anthropic_key" || local.app_env["APP_MODEL_PROVIDER"] == "anthropic"]
  container_security = {
    user                   = "10001"
    readonlyRootFilesystem = true
    linuxParameters        = { capabilities = { drop = ["ALL"] }, initProcessEnabled = true }
    mountPoints            = [{ sourceVolume = "tmp", containerPath = "/tmp" }]
  }
  logs = { for name in ["api", "payments", "network-gateway", "migrate"] : name => {
    logDriver = "awslogs"
    options = {
      awslogs-group         = aws_cloudwatch_log_group.app[name].name
      awslogs-region        = var.region
      awslogs-stream-prefix = name
    }
  } }
}

resource "aws_ecs_cluster" "main" {
  name = "disputes-${var.env}"
  setting {
    name  = "containerInsights"
    value = "enhanced"
  }
}

resource "aws_service_discovery_http_namespace" "main" {
  name = "disputes-${var.env}"
}

# --- task definitions

resource "aws_ecs_task_definition" "api" {
  family                   = "disputes-${var.env}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.api_cpu
  memory                   = var.api_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  volume { name = "tmp" }
  container_definitions = jsonencode([merge(local.container_security, {
    name             = "api"
    image            = var.image
    essential        = true
    command          = ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
    portMappings     = [{ name = "http", containerPort = 8000, protocol = "tcp" }]
    environment      = [for k, v in local.app_env : { name = k, value = v }]
    secrets          = local.app_secrets
    stopTimeout      = 40 # > APP_RUN_TIMEOUT_S
    logConfiguration = local.logs["api"]
    healthCheck = {
      command     = ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]
      interval    = 10
      timeout     = 3
      retries     = 3
      startPeriod = 30
    }
  })])
}

resource "aws_ecs_task_definition" "migrate" {
  family                   = "disputes-${var.env}-migrate"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  volume { name = "tmp" }
  container_definitions = jsonencode([merge(local.container_security, {
    name             = "migrate"
    image            = var.image
    essential        = true
    command          = ["sh", "-c", "alembic upgrade head && python scripts/checkpoint_setup.py"]
    environment      = [for k, v in local.app_env : { name = k, value = v }]
    secrets          = local.app_secrets
    logConfiguration = local.logs["migrate"]
  })])
}

resource "aws_ecs_task_definition" "mcp" {
  for_each = {
    payments          = { port = 8101, module = "mcp_servers.payments:app", token = "mcp_payments", env = "MCP_PAYMENTS_TOKEN" }
    "network-gateway" = { port = 8102, module = "mcp_servers.network_gateway:app", token = "mcp_network", env = "MCP_NETWORK_TOKEN" }
  }
  family                   = "disputes-${var.env}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  volume { name = "tmp" }
  container_definitions = jsonencode([merge(local.container_security, {
    name             = each.key
    image            = var.image
    essential        = true
    command          = ["uvicorn", each.value.module, "--host", "0.0.0.0", "--port", tostring(each.value.port)]
    portMappings     = [{ name = "mcp", containerPort = each.value.port, protocol = "tcp" }]
    secrets          = [{ name = each.value.env, valueFrom = aws_secretsmanager_secret.app[each.value.token].arn }]
    logConfiguration = local.logs[each.key]
  })])
}

# --- services: rolling deploys that keep full capacity, abort and roll back automatically (T24.10)

resource "aws_ecs_service" "mcp" {
  for_each               = aws_ecs_task_definition.mcp
  name                   = each.key
  cluster                = aws_ecs_cluster.main.id
  task_definition        = each.value.arn
  desired_count          = 2
  launch_type            = "FARGATE"
  enable_execute_command = false
  network_configuration {
    subnets          = aws_subnet.app[*].id
    security_groups  = [aws_security_group.mcp.id]
    assign_public_ip = false
  }
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  service_connect_configuration {
    enabled   = true
    namespace = aws_service_discovery_http_namespace.main.arn
    service {
      port_name = "mcp"
      client_alias {
        dns_name = each.key
        port     = each.key == "payments" ? 8101 : 8102
      }
    }
  }
}

resource "aws_ecs_service" "api" {
  name                               = "api"
  cluster                            = aws_ecs_cluster.main.id
  task_definition                    = aws_ecs_task_definition.api.arn
  desired_count                      = var.api_desired_count
  launch_type                        = "FARGATE"
  enable_execute_command             = false
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  health_check_grace_period_seconds  = 30
  network_configuration {
    subnets          = aws_subnet.app[*].id
    security_groups  = [aws_security_group.api.id]
    assign_public_ip = false
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  service_connect_configuration {
    enabled   = true
    namespace = aws_service_discovery_http_namespace.main.arn
  }
  depends_on = [aws_lb_listener.https, aws_ecs_service.mcp]
}
