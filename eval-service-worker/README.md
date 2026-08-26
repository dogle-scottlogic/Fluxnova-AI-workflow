# eval-service-worker

A standalone BPMN external-task worker that scores a just-completed agentic subprocess run against the
`decision_quality` MLflow judge, reading data purely from MLflow's trace store — no BPMN parsing, no Fluxnova REST
history calls. See `EVAL-SERVICE-WORKER-PLAN.md` at the repo root for the full background/scope and
`local-dev/README.md`'s "eval-service-worker" section for how to run it as a Podman pod.

**First-pass scope:** only the `decision_quality` judge, no deterministic scorers, no BPMN wiring yet (the worker
subscribes to a topic — default `agent-output-eval` — but nothing in `loan-assesment.bpmn` creates tasks on it yet).
It logs both a trace assessment and an MLflow evaluation-dataset record, and completes its task with
`evalPassed` (Boolean) / `evalRationale` (String) output variables for a later BPMN gateway to use.

## Install (editable, into the harness venv or your own)

```bash
pip install -e ../fluxnova-mlflow-dataset   # shared trace-reader + judge definition
pip install -e .
```

## Run as a worker (host)

```bash
eval-service-worker ../harness/config/loan-assesment.yml
```

Subscribes to the configured topic (`eval_service.topic`, default `agent-output-eval`) against `fluxnova_url` from
the config file, and blocks until interrupted (Ctrl+C). Requires `FLUXNOVA_USERNAME`/`FLUXNOVA_PASSWORD` env vars
only if the engine has auth enabled.

## Score a single completed run manually (no BPMN task needed)

Useful for testing before the BPMN gateway exists, or for one-off re-scoring:

```python
from eval_service_worker.scoring import score_process_instance

outcome = score_process_instance(
    "<processInstanceId>",
    tracking_uri="http://localhost:5000",
    process_key="loanAssessmentProcess",
    experiment_name=None,          # or an explicit override, see main README
    judge_model="gateway:/fluxnova-judge",
)
print(outcome.eval_passed, outcome.eval_rationale)
```

## Config

Reads the same YAML file used by `harness`/`fluxnova-runner` (e.g. `harness/config/loan-assesment.yml`) plus two
optional sections:

```yaml
eval_service:
  topic: agent-output-eval           # external-task topic to subscribe to
  judge_model: gateway:/fluxnova-judge
  lock_duration_ms: 180000           # judge calls can take 90s+, keep generous

mlflow_dataset:
  tracking_uri: http://localhost:5000   # must be the MLflow server's HTTP(S) address
                                         # for gateway-routed judge models to work
  experiment_name: fluxnova-loanAssessmentProcess   # optional override
```

## Tests

```bash
pytest tests
```
