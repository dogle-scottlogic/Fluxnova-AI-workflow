# Fluxnova AI Workflow

A test harness and evaluation framework for Fluxnova workflows that include AI-driven agentic subprocesses.

Goals of this project are:

- Create an Eval Test framework which facilitates Evaluation Driven Design (EDD). This will allow a demo of how you can
  build out an agentic workflow using EDD.
- Add an OTel collector and gen_ai metrics and tracing with a suitable dashboard. This will allow a demo of real time
  production monitoring
- Add some mechanism for guardrails which will facilitate checks on responses and break-glass to a human in Production
  environments.

## Overview

This project is split into four independent Python packages:

1. **`fluxnova-runner`** (`fluxnova-runner/`) — deploys a BPMN workflow to Fluxnova, starts a process instance with
   configurable input variables, drives mock external-task workers, and polls until the process completes. It does
   **not** produce any evaluation report itself — that's the listener's job (see below).
2. **`fluxnova-listener`** (`fluxnova-listener/`) — a standalone, long-running background service. It runs a small
   OTLP/HTTP trace receiver, polls the captured spans for completed agentic subprocess runs (matched by BPMN element id
   against a configured watch-list), and for each newly-completed run builds an agent-history report and records it into
   a persistent MLflow evaluation dataset (optionally also as a JSON file).
3. **`fluxnova-mlflow-dataset`** (`fluxnova-mlflow-dataset/`) — a small shared library (no CLI) used by both the
   listener and the harness's `mlflow-eval` suite: builds MLflow-dataset records from agent-history data and
   reads/writes the persistent SQLite-backed MLflow evaluation dataset (with skip-if-exists/upsert semantics).
4. **`harness`** (`harness/`) — the two evaluation suites:
    - **DeepEval evaluation suite** (`deep-eval`) — loads an agent-history JSON report and evaluates the LLM agent's
      behaviour using configurable metrics: tool correctness, argument correctness, decision quality, evidence citation,
      and deterministic safety checks.
    - **MLflow evaluation suite** (`mlflow-eval`) — an alternative to the DeepEval suite above, evaluating agent-history
      data with `mlflow.genai.evaluate` scorers instead, with results browsable in the MLflow UI. It can evaluate a
      single ad-hoc report file, a single previously recorded run (by process instance ID, read from the MLflow dataset
      the listener populates), or every recorded run at once.

   Both evaluation suites can be run independently; neither depends on the other, and neither depends on the runner or
   listener being active at evaluation time (only on the report file / dataset records they've already produced).

## Architecture

```
┌───────────────────────────┐        ┌──────────────────────────────────────────────┐
│  fluxnova-runner CLI      │        │  fluxnova-listener service (long-running)     │
│  (fluxnova-run)           │        │  (fluxnova-listener)                          │
│                           │        │                                                │
│  1. Deploy BPMN           │        │  1. Run local OTLP/HTTP trace receiver         │
│     → Fluxnova engine     │        │     (POST /v1/traces) fed by the OTel          │
│  2. Start process         │        │     Collector                                  │
│     instance with input   │        │  2. Poll captured spans for invoke_agent runs  │
│     variables             │        │     matching a configured watch-list of BPMN   │
│  3. Run mock external-    │        │     subprocess element ids (presence of a span │
│     task workers          ├───────▶│     IS the "completed" signal)                 │
│  4. Poll until process    │  OTel  │  3. Build an agent-history report (BPMN +      │
│     completes             │ traces │     OTLP + core API) for each new run          │
│                           │        │  4. Upsert into the MLflow evaluation dataset  │
│                           │        │     (skip if already recorded)                 │
│                           │        │  5. Optionally also write a JSON report file   │
└───────────────────────────┘        └───────────────────────┬────────────────────────┘
                                                              │ MLflow dataset record
                                                              │ and/or JSON report
                                                              ▼
┌─────────────────────────────────────────────────────────────────────────────────────┐
│  deep-eval CLI                              │  mlflow-eval CLI                       │
│                                              │                                        │
│  Reads config YAML + agent-history JSON     │  Reads config YAML + agent-history JSON │
│  Evaluates with DeepEval metrics            │  OR reads an existing MLflow dataset    │
│  (Ollama / llama3.2)                        │  record by --instance-id / --all        │
│  Outputs pass/fail per test                 │  Evaluates with MLflow scorers          │
│  + terminal summary + HTML report           │  (Ollama / llama3.2)                    │
│                                              │  Outputs metrics summary; browse in the │
│                                              │  MLflow UI                              │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

## Prerequisites

- Python 3.11+
- A running [Fluxnova](https://fluxnova.io) engine (default: `http://localhost:8080/engine-rest`)
- [Ollama](https://ollama.com) with `llama3.2` pulled — used as the judge model for both the DeepEval and MLflow
  evaluation suites

## Installation

All four packages share a single virtual environment (created under `harness/.venv`) and are installed editable, so
changes to any package take effect immediately without reinstalling:

```bash
cd harness
python -m venv .venv
source .venv/Scripts/activate   # Git Bash on Windows; use .venv\Scripts\Activate.ps1 in PowerShell

pip install -e ".[dev]"
pip install -e ../fluxnova-mlflow-dataset[dev]
pip install -e ../fluxnova-listener[dev]
pip install -e ../fluxnova-runner[dev]
```

This installs four console-script entry points into `harness/.venv`: `fluxnova-run` / `fluxnova-run-mock-workers`
(runner), `fluxnova-listener` (listener), and `deep-eval` / `mlflow-eval` (harness evaluation suites).

## A note on shells (Windows)

All commands below use forward slashes (`/`) for paths, which work correctly in both **Git Bash / WSL** and
**PowerShell**. If you use backslashes (`\`) in **Git Bash / MINGW64**, they are treated as escape characters and will
silently mangle the path (e.g. `config\loan-assesment.yml` becomes `configloan-assesment.yml`). Backslashes are safe to
use in native **PowerShell** or `cmd.exe`, but forward slashes work everywhere, so they're used consistently in this
README.

## Running the harness

```bash
harness config/loan-assesment.yml --with-mock-workers
```

This deploys the BPMN, starts the process, runs mock workers in the background, waits for completion, and writes the
agent-history report to `harness/.fluxnova/<processKey>/<instanceId>.json`.

## Running the evaluations

```bash
# Via the entry point (recommended — generates both the DeepEval score summary and an HTML report)
deep-eval config/loan-assesment.yml \
          .fluxnova/loanAssessmentProcess/<instanceId>.json
```

Two reports are written on every run (pass or fail):

| Report                 | Location                                        |
|------------------------|-------------------------------------------------|
| DeepEval score summary | printed to terminal at end of session           |
| HTML report            | `.fluxnova/<processKey>/<instanceId>-eval.html` |

```bash
# Via the deepeval CLI directly (also generates the DeepEval score summary)
deepeval test run src/deep_eval/main.py \
    --config config/loan-assesment.yml \
    --report .fluxnova/loanAssessmentProcess/<instanceId>.json

# Via pytest (HTML report only — no DeepEval score summary)
pytest src/deep_eval/main.py -v \
    --config config/loan-assesment.yml \
    --report .fluxnova/loanAssessmentProcess/<instanceId>.json \
    --html .fluxnova/loanAssessmentProcess/<instanceId>-eval.html \
    --self-contained-html
```

## Running the evaluations (MLflow, alternative)

`mlflow-eval` is a drop-in alternative to `deep-eval` — same config YAML, same underlying Ollama/llama3.2 judge model,
but scored with `mlflow.genai.evaluate` scorers instead of DeepEval metrics. It supports three ways to select what to
evaluate:

```bash
# 1. A single ad-hoc agent-history JSON report file (same as deep-eval)
mlflow-eval config/loan-assesment.yml \
            .fluxnova/loanAssessmentProcess/<instanceId>.json

# 2. A single previously recorded run, read back from the MLflow dataset by process instance ID
#    (requires mlflow_dataset.enabled: true in the config when that run was originally produced — see below)
mlflow-eval config/loan-assesment.yml --instance-id <instanceId>

# 3. Every run currently recorded in the MLflow dataset for this process_key
mlflow-eval config/loan-assesment.yml --all
```

Each of these prints an aggregate metrics summary to the terminal (MLflow's own `EvaluationResult.metrics` — there is no
bespoke report file) and writes full per-scorer results (scores, rationales, inputs/outputs) to a local SQLite tracking
store at `harness/.mlflow/mlflow.db`.

To view the results in the browser, start the MLflow UI pointed at that store:

```bash
mlflow ui --backend-store-uri sqlite:///harness/.mlflow/mlflow.db --port 5000
```

Then open [http://localhost:5000](http://localhost:5000) and open the `fluxnova-loanAssessmentProcess` experiment. Each
`mlflow-eval` run appears as a row; click into one to see per-scorer scores and rationales. Running
`mlflow-eval` again against other reports/instances adds further rows to the same experiment for side-by-side
comparison.

### Recording runs into the MLflow dataset (`mlflow_dataset` config)

By default, `harness` only writes the JSON agent-history report file (as before). To also (or instead) record each
completed run as an entry in a persistent MLflow evaluation dataset — so it can later be evaluated with
`--instance-id`/`--all` above, without needing to keep the JSON file around — add an `mlflow_dataset` block to the
workflow config:

```yaml
mlflow_dataset:
  enabled: true                                      # turn on recording (default: false)
  name: loan-assessment-runs                          # optional; defaults to "fluxnova-<process_key>"
  tracking_uri: sqlite:///harness/.mlflow/mlflow.db    # optional; defaults to the same store mlflow-eval uses
  also_write_json_report: true                         # false = record to MLflow only, skip the JSON file
```

`harness` prints the dataset name and record ID (and the exact `mlflow-eval --instance-id ...` command to re-run)
after each completed process instance. Records are shaped identically whether they come from a fresh `harness` run or an
ad-hoc JSON report file, via the shared `fluxnova.mlflow_dataset` module, so results are consistent regardless of which
evaluation mode you use.

## OTel observability (`OtelClient`)

Alongside the `/agent-history` REST-based flow above, the harness can also read GenAI run data (iteration/tool-call
counts, tool spans, and — once content capture ships — LLM messages) straight out of the OTLP trace stream emitted by
the `agentic-subprocess` plugin. This is **backend-agnostic**: it doesn't depend on whichever vendor (MLflow, Tempo,
Jaeger, Datadog, ...) your OTel Collector happens to be configured to export traces to for visualisation — it reads the
raw OTLP wire format via a small local receiver the harness runs itself. (Note: this is unrelated to the `mlflow-eval`
evaluation suite above — MLflow here refers to a possible OTel trace-visualisation backend, not the GenAI evaluation
tracking store.) See `harness/docs/deepeval-otel-gap-analysis.md` for the full rationale and
`fluxnova-plugins/agentic-subprocess/docs/observability/GENAI_SEMCONV_ALIGNMENT.md` for the exact span/attribute shapes.

### 1. Add a second exporter to your OTel Collector config

Find your Collector's `config.yaml` (e.g. on Windows, if installed as a service:
`C:\Program Files (x86)\OpenTelemetry Collector\config.yaml`) and add an
`otlphttp/harness` exporter alongside whatever trace exporter (s) you already have (e.g. `otlphttp/mlflow`), then add it
to the `traces` pipeline's `exporters` list — it's additive, so existing exporters keep working unchanged:

```yaml
exporters:
  otlphttp/harness:
    # Points at the harness's own local OTLP trace receiver (fluxnova.otel_receiver),
    # not a visualisation backend.
    traces_endpoint: http://localhost:4319/v1/traces
    tls:
      insecure: true

service:
  pipelines:
    traces:
      receivers: [ otlp, jaeger, zipkin ]
      processors: [ batch ]
      exporters: [ debug, otlphttp/mlflow, otlphttp/harness ]
```

Restart the Collector service after saving (e.g. `Restart-Service "OpenTelemetry Collector"`
from an admin PowerShell — editing `config.yaml` itself also requires admin rights).

### 2. Run the harness's local OTLP receiver

```bash
cd harness
otel-receiver --port 4319 --store harness/.fluxnova/otel-spans.json
```

Leave it running. It accepts `POST /v1/traces` OTLP/HTTP exports and appends each span as one JSON line to the store
file.

### 3. Drive a process run

Run the harness (or trigger the workflow however you normally do) so the plugin emits spans through the Collector to the
receiver.

### 4. Verify spans landed

```bash
Get-Content harness/.fluxnova/otel-spans.json -Tail 5
```

### 5. Query via `OtelClient`

```python
from fluxnova.otel_client import OtelClient

client = OtelClient()  # defaults to harness/.fluxnova/otel-spans.json
correlation_id = "<gen_ai.conversation.id, i.e. the process instance id>"

client.get_invoke_agent_metrics(correlation_id)  # agent name, model, tokens, inference/tool-call counts, duration
client.get_tool_call_spans(correlation_id)  # one entry per execute_tool span
```

## Config file

All harness and evaluation behaviour is driven by a single YAML file.

```yaml
fluxnova_url: http://localhost:8080/engine-rest
bpmn_path: bpmn/loan-assesment.bpmn
process_key: loanAssessmentProcess
subprocess_id: AdHocSubProcess_LoanAssessmentAgent
deployment_name: Loan Assessment

# Initial process variables passed to the workflow
variables:
  applicantType: EMPLOYED
  hasCollateral: false
  requestedAmount: 50000
  # ...

# Mock external-task workers: topic → output variables
mock_workers:
  credit-score-check:
    creditScore: 720
  # ...

# Tools available to the agent (BPMN element ID → display name)
available_tools:
  ServiceTask_CreditScoreCheck: Check Credit Score
  # ...

# Tools the agent is expected to call — GitLab CI-style rule list.
# Rules are evaluated top-to-bottom against the run's inputVariables.
# A rule without "if" always applies. Supported operators: == and !=
expected_tools:
  - tool: Check Credit Score
  - tool: Run Fraud Screening
  - tool: Assess Affordability
  - if: '$applicantType == "EMPLOYED"'
    tool: Verify Employment
  - if: '$applicantType != "EMPLOYED"'
    tool: Analyse Bank Statements
  - if: '$hasCollateral == true'
    tool: Value Collateral

# Optional: also (or instead) record each completed run into a persistent
# MLflow evaluation dataset — see "Recording runs into the MLflow dataset" above.
mlflow_dataset:
  enabled: false
  # name: loan-assessment-runs
  # tracking_uri: sqlite:///harness/.mlflow/mlflow.db
  # also_write_json_report: true
```

### `expected_tools` rule syntax

Each entry in `expected_tools` is a rule with:

| Key    | Required | Description                                                       |
|--------|----------|-------------------------------------------------------------------|
| `tool` | Yes      | Display name of the tool (must match `available_tools` values)    |
| `if`   | No       | Condition string — omit to make the tool unconditionally expected |

**Condition syntax:** `$variableName <op> <value>`

- `$variable` is resolved from `inputVariables` in the agent-history report at evaluation time
- Operators: `==` (equals), `!=` (not equals)
- Values: `"quoted string"`, `true`, `false`, or an integer

Examples:

```yaml
- if: '$applicantType == "EMPLOYED"'   # string comparison
- if: '$hasCollateral == true'          # boolean
- if: '$loanAmount != 0'               # integer
```

## Agent-history report format

The harness fetches this from the Fluxnova subprocess history endpoint and writes it as JSON:

```json
{
  "goal": "Perform a full loan risk assessment for applicant ...",
  "finalOutput": "APPROVE — the applicant meets all lending criteria ...",
  "iterations": 2,
  "inputVariables": {
    "applicantType": "EMPLOYED",
    "hasCollateral": false
  },
  "toolCalls": [
    {
      "toolName": "Check Credit Score",
      "toolElementId": "ServiceTask_CreditScoreCheck",
      "toolInput": "{\"customerId\": \"C001\"}",
      "status": "COMPLETED"
    }
  ],
  "promptMessages": [
    ...
  ],
  "executionTime": 4200
}
```

## Evaluation tests (DeepEval)

| Test                                            | Metric                                     | Description                                                            |
|-------------------------------------------------|--------------------------------------------|------------------------------------------------------------------------|
| `test_tool_correctness`                         | `ToolCorrectnessMetric`                    | Agent calls all expected tools and no inappropriate ones               |
| `test_tool_argument_correctness`                | `ToolCorrectnessMetric` (INPUT_PARAMETERS) | Each tool is invoked with the correct arguments from process variables |
| `test_decision_quality`                         | `GEval`                                    | Final output contains a clear, justified APPROVE/REJECT recommendation |
| `test_evidence_citation`                        | `GEval`                                    | Output cites the key financial data points gathered                    |
| `test_output_matches_golden`                    | `GEval`                                    | Output reaches the same decision as the matching golden scenario       |
| `test_no_collateral_check_when_no_collateral`   | Deterministic                              | Value Collateral is not called when `hasCollateral=false`              |
| `test_no_bank_statement_for_employed_applicant` | Deterministic                              | Bank statements are not analysed for EMPLOYED applicants               |
| `test_all_tool_calls_completed`                 | Deterministic                              | Every initiated tool call reached COMPLETED status                     |
| `test_step_efficiency`                          | Deterministic                              | Agent completes within ≤ 3 loop iterations                             |
| `test_requested_amount_passed_to_affordability` | Deterministic                              | Affordability tool receives the correct `requestedAmount`              |

## Evaluation scorers (MLflow)

`harness/src/mlflow_eval/main.py` covers the same scenarios as the DeepEval suite above, plus a few extra checks added
after reviewing the BPMN's agent prompt and tool wiring:

| Scorer                                                | Type       | Description                                                                     |
|-------------------------------------------------------|------------|---------------------------------------------------------------------------------|
| `tool_correctness`                                    | Code       | Agent calls all expected tools and no inappropriate ones                        |
| `tool_argument_correctness`                           | Code       | Each expected tool receives the arguments the BPMN wires up for it              |
| `decision_quality`                                    | LLM judge  | Final output contains a clear, justified APPROVE/REJECT recommendation          |
| `evidence_citation`                                   | LLM judge  | Output cites fraud/affordability/income evidence (branches on `applicantType`)  |
| `matches_golden`                                      | LLM judge  | Output reaches the same decision as the matching golden scenario                |
| `no_collateral_check_when_no_collateral`              | Code       | Value Collateral is not called when `hasCollateral=false`                       |
| `collateral_valuation_called_correctly_when_required` | Code (new) | Value Collateral **is** called, with the correct `applicationId`, when required |
| `no_bank_statement_for_employed_applicant`            | Code       | Bank statements are not analysed for EMPLOYED applicants                        |
| `no_verify_employment_for_self_employed_applicant`    | Code (new) | Employment is not verified for non-EMPLOYED applicants                          |
| `all_tool_calls_completed`                            | Code       | Every initiated tool call reached COMPLETED status                              |
| `step_efficiency`                                     | Code       | Agent completes within a budget of loop iterations (+1 if collateral required)  |
| `requested_amount_passed_to_affordability`            | Code       | Affordability tool receives the correct `customerId` and `requestedAmount`      |
| `affordability_called_after_income_confirmed`         | Code (new) | Affordability is assessed only after income has been confirmed                  |

## Tech stack

| Component            | Technology                                                                                                                       |
|----------------------|----------------------------------------------------------------------------------------------------------------------------------|
| Workflow engine      | [Fluxnova](https://fluxnova.io) (Camunda-based)                                                                                  |
| BPMN                 | Camunda BPMN 2.0                                                                                                                 |
| Harness runtime      | Python 3.11, requests, PyYAML                                                                                                    |
| Mock workers         | `camunda-external-task-client-python3`                                                                                           |
| LLM agent            | Ollama / llama3.2 (runs inside Fluxnova)                                                                                         |
| Evaluation framework | [DeepEval](https://deepeval.com) or [MLflow GenAI evaluation](https://mlflow.org/docs/latest/genai/eval-monitor/) (alternatives) |
| Judge model          | Ollama / llama3.2                                                                                                                |
