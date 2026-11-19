output "dashboard_url" {
  value = "http://${aws_lb.web.dns_name}"
}

output "ecr_pipeline" {
  value = aws_ecr_repository.repo["pipeline"].repository_url
}

output "ecr_web" {
  value = aws_ecr_repository.repo["web"].repository_url
}

output "cluster" {
  value = aws_ecs_cluster.main.name
}

output "pipeline_task_family" {
  value = aws_ecs_task_definition.pipeline.family
}

output "seed_bucket" {
  value = aws_s3_bucket.seed.bucket
}

output "groq_secret_arn" {
  value = aws_secretsmanager_secret.groq_api_key.arn
}

output "task_subnets" {
  value = join(",", aws_subnet.public[*].id)
}

output "pipeline_security_group" {
  value = aws_security_group.pipeline.id
}
