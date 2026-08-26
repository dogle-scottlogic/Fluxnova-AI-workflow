#!/usr/bin/env bash
# eval-service-worker: standalone Podman pod that subscribes to the Camunda/Fluxnova
# external-task topic (default agent-output-eval) and scores completed agentic
# subprocess runs against the decision_quality MLflow judge. See
# EVAL-SERVICE-WORKER-PLAN.md at the repo root for background/scope.
#
# Requires fluxnova-local and mlflow-local (fluxnova-up.sh / mlflow-up.sh) already
# running on the shared fluxnova-dev network — this worker reaches both by pod name
# (see eval-service-config.yml).
#
# Usage (from this directory):
#   ./eval-service-up.sh
#   ./eval-service-down.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL="*"

POD_NAME="${EVAL_SERVICE_POD_NAME:-eval-service-local}"
IMAGE_NAME="${EVAL_SERVICE_IMAGE:-eval-service-worker:local}"
DEV_NETWORK="${DEV_NETWORK:-fluxnova-dev}"
REPO_ROOT="$(cd .. && pwd)"

# Load .env (KEY=VALUE lines) if present.
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

podman network create --ignore "$DEV_NETWORK"

echo "Building $IMAGE_NAME..."
podman build -f "$REPO_ROOT/eval-service-worker/Dockerfile" -t "$IMAGE_NAME" "$REPO_ROOT"

echo "Creating pod '$POD_NAME'..."
podman pod create --name "$POD_NAME" \
  --network "$DEV_NETWORK" \
  --add-host host.containers.internal:host-gateway

echo "Starting eval-service-worker..."
podman run -d --pod "$POD_NAME" --name eval-service-worker \
  -v "$(pwd)/eval-service-config.yml:/app/config/loan-assesment.yml:ro" \
  "$IMAGE_NAME"

echo
echo "Worker running. Topic: agent-output-eval (see eval-service-config.yml to change)"
echo "Logs:          podman logs -f eval-service-worker"
echo "Stop/remove:   ./eval-service-down.sh"
