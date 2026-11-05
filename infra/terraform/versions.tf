terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.80"
    }
  }
  # State in S3 with locking; bucket and table per account: terraform init -backend-config=envs/backend-prod.hcl
  backend "s3" {}
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      service     = "disputes-service"
      environment = var.env
      managed_by  = "terraform"
    }
  }
}
