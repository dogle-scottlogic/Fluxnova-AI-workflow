# eval-service-worker: BPMN external-task worker that scores a just-completed
# agentic subprocess run using MLflow's decision_quality judge (trace data only).
#
# Build from the repo root so the sibling fluxnova-mlflow-dataset package (a local
# path dependency, not published to PyPI) is in the build context:
#   podman build -f eval-service-worker/Dockerfile -t eval-service-worker:local .
FROM python:3.11-slim

WORKDIR /app

# Install the shared trace-reading/scoring library first (own layer — changes less
# often than the worker itself) followed by the worker package.
COPY fluxnova-mlflow-dataset /app/fluxnova-mlflow-dataset
COPY eval-service-worker /app/eval-service-worker

RUN pip install --no-cache-dir \
    /app/fluxnova-mlflow-dataset \
    /app/eval-service-worker

# Mount (or bake in) the workflow config at this path — see
# local-dev/eval-service-up.sh for how it's supplied at runtime.
ENV EVAL_SERVICE_CONFIG=/app/config/loan-assesment.yml

ENTRYPOINT ["eval-service-worker"]
CMD ["/app/config/loan-assesment.yml"]
