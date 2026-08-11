# Fluxnova AI Workflow

A test harness and evaluation framework for Fluxnova workflows that include AI-driven agentic subprocesses.

## Overview

This project provides:

1. **A Fluxnova runner** — deploys a BPMN workflow to Fluxnova, starts a process instance with configurable input
   variables, drives mock external-task workers, polls for completion, and writes an agent-history report.
2. **A DeepEval evaluation suite** — loads the agent-history report and evaluates the LLM agent's behaviour using
   configurable metrics: tool correctness, argument correctness, decision quality, evidence citation, and deterministic
   safety checks.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  harness CLI                                            │
│                                                         │
│  1. Deploy BPMN  →  Fluxnova engine                     │
│  2. Start process instance with input variables         │
│  3. Run mock external-task workers (topics → outputs)   │
│  4. Poll until process completes                        │
│  5. Fetch agent-history from subprocess endpoint        │
│  6. Write  harness/.fluxnova/<processKey>/<id>.json     │
└──────────────────────────────┬──────────────────────────┘
                               │ agent-history JSON
                               ▼
┌─────────────────────────────────────────────────────────┐
│  deep-eval CLI                                          │
│                                                         │
│  Reads config YAML + agent-history JSON                 │
│  Evaluates with DeepEval metrics (Ollama / llama3.2)    │
│  Outputs pass/fail results per test                     │
└─────────────────────────────────────────────────────────┘
```

## Prerequisites

- Python 3.11+
- A running [Fluxnova](https://fluxnova.io) engine (default: `http://localhost:8080/engine-rest`)
- [Ollama](https://ollama.com) with `llama3.2` pulled — used as the DeepEval judge model

## Installation

```bash
cd harness
python -m venv .venv
pip install -e ".[dev]"
```

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

## Evaluation tests

| Test                                            | Metric                                     | Description                                                            |
|-------------------------------------------------|--------------------------------------------|------------------------------------------------------------------------|
| `test_tool_correctness`                         | `ToolCorrectnessMetric`                    | Agent calls all expected tools and no inappropriate ones               |
| `test_tool_argument_correctness`                | `ToolCorrectnessMetric` (INPUT_PARAMETERS) | Each tool is invoked with the correct arguments from process variables |
| `test_decision_quality`                         | `GEval`                                    | Final output contains a clear, justified APPROVE/REJECT recommendation |
| `test_evidence_citation`                        | `GEval`                                    | Output cites the key financial data points gathered                    |
| `test_no_collateral_check_when_no_collateral`   | Deterministic                              | Value Collateral is not called when `hasCollateral=false`              |
| `test_no_bank_statement_for_employed_applicant` | Deterministic                              | Bank statements are not analysed for EMPLOYED applicants               |
| `test_all_tool_calls_completed`                 | Deterministic                              | Every initiated tool call reached COMPLETED status                     |
| `test_step_efficiency`                          | Deterministic                              | Agent completes within ≤ 3 loop iterations                             |
| `test_requested_amount_passed_to_affordability` | Deterministic                              | Affordability tool receives the correct `requestedAmount`              |

## Tech stack

| Component            | Technology                                      |
|----------------------|-------------------------------------------------|
| Workflow engine      | [Fluxnova](https://fluxnova.io) (Camunda-based) |
| BPMN                 | Camunda BPMN 2.0                                |
| Harness runtime      | Python 3.11, requests, PyYAML                   |
| Mock workers         | `camunda-external-task-client-python3`          |
| LLM agent            | Ollama / llama3.2 (runs inside Fluxnova)        |
| Evaluation framework | [DeepEval](https://deepeval.com)                |
| Judge model          | Ollama / llama3.2                               |
