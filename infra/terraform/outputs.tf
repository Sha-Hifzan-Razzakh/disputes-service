output "url" {
  value = "https://${aws_lb.api.dns_name}"
}

output "cluster" {
  value = aws_ecs_cluster.main.name
}

output "ecr_repository" {
  value = aws_ecr_repository.app.repository_url
}

output "service_task_definitions" {
  description = "Task definition each service must be running after a deploy (rollback detection)"
  value = merge(
    { api = aws_ecs_task_definition.api.arn },
    { for k, td in aws_ecs_task_definition.mcp : k => td.arn },
  )
}

output "migrate_task_definition" {
  value = aws_ecs_task_definition.migrate.arn
}

output "migrate_network" {
  description = "awsvpcConfiguration for `aws ecs run-task` (scripts/deploy_ecs.sh)"
  value = {
    subnets        = aws_subnet.app[*].id
    securityGroups = [aws_security_group.api.id]
    assignPublicIp = "DISABLED"
  }
}

output "db_endpoint" {
  value = aws_db_instance.main.address
}
