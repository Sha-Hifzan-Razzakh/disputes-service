#!/usr/bin/env bash
# Release one image to a Kubernetes environment the way deploy.yml does, so the runbook and CI share one path.
#   scripts/deploy_k8s.sh <overlay dir> <image ref>     e.g. deploy/k8s/overlays/staging disputes-service:abc123
# Order: migrations (Job, waited for) -> workloads -> rollout status. A rollout that does not become ready within
# its progress deadline is undone automatically and the script exits 1 (T24.10 automatic abort).
set -euo pipefail
OVERLAY=$1; IMAGE=$2
NS=$(kubectl kustomize "$OVERLAY" | awk '/^  namespace:/{print $2; exit}')
render() { kubectl kustomize "$1" | sed "s#image: disputes-service:local#image: $IMAGE#"; }

echo "::group::migrate ($IMAGE)"
kubectl -n "$NS" delete job migrate --ignore-not-found --wait=true
render "$OVERLAY/migrate" | kubectl apply -f -
if ! kubectl -n "$NS" wait --for=condition=complete job/migrate --timeout=300s; then
  kubectl -n "$NS" logs job/migrate --tail=100 || true
  echo "migrations failed: workloads not touched"; exit 1
fi
echo "::endgroup::"

echo "::group::rollout"
rev() { kubectl -n "$NS" get "deploy/$1" -o jsonpath='{.metadata.annotations.deployment\.kubernetes\.io/revision}' 2>/dev/null || true; }
declare -A BEFORE
for d in payments network-gateway api; do BEFORE[$d]=$(rev "$d"); done
render "$OVERLAY" | kubectl apply -f -
failed=""
for d in payments network-gateway api; do
  kubectl -n "$NS" rollout status "deploy/$d" --timeout=180s || failed="$failed $d"
done
if [ -n "$failed" ]; then
  # undo every Deployment, not only the failed one: the release is one unit (config comes back with the
  # ReplicaSet because app-config is content-hashed)
  echo "not ready:$failed -- undoing the whole release"
  for d in payments network-gateway api; do      # only those this release changed (a no-op apply keeps its revision)
    [ -n "${BEFORE[$d]}" ] && [ "$(rev "$d")" != "${BEFORE[$d]}" ] && kubectl -n "$NS" rollout undo "deploy/$d"
  done
  for d in payments network-gateway api; do kubectl -n "$NS" rollout status "deploy/$d" --timeout=180s; done
  exit 1
fi
echo "::endgroup::"
