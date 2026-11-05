variable "env" {
  description = "Environment name; prod is the only AWS environment today (staging runs on kind in CI)."
  type        = string
}

variable "region" {
  type    = string
  default = "me-central-1"
}

variable "image" {
  description = "Image by digest, promoted from staging: <account>.dkr.ecr.<region>.amazonaws.com/disputes-service@sha256:..."
  type        = string
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "certificate_arn" {
  description = "ACM certificate for the public HTTPS listener."
  type        = string
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.medium"
}

variable "db_multi_az" {
  type    = bool
  default = true
}

variable "api_desired_count" {
  type    = number
  default = 3
}

variable "api_cpu" {
  type    = number
  default = 512
}

variable "api_memory" {
  type    = number
  default = 1024
}

variable "app_settings" {
  description = "Non-secret APP_* settings for this environment (docs/environments.md)."
  type        = map(string)
  default     = {}
}
