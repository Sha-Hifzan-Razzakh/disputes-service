resource "aws_lb" "api" {
  name                       = "disputes-${var.env}"
  load_balancer_type         = "application"
  subnets                    = aws_subnet.public[*].id
  security_groups            = [aws_security_group.alb.id]
  drop_invalid_header_fields = true
  enable_deletion_protection = true
  idle_timeout               = 75 # > SSE idle_s cap
  access_logs {
    bucket  = aws_s3_bucket.alb_logs.id
    enabled = true
  }
}

# /metrics carries per-tenant series (model spend, tool use): never through the public listener. Prometheus
# scrapes the tasks inside the VPC (review of PR #14).
resource "aws_lb_listener_rule" "block_metrics" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 1
  condition {
    path_pattern {
      values = ["/metrics", "/metrics/*"]
    }
  }
  action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      message_body = "not found"
      status_code  = "404"
    }
  }
}

resource "aws_wafv2_web_acl" "api" {
  name  = "disputes-${var.env}"
  scope = "REGIONAL"
  default_action {
    allow {}
  }
  rule {
    name     = "aws-common"
    priority = 1
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesCommonRuleSet"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "aws-common"
      sampled_requests_enabled   = true
    }
  }
  rule {
    name     = "known-bad-inputs" # includes Log4j-style payloads
    priority = 2
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "known-bad-inputs"
      sampled_requests_enabled   = true
    }
  }
  rule {
    name     = "rate-per-ip"
    priority = 3
    action {
      block {}
    }
    statement {
      rate_based_statement {
        limit              = 2000
        aggregate_key_type = "IP"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "rate-per-ip"
      sampled_requests_enabled   = true
    }
  }
  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "disputes-${var.env}"
    sampled_requests_enabled   = true
  }
}

resource "aws_wafv2_web_acl_association" "api" {
  resource_arn = aws_lb.api.arn
  web_acl_arn  = aws_wafv2_web_acl.api.arn
}

resource "aws_s3_bucket" "alb_logs" {
  #checkov:skip=CKV_AWS_144:access logs are not replicated cross-region
  #checkov:skip=CKV_AWS_145:ALB access logs only support SSE-S3
  #checkov:skip=CKV_AWS_18:this is the access-log bucket
  #checkov:skip=CKV2_AWS_62:no event consumers for access logs
  bucket_prefix = "disputes-${var.env}-alb-logs-"
}

resource "aws_s3_bucket_public_access_block" "alb_logs" {
  bucket                  = aws_s3_bucket.alb_logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id
  rule {
    id     = "expire"
    status = "Enabled"
    filter {}
    expiration {
      days = 365
    }
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

data "aws_iam_policy_document" "alb_logs" {
  statement {
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.alb_logs.arn}/AWSLogs/*"]
    principals {
      # log-delivery service principal: required in regions launched after Aug 2022 (me-central-1)
      type        = "Service"
      identifiers = ["logdelivery.elasticloadbalancing.amazonaws.com"]
    }
  }
}

resource "aws_s3_bucket_policy" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id
  policy = data.aws_iam_policy_document.alb_logs.json
}

resource "aws_lb_target_group" "api" {
  name                 = "disputes-${var.env}-api"
  port                 = 8000
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = aws_vpc.main.id
  deregistration_delay = 30
  health_check {
    path                = "/readyz" # DB + checkpointer + compiled graphs
    interval            = 10
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 5
    matcher             = "200"
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.api.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

resource "aws_cloudwatch_log_group" "waf" {
  name              = "aws-waf-logs-disputes-${var.env}" # WAF requires the aws-waf-logs- prefix
  retention_in_days = 365
  kms_key_id        = aws_kms_key.app.arn
}

resource "aws_wafv2_web_acl_logging_configuration" "api" {
  resource_arn            = aws_wafv2_web_acl.api.arn
  log_destination_configs = [aws_cloudwatch_log_group.waf.arn]
  redacted_fields {
    single_header {
      name = "authorization"
    }
  }
}
