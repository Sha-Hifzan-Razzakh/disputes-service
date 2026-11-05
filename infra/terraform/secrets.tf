# Containers for the runtime secrets. Values are written out of band (docs/runbook.md, "Secrets"), never by
# Terraform, so they are not in state or in the repo. ECS injects them at task start (T24.4).
locals {
  secret_names = {
    database_url  = "APP_DATABASE_URL" # postgresql+psycopg://app:...@<endpoint>:5432/disputes?sslmode=require
    jwt_secret    = "APP_JWT_SECRET"
    mcp_payments  = "APP_MCP_PAYMENTS_TOKEN" # also MCP_PAYMENTS_TOKEN for the payments server
    mcp_network   = "APP_MCP_NETWORK_TOKEN"  # also MCP_NETWORK_TOKEN for the network-gateway server
    anthropic_key = "APP_ANTHROPIC_API_KEY"  # only read when APP_MODEL_PROVIDER=anthropic
  }
}

data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "kms" {
  #checkov:skip=CKV_AWS_109:key policy; "*" means this key only, and admin rights stay with the account root
  #checkov:skip=CKV_AWS_111:key policy; see above
  #checkov:skip=CKV_AWS_356:key policy; see above
  statement {
    sid       = "AccountAdmin"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
  }
  statement {
    sid       = "CloudWatchLogs"
    actions   = ["kms:Encrypt*", "kms:Decrypt*", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:Describe*"]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["logs.${var.region}.amazonaws.com"]
    }
    condition {
      test     = "ArnLike"
      variable = "kms:EncryptionContext:aws:logs:arn"
      values = [
        "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/disputes/${var.env}/*",
        "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:aws-waf-logs-disputes-${var.env}",
      ]
    }
  }
}

resource "aws_kms_key" "app" {
  description         = "disputes-${var.env}: secrets, image repository and logs"
  enable_key_rotation = true
  policy              = data.aws_iam_policy_document.kms.json
}

resource "aws_secretsmanager_secret" "app" {
  #checkov:skip=CKV2_AWS_57:application secrets rotate by runbook (JWT key overlap, MCP token pairs); the DB master password is rotated by RDS
  for_each                = local.secret_names
  name                    = "disputes/${var.env}/${each.key}"
  kms_key_id              = aws_kms_key.app.arn
  recovery_window_in_days = 7
}
