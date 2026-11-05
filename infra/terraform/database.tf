resource "aws_db_subnet_group" "main" {
  name       = "disputes-${var.env}"
  subnet_ids = aws_subnet.db[*].id
}

resource "aws_db_parameter_group" "pg16" {
  name   = "disputes-${var.env}-pg16"
  family = "postgres16"
  parameter {
    name  = "rds.force_ssl" # matches the app's sslmode=require check
    value = "1"
  }
  parameter {
    name  = "idle_in_transaction_session_timeout" # safe since the thread lock uses an autocommit connection
    value = "60000"
  }
  parameter {
    name  = "log_min_duration_statement"
    value = "500"
  }
}

resource "aws_db_instance" "main" {
  identifier                          = "disputes-${var.env}"
  engine                              = "postgres"
  engine_version                      = "16"
  instance_class                      = var.db_instance_class
  allocated_storage                   = 50
  max_allocated_storage               = 200
  storage_encrypted                   = true
  db_name                             = "disputes"
  username                            = "disputes_admin"
  manage_master_user_password         = true # password in Secrets Manager, rotated by RDS
  db_subnet_group_name                = aws_db_subnet_group.main.name
  vpc_security_group_ids              = [aws_security_group.db.id]
  parameter_group_name                = aws_db_parameter_group.pg16.name
  publicly_accessible                 = false
  multi_az                            = var.db_multi_az
  backup_retention_period             = 14
  deletion_protection                 = true
  skip_final_snapshot                 = false
  final_snapshot_identifier           = "disputes-${var.env}-final"
  copy_tags_to_snapshot               = true
  auto_minor_version_upgrade          = true
  performance_insights_enabled        = true
  performance_insights_kms_key_id     = aws_kms_key.app.arn
  iam_database_authentication_enabled = true
  enabled_cloudwatch_logs_exports     = ["postgresql"]
  monitoring_interval                 = 60
  monitoring_role_arn                 = aws_iam_role.rds_monitoring.arn
}
