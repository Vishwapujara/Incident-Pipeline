#!/bin/bash
# AWS operations that sit outside Terraform. Run from the repo root after
# `terraform -chdir=infra/terraform apply`.
#
#   scripts/aws.sh push   build both images for ARM64, push to ECR, redeploy the dashboard
#   scripts/aws.sh seed   upload the SQLite working DB and load it into RDS (one-off)
#   scripts/aws.sh run    run one pipeline cycle now (same task the daily schedule runs)
#   scripts/aws.sh logs   follow the pipeline logs (logs web for the dashboard)

set -euo pipefail
cd "$(dirname "$0")/.."

tf() { terraform -chdir=infra/terraform output -raw "$1"; }
REGION=$(aws configure get region || echo us-east-1)
PROJECT=incident-pipeline

run_task() {  # run_task [command-override-json]
  local overrides='{}'
  [ $# -gt 0 ] && overrides="{\"containerOverrides\":[{\"name\":\"pipeline\",\"command\":$1}]}"
  aws ecs run-task --cluster "$(tf cluster)" --launch-type FARGATE \
    --task-definition "$(tf pipeline_task_family)" \
    --network-configuration "awsvpcConfiguration={subnets=[$(tf task_subnets)],securityGroups=[$(tf pipeline_security_group)],assignPublicIp=ENABLED}" \
    --overrides "$overrides" --query 'tasks[0].taskArn' --output text
}

case "${1:-}" in
  push)
    registry=$(tf ecr_web | cut -d/ -f1)
    aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$registry"
    docker buildx build --platform linux/arm64 -f docker/pipeline.Dockerfile -t "$(tf ecr_pipeline):latest" --push .
    docker buildx build --platform linux/arm64 -t "$(tf ecr_web):latest" --push web
    aws ecs update-service --cluster "$(tf cluster)" --service web --force-new-deployment \
      --query 'service.serviceName' --output text
    ;;
  seed)
    src=${2:-data/processed/incidents.db}
    aws s3 cp "$src" "s3://$(tf seed_bucket)/incidents.db"
    run_task "[\"python\",\"-m\",\"src.migrate_to_postgres\",\"--source\",\"s3://$(tf seed_bucket)/incidents.db\",\"--replace\"]"
    echo "Seeding started - follow with: scripts/aws.sh logs"
    ;;
  run)
    run_task
    ;;
  logs)
    aws logs tail "/ecs/$PROJECT/${2:-pipeline}" --follow --since 1h
    ;;
  *)
    sed -n '2,9p' "$0"; exit 1
    ;;
esac
