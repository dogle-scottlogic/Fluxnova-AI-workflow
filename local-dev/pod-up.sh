#!/usr/bin/env bash
# Local development stack: Fluxnova + OTel Collector, run in a single Podman pod
# (alternative to docker-compose.yml — see local-dev/README.md's "Option B: a single
# Podman pod" section for the tradeoffs between the two). Works from Git Bash on Windows too.
#
# All containers in a pod share one network namespace, so they talk to each other via
# `localhost` rather than by service name — that's why default-pod.yml points
# exporterEndpoint at http://localhost:4317 instead of http://otel-collector:4317.
#
# Usage (from this directory):
#   ./pod-up.sh
#   ./pod-down.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

# Prevent Git Bash/MSYS from mangling container-side paths (e.g. /etc/otelcol/config.yaml)
# in -v/--add-host arguments by rewriting them as Windows paths.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL="*"

POD_NAME="${POD_NAME:-fluxnova-local}"

# Load .env (KEY=VALUE lines) if present, without requiring a compose provider.
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

FLUXNOVA_IMAGE="${FLUXNOVA_IMAGE:-fluxnova:local}"
FLUXNOVA_PORT="${FLUXNOVA_PORT:-8080}"
OTEL_COLLECTOR_IMAGE="${OTEL_COLLECTOR_IMAGE:-docker.io/otel/opentelemetry-collector-contrib:latest}"
FLUXNOVA_OTEL_USERLIB_DIR="${FLUXNOVA_OTEL_USERLIB_DIR:-../../fluxnova-bpm-platform/local/configuration/userlib}"
USERLIB_DIR_RESOLVED="$(cd "$FLUXNOVA_OTEL_USERLIB_DIR" && pwd)"

echo "Creating pod '$POD_NAME' (fluxnova:${FLUXNOVA_PORT}->8080, otel-collector:4317/4318)..."
# --add-host must be set at pod level (not per-container) since networking is shared.
podman pod create --name "$POD_NAME" \
  -p "${FLUXNOVA_PORT}:8080" \
  -p 4317:4317 \
  -p 4318:4318 \
  --add-host host.containers.internal:host-gateway

echo "Starting otel-collector..."
podman run -d --pod "$POD_NAME" --name otel-collector \
  -v "$(pwd)/otel-collector-config.yaml:/etc/otelcol/config.yaml:ro" \
  "$OTEL_COLLECTOR_IMAGE" --config=/etc/otelcol/config.yaml

echo "Starting fluxnova..."
podman run -d --pod "$POD_NAME" --name fluxnova \
  -e OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 \
  -e OTEL_EXPORTER_OTLP_PROTOCOL=grpc \
  -v "$(pwd)/default-pod.yml:/fluxnova/configuration/default.yml:ro" \
  -v "${USERLIB_DIR_RESOLVED}:/fluxnova/configuration/userlib:ro" \
  "$FLUXNOVA_IMAGE"

echo
echo "Fluxnova:      http://localhost:${FLUXNOVA_PORT}/engine-rest"
echo "OTel gRPC:     localhost:4317"
echo "OTel HTTP:     localhost:4318"
echo "Logs:          podman pod logs -f $POD_NAME   (or: podman logs -f fluxnova / otel-collector)"
echo "Stop/remove:   ./pod-down.sh"
