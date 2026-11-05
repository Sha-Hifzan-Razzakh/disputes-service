data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, 2)
}

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags                 = { Name = "disputes-${var.env}" }
}

# public: load balancer and NAT only
resource "aws_subnet" "public" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone = local.azs[count.index]
  tags              = { Name = "disputes-${var.env}-public-${count.index}" }
}

# private: ECS tasks (egress through NAT)
resource "aws_subnet" "app" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, 10 + count.index)
  availability_zone = local.azs[count.index]
  tags              = { Name = "disputes-${var.env}-app-${count.index}" }
}

# isolated: database, no route out of the VPC at all
resource "aws_subnet" "db" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, 20 + count.index)
  availability_zone = local.azs[count.index]
  tags              = { Name = "disputes-${var.env}-db-${count.index}" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
}

resource "aws_eip" "nat" {
  domain = "vpc"
}

resource "aws_nat_gateway" "main" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id
  depends_on    = [aws_internet_gateway.main]
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
}

resource "aws_route_table" "app" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main.id
  }
}

resource "aws_route_table" "db" {
  vpc_id = aws_vpc.main.id # local routes only
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "app" {
  count          = 2
  subnet_id      = aws_subnet.app[count.index].id
  route_table_id = aws_route_table.app.id
}

resource "aws_route_table_association" "db" {
  count          = 2
  subnet_id      = aws_subnet.db[count.index].id
  route_table_id = aws_route_table.db.id
}

resource "aws_default_security_group" "main" {
  vpc_id = aws_vpc.main.id # no rules: nothing uses the default group
}

resource "aws_flow_log" "vpc" {
  vpc_id               = aws_vpc.main.id
  traffic_type         = "REJECT"
  log_destination_type = "cloud-watch-logs"
  log_destination      = aws_cloudwatch_log_group.flow.arn
  iam_role_arn         = aws_iam_role.flow_logs.arn
}

# --- security groups: every path the design needs and nothing else (T24.8)

resource "aws_security_group" "alb" {
  name        = "disputes-${var.env}-alb"
  description = "Public HTTPS to the load balancer"
  vpc_id      = aws_vpc.main.id
}

resource "aws_security_group" "api" {
  name        = "disputes-${var.env}-api"
  description = "API tasks and the migrate task"
  vpc_id      = aws_vpc.main.id
}

resource "aws_security_group" "mcp" {
  name        = "disputes-${var.env}-mcp"
  description = "MCP servers, reachable from the API only"
  vpc_id      = aws_vpc.main.id
}

resource "aws_security_group" "db" {
  name        = "disputes-${var.env}-db"
  description = "Postgres, reachable from the API and migrate tasks only"
  vpc_id      = aws_vpc.main.id
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTPS from clients"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_egress_rule" "alb_to_api" {
  security_group_id            = aws_security_group.alb.id
  description                  = "to API tasks"
  referenced_security_group_id = aws_security_group.api.id
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}

resource "aws_vpc_security_group_ingress_rule" "api_from_alb" {
  security_group_id            = aws_security_group.api.id
  description                  = "from the load balancer"
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}

resource "aws_vpc_security_group_egress_rule" "api_to_db" {
  security_group_id            = aws_security_group.api.id
  description                  = "to Postgres"
  referenced_security_group_id = aws_security_group.db.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

resource "aws_vpc_security_group_egress_rule" "api_to_mcp" {
  security_group_id            = aws_security_group.api.id
  description                  = "to MCP servers"
  referenced_security_group_id = aws_security_group.mcp.id
  ip_protocol                  = "tcp"
  from_port                    = 8101
  to_port                      = 8102
}

resource "aws_vpc_security_group_egress_rule" "api_https" {
  # ECR, Secrets Manager, CloudWatch through NAT; plus the model provider when enabled
  security_group_id = aws_security_group.api.id
  description       = "HTTPS to AWS APIs and, when enabled, the model provider"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_ingress_rule" "mcp_from_api" {
  security_group_id            = aws_security_group.mcp.id
  description                  = "from the API"
  referenced_security_group_id = aws_security_group.api.id
  ip_protocol                  = "tcp"
  from_port                    = 8101
  to_port                      = 8102
}

resource "aws_vpc_security_group_egress_rule" "mcp_https" {
  security_group_id = aws_security_group.mcp.id
  description       = "HTTPS to AWS APIs (image pull, logs, secrets)"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_ingress_rule" "db_from_api" {
  security_group_id            = aws_security_group.db.id
  description                  = "from API and migrate tasks"
  referenced_security_group_id = aws_security_group.api.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}
