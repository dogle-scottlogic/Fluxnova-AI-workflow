# Fluxnova AI Workflow

A test harness and evaluation framework for Fluxnova workflows that include AI-driven agentic subprocesses.

Goals of this project are:

- Create an Eval Test framework which facilitates Evaluation Driven Design (EDD). This will allow a demo of how you can
  build out an agentic workflow using EDD.
- Add an OTel collector and gen_ai metrics and tracing with a suitable dashboard. This will allow a demo of real time
  production monitoring
- Add Eval to production level process runs which will facilitate checks on responses and break-glass to a human in
  Production environments.

## Overview

This project is split into three independent Python packages:

1. **`fluxnova-runner`** (`fluxnova-runner/`) — deploys a BPMN workflow to Fluxnova, starts a process instance with
   configurable input variables, drives mock external-task workers, and polls until the process completes. It does
   **not** produce any evaluation report itself.
2. **`fluxnova-mlflow-dataset`** (`fluxnova-mlflow-dataset/`) — a small shared library (no CLI) used by the harness's
   `mlflow-eval` suite. It provides:
    - **collection** (`collect_new_runs`) — an on-demand pre-step (run via `mlflow-eval --collect`, not a background
      service) that finds newly-completed agentic subprocess runs directly in MLflow's own trace store (populated by an
      OTel Collector exporting straight to MLflow), builds an agent-history report by joining that trace data with the
      static BPMN definition and the run's final process variables (read from Fluxnova's core REST API), and upserts it
      into a persistent MLflow evaluation dataset (skip-if-exists/idempotent).
    - **record shaping/read/write** — builds MLflow-dataset records from agent-history data and reads/writes the
      persistent SQLite-backed MLflow evaluation dataset.
3. **`harness`** (`harness/`) — the two evaluation suites:
    - **MLflow evaluation suite** (`mlflow-eval`) — the primary evaluation suite, evaluating agent-history data with
      `mlflow.genai.evaluate` scorers, with results browsable in the MLflow UI. It can evaluate a single ad-hoc report
      file, a single previously recorded run (by process instance ID, read from the MLflow dataset), or every recorded
      run at once — optionally collecting newly-completed runs first (`--collect`).
    - **DeepEval evaluation suite** (`deep-eval`) — *deprecated*. Loads an agent-history JSON report and evaluates the
      LLM agent's behaviour using configurable metrics: tool correctness, argument correctness, decision quality,
      evidence citation, and deterministic safety checks. Both evaluation suites can be run independently; neither
      depends on the other, and neither depends on the runner being active at evaluation time (only on the report file /
      trace data / dataset records already produced).

## Architecture

```
┌───────────────────────────┐        ┌──────────────────────────────────────┐
│  fluxnova-runner CLI      │        │  Agent's OTel instrumentation        │
│  (fluxnova-run)           │        │                                      │
│                           │        │  Emits gen_ai.* spans (invoke_agent, │
│  1. Deploy BPMN           │        │  execute_tool, chat) via OTLP        │
│     → Fluxnova engine     │        └───────────────────┬──────────────────┘
│  2. Start process         │                            │ OTLP
│     instance with input   │                            ▼
│     variables             │                 ┌────────────────────┐
│  3. Run mock external-    │                 │   OTel Collector   │
│     task workers          │                 └──────────┬─────────┘
│  4. Poll until process    │                            │ otlphttp exporter
│     completes             │                            ▼
│                           │              ┌────────────────────────────┐
└───────────────────────────┘              │  MLflow  /v1/traces        │
                                           │  (trace store)             │
                                           └──────────────┬─────────────┘
                                                          │ mlflow.search_traces()
                                                          ▼
┌─────────────────────────────────────────────────────────────────────────────────────┐
│  mlflow-eval CLI  --collect  (on-demand pre-step; no background service)            │
│                                                                                     │
│  1. Find newly-completed invoke_agent traces for the configured subprocess_id       │
│  2. Join with BPMN (system prompt, tool input params) + Fluxnova core API           │
│     (final process variables, via /history/variable-instance)                       │
│  3. Upsert an agent-history record into the persistent MLflow evaluation dataset    │
│     (skip if already recorded)                                                      │
└─────────────────────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────────────────────────┐
│  deep-eval CLI (deprecated)                  │  mlflow-eval CLI                        │
│                                              │                                         │
│  Reads config YAML + agent-history JSON      │  Reads config YAML + agent-history JSON │
│  Evaluates with DeepEval metrics             │  OR reads an existing MLflow dataset    │
│  (Ollama / llama3.2)                         │  record by --instance-id / --all        │
│  Outputs pass/fail per test                  │  Evaluates with MLflow scorers          │
│  + terminal summary + HTML report            │  (Ollama / llama3.2)                    │
│                                              │  Outputs metrics summary; browse in the │
│                                              │  MLflow UI                              │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

## Prerequisites

- Python 3.11+
- A running [Fluxnova](https://fluxnova.io) engine (default: `http://localhost:8080/engine-rest`)
- A running **MLflow tracking server** (not just the `mlflow` CLI) on `http://localhost:5000`, backed by the same SQLite
  store the harness uses — see "Running the MLflow tracking server" below. It must be running *before* you run the
  workflow, since the OTel Collector posts traces to it in real time.
- An OTel Collector configured with an `otlphttp` exporter pointing at MLflow's `/v1/traces` endpoint — required for
  `mlflow-eval --collect` to find completed runs
- [Ollama](https://ollama.com) with `llama3.2` pulled — used as the judge model for both the DeepEval and MLflow
  evaluation suites

## Installation

All three packages share a single virtual environment (created under `harness/.venv`) and are installed editable, so
changes to any package take effect immediately without reinstalling:

```bash
cd harness
python -m venv .venv
source .venv/Scripts/activate   # Git Bash on Windows; use .venv\Scripts\Activate.ps1 in PowerShell

pip install -e ".[dev]"
pip install -e ../fluxnova-mlflow-dataset[dev]
pip install -e ../fluxnova-runner[dev]
```

This installs three console-script entry points into `harness/.venv`: `fluxnova-run` / `fluxnova-run-mock-workers`
(runner), and `deep-eval` / `mlflow-eval` (harness evaluation suites).

## A note on shells (Windows)

All commands below use forward slashes (`/`) for paths, which work correctly in both **Git Bash / WSL** and
**PowerShell**. If you use backslashes (`\`) in **Git Bash / MINGW64**, they are treated as escape characters and will
silently mangle the path (e.g. `config\loan-assesment.yml` becomes `configloan-assesment.yml`). Backslashes are safe to
use in native **PowerShell** or `cmd.exe`, but forward slashes work everywhere, so they're used consistently in this
README.

## Running `fluxnova-runner` (Windows Git Bash)

From a Git Bash terminal, with `harness/.venv` activated (`source .venv/Scripts/activate` — see Installation above)
and your working directory at `harness/`:

```bash
fluxnova-run config/loan-assesment.yml --with-mock-workers
```

This deploys the BPMN, starts the process instance with the config's `variables`, runs mock external-task workers in the
background to complete each service task, and polls until the process finishes. It prints the **process instance ID** to
the terminal, e.g.:

```
Deploying bpmn/loan-assesment.bpmn …
  Deployed 'Loan Assessment' (id=...)
Starting process 'loanAssessmentProcess' …
  Instance ID: 3816567e-9f94-11f1-9ead-94b609a26547
Waiting for process to complete …
Process 3816567e-9f94-11f1-9ead-94b609a26547 completed.
Run 'mlflow-eval <config> --collect' to record the completed subprocess run into the MLflow evaluation dataset.
```

`fluxnova-runner` **does not** write an agent-history report file itself (that responsibility moved to
`fluxnova-mlflow-dataset`/`mlflow-eval`, per the Architecture diagram above) — copy the printed instance ID and use it
in the next step.

Other flags:

```bash
fluxnova-run --skip-deploy config/loan-assesment.yml   # BPMN already deployed
fluxnova-run config/loan-assesment.yml                 # no mock workers (needs real service-task workers/backends)
```

### Then evaluate that run with `mlflow-eval`

Make sure the MLflow tracking server is running first (see "Running the MLflow tracking server" below) and the OTel
Collector is exporting to it, then, still from `harness/`:

```bash
mlflow-eval config/loan-assesment.yml --collect --instance-id 3816567e-9f94-11f1-9ead-94b609a26547
```

`--collect` pulls that instance's completed trace out of MLflow's trace store, joins it with the BPMN and Fluxnova's
process-variable history, and records it into the persistent MLflow evaluation dataset; `--instance-id` then immediately
evaluates just that one record and prints the scorer summary to the terminal. See "Running the evaluations (MLflow,
primary)" and "Collecting completed runs" below for the other modes (`--all`, `--collect` alone).

## Running the evaluations (MLflow, primary)

`mlflow-eval` is the primary evaluation suite (`deep-eval` is deprecated) — same config YAML, same underlying
Ollama/llama3.2 judge model, but scored with `mlflow.genai.evaluate` scorers. It supports:

```bash
# 1. A single previously recorded run, read back from the MLflow dataset by process instance ID
mlflow-eval config/loan-assesment.yml --instance-id <instanceId>

# 2. Every run currently recorded in the MLflow dataset for this process_key
mlflow-eval config/loan-assesment.yml --all

# 3. --collect: pull newly-completed runs out of MLflow's trace store first (see below),
#    then evaluate. Can also be used on its own, with no evaluation selector, to only collect.
mlflow-eval config/loan-assesment.yml --collect --all
mlflow-eval config/loan-assesment.yml --collect
```

`mlflow-eval` only evaluates runs already recorded in the persistent MLflow dataset — there is no ad-hoc
agent-history-report-file mode. Use `--collect` first (or on its own) to populate the dataset from MLflow's trace store
before evaluating.

Each of these prints an aggregate metrics summary to the terminal (MLflow's own `EvaluationResult.metrics` — there is no
bespoke report file) and writes full per-scorer results (scores, rationales, inputs/outputs) to a local SQLite tracking
store at `harness/.mlflow/mlflow.db`.

To view the results in the browser, use the same MLflow tracking server described in "Running the MLflow tracking
server" below (or, if you only need read-only browsing and aren't using `--collect`/OTel ingestion, the lighter-weight
`mlflow ui --backend-store-uri sqlite:///harness/.mlflow/mlflow.db --port 5000` works too).

Then open [http://localhost:5000](http://localhost:5000) and open the `fluxnova-loanAssessmentProcess` experiment. Each
`mlflow-eval` run appears as a row; click into one to see per-scorer scores and rationales. Running
`mlflow-eval` again against other instances adds further rows to the same experiment for side-by-side comparison.

### Collecting completed runs (`mlflow-eval --collect`)

An OTel Collector exports traces straight to MLflow's own `/v1/traces` endpoint, so MLflow is the trace store.
`mlflow-eval --collect` is a lightweight, on-demand pre-step run each time you want fresh data:

1. Finds every `invoke_agent` trace for the config's `subprocess_id` in MLflow's trace store that isn't already recorded
   (idempotent — skips runs already present, matched by `processInstanceId`).
2. For each one, builds an agent-history report by joining that trace data with the static BPMN definition (system
   prompt, tool input params) and the run's final process variables (read from Fluxnova's
   `/history/variable-instance` API, which stays queryable long after the instance ends).
3. Upserts the result into the persistent MLflow evaluation dataset (`fluxnova-<process_key>` by default).

```bash
mlflow-eval config/loan-assesment.yml --collect
```

```
--collect: processInstanceId=... -> recorded (record_id=...)
```

You still need the OTel Collector configured to export directly to MLflow — add an `otlphttp` exporter pointing at
MLflow's `/v1/traces` endpoint and restart the Collector. `--collect` requires `subprocess_id` to be set in the workflow
config; the `mlflow_dataset.name`/`tracking_uri` config block (if present) is still honoured for where records get
written.

## Running the MLflow tracking server

`mlflow-eval` can *read* the SQLite store directly (`sqlite:///harness/.mlflow/mlflow.db`), but for the OTel Collector
to *write* traces into it, an actual MLflow tracking **server** (an HTTP process, not just the library) must be running
and listening on the URL the Collector's `otlphttp` exporter targets (`http://localhost:5000/v1/traces` by default).
`mlflow ui` alone does **not** accept incoming trace writes — it only reads.

Start it (from the repo root, with `harness/.venv` activated) and leave it running in its own terminal for as long as
you want traces to be captured:

```bash
cd harness
.venv/Scripts/mlflow server --backend-store-uri sqlite:///.mlflow/mlflow.db --host 127.0.0.1 --port 5000
```

This single server also serves the browsable UI at [http://localhost:5000](http://localhost:5000), so once it's running
you no longer need a separate `mlflow ui` process pointed at the same store.

### As an IntelliJ Run/Debug Configuration

1. **Run → Edit Configurations… → + → Python** (requires the Python plugin).
2. **Name:** `MLflow Tracking Server`
3. **Run** (instead of "Script path"): choose **Module name** and set it to `mlflow`, *or* set "Script path" to
   `harness/.venv/Scripts/mlflow.exe` (Windows) / `harness/.venv/bin/mlflow` (macOS/Linux).
4. **Parameters:**
   ```
   server --backend-store-uri sqlite:///.mlflow/mlflow.db --host 127.0.0.1 --port 5000
   ```
5. **Working directory:** `<repo root>/harness`
6. **Python interpreter:** the `harness/.venv` interpreter (add it via **Add Interpreter → Existing** if not already
   registered).
7. Apply, then run this configuration before starting the OTel Collector or running `fluxnova-run` — leave it running in
   the background for the duration of your session.

## Local development with Podman (Fluxnova + OTel Collector + MLflow)

See [`local-dev/README.md`](local-dev/README.md) for running the Fluxnova engine, the OTel Collector, and (optionally)
the MLflow tracking server as containers for local development with Podman (or Docker) — including prerequisites, a
Compose-based option and a Podman-pod alternative, an optional MLflow pod, and troubleshooting notes.

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

## Running the evaluations (DeepEval, deprecated)

> `deep-eval` requires an agent-history JSON report file as input. Since `fluxnova-runner` no longer produces one
> itself (see above), use `mlflow-eval` instead (below) unless you already have a report file from another source.

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