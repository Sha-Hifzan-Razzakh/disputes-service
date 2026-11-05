# terraform init -backend-config=envs/backend-prod.hcl  (bucket and lock table created once per account)
bucket         = "disputes-terraform-state-prod"
key            = "disputes-service/prod.tfstate"
region         = "me-central-1"
dynamodb_table = "disputes-terraform-locks"
encrypt        = true
