#!/usr/bin/env bash
# Promote one image (by digest) to an AWS environment built from infra/terraform (deploy.yml, job prod).
#   scripts/deploy_ecs.sh <env> <source image@sha256:...>
# Needs AWS credentials (OIDC in CI), terraform, docker buildx and vars TF_VAR_certificate_arn.
# Order: copy the exact digest to ECR -> migrate task (waited for) -> services -> wait stable or the circuit
# breaker rolls back (exit 1) -> synthetic check through the load balancer.
set -euo pipefail
ENV=$1; SRC=$2
DIGEST=${SRC#*@}
cd infra/terraform
terraform init -input=false -backend-config="envs/backend-$ENV.hcl"
REPO=$(terraform output -raw ecr_repository)
aws ecr get-login-password | docker login --username AWS --password-stdin "${REPO%%/*}"
docker buildx imagetools create --tag "$REPO:${GITHUB_SHA:-manual}" "$SRC"     # same manifest, same digest
IMAGE="$REPO@$DIGEST"
TF="terraform apply -input=false -auto-approve -var-file=envs/$ENV.tfvars -var image=$IMAGE"

echo "::group::migrate"
$TF -target=aws_ecs_task_definition.migrate
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
$TF
for svc in payments network-gateway api; do
  aws ecs wait services-stable --cluster "$CLUSTER" --services "$svc"
  STATE=$(aws ecs describe-services --cluster "$CLUSTER" --services "$svc" \
    --query 'services[0].deployments[?status==`PRIMARY`].rolloutState | [0]' --output text)
  [ "$STATE" = "COMPLETED" ] || { echo "$svc rollout $STATE: the circuit breaker rolled it back"; exit 1; }
done
echo "::endgroup::"

URL=$(terraform output -raw url)
APP_JWT_SECRET=$(aws secretsmanager get-secret-value --secret-id "disputes/$ENV/jwt_secret" --query SecretString --output text) \
  python ../../scripts/synthetic.py --base "$URL"
