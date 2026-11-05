resource "aws_ecr_repository" "app" {
  name                 = "disputes-service"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration {
    scan_on_push = true
  }
  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.app.arn
  }
}

resource "aws_cloudwatch_log_group" "app" {
  for_each          = toset(["api", "payments", "network-gateway", "migrate"])
  name              = "/disputes/${var.env}/${each.key}"
  retention_in_days = 365
  kms_key_id        = aws_kms_key.app.arn
}

resource "aws_cloudwatch_log_group" "flow" {
  name              = "/disputes/${var.env}/vpc-flow"
  retention_in_days = 365
  kms_key_id        = aws_kms_key.app.arn
}
