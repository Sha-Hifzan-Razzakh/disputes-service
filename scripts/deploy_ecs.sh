#!/usr/bin/env bash
# Promote one image (by digest) to an AWS environment built from infra/terraform (deploy.yml, job prod).
#   scripts/deploy_ecs.sh <env> <source image@sha256:...>
# Needs AWS credentials (OIDC in CI), terraform, docker buildx logged in to the source registry, uv, and
# TF_VAR_certificate_arn. Order: copy the exact digest to ECR -> migrate task (waited for, exit 0 required)
# -> services -> each service stable on the task definition just applied (a circuit-breaker rollback lands on
# the old one and fails this script) -> synthetic check through the load balancer.
set -euo pipefail
ENV=$1; SRC=$2
DIGEST=${SRC#*@}
cd infra/terraform
terraform init -input=false -backend-config="envs/backend-$ENV.hcl"
TFV=(-input=false -auto-approve -var-file="envs/$ENV.tfvars")

if ! REPO=$(terraform output -raw ecr_repository 2>/dev/null); then   # first deploy: the repository does not exist yet
  terraform apply "${TFV[@]}" -var image=bootstrap -target=aws_ecr_repository.app
  REPO=$(terraform output -raw ecr_repository)
fi
aws ecr get-login-password | docker login --username AWS --password-stdin "${REPO%%/*}"
docker buildx imagetools create --tag "$REPO:${GITHUB_SHA:-manual}" "$SRC"     # same manifest, same digest
IMAGE="$REPO@$DIGEST"

echo "::group::migrate"
terraform apply "${TFV[@]}" -var "image=$IMAGE" -target=aws_ecs_task_definition.migrate -target=aws_ecs_cluster.main \
  -target=aws_subnet.app -target=aws_security_group.api
CLUSTER=$(terraform output -raw cluster)
NET=$(terraform output -json migrate_network)
TASK=$(aws ecs run-task --cluster "$CLUSTER" --launch-type FARGATE \
  --task-definition "$(terraform output -raw migrate_task_definition)" \
  --network-configuration "{\"awsvpcConfiguration\": $NET}" --query 'tasks[0].taskArn' --output text)
aws ecs wait tasks-stopped --cluster "$CLUSTER" --tasks "$TASK"
CODE=$(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$TASK" --query 'tasks[0].containers[0].exitCode' --output text)
[ "$CODE" = "0" ] || { echo "migrations failed (exit $CODE): services not touched"; exit 1; }
echo "::endgroup::"

echo "::group::rollout"
terraform apply "${TFV[@]}" -var "image=$IMAGE"
declare -A WANT
while IFS=$'\t' read -r svc arn; do WANT[$svc]=$arn; done < <(terraform output -json service_task_definitions | jq -r 'to_entries[] | [.key, .value] | @tsv')
for svc in payments network-gateway api; do
  aws ecs wait services-stable --cluster "$CLUSTER" --services "$svc"
  RUNNING=$(aws ecs describe-services --cluster "$CLUSTER" --services "$svc" \
    --query 'services[0].deployments[?status==`PRIMARY`].taskDefinition | [0]' --output text)
  if [ "$RUNNING" != "${WANT[$svc]}" ]; then
    echo "$svc is running $RUNNING, not ${WANT[$svc]}: the circuit breaker rolled the release back"; exit 1
  fi
done
echo "::endgroup::"

URL=$(terraform output -raw url)
cd ../..
APP_JWT_SECRET=$(aws secretsmanager get-secret-value --secret-id "disputes/$ENV/jwt_secret" --query SecretString --output text) \
  uv run --no-sync python scripts/synthetic.py --base "$URL"
