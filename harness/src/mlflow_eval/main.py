"""MLflow GenAI evaluation for the Fluxnova loan assessment agent subprocess.

This is an MLflow-based alternative to ``deep_eval`` (see
``harness/src/deep_eval/main.py``), covering the same scenarios but using
``mlflow.genai.evaluate`` and MLflow scorers instead of DeepEval metrics.
Both harnesses can be run side by side against the same agent-history report.

Three ways to select what to evaluate (use forward slashes for paths — on
Windows Git Bash / MINGW64, backslashes are treated as escape characters and
will mangle the path):

    # A single ad-hoc agent-history JSON report file (as produced by `harness`)
    mlflow-eval harness/config/loan-assesment.yml \\
                harness/.fluxnova/loanAssessmentProcess/<id>.json

    # A single previously recorded run, read back from the MLflow dataset
    # (requires the workflow config's mlflow_dataset.enabled to have been
    # set to true when that run was originally produced by `harness`)
    mlflow-eval harness/config/loan-assesment.yml --instance-id <processInstanceId>

    # Every run currently recorded in the MLflow dataset for this process_key
    mlflow-eval harness/config/loan-assesment.yml --all

Results are written to a local MLflow tracking store (SQLite) at
``harness/.mlflow/mlflow.db`` and can be browsed with:
    mlflow ui --backend-store-uri sqlite:///harness/.mlflow/mlflow.db

A summary of the aggregate scorer metrics (MLflow's own ``EvaluationResult``
summary, not a bespoke report) is also printed to stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import mlflow
from mlflow.entities import Feedback
from mlflow.genai.scorers import scorer

from fluxnova.config import WorkflowConfig
from fluxnova.mlflow_dataset import (
    build_mlflow_record,
    dataset_name_for,
    experiment_name_for,
    get_or_create_dataset,
    tracking_uri_for,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_JUDGE_MODEL = "ollama/llama3.2"

# Tool -> BPMN camunda:inputParameter names (see bpmn/loan-assesment.bpmn),
# used to validate that each tool call received the arguments the process
# actually wires up for it.
_EXPECTED_TOOL_INPUTS: dict[str, list[str]] = {
    "Check Credit Score": ["customerId"],
    "Run Fraud Screening": ["applicationId", "customerId"],
    "Verify Employment": ["customerId"],
    "Analyse Bank Statements": ["customerId"],
    "Value Collateral": ["applicationId"],
    "Assess Affordability": ["customerId", "requestedAmount"],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tool_names(tool_calls: list[dict]) -> list[str]:
    return [tc["toolName"] for tc in tool_calls]


def _tool_input(tool_call: dict) -> dict:
    return json.loads(tool_call["toolInput"]) if tool_call.get("toolInput") else {}


def _llm_judge(criteria: str, context: dict[str, Any], model: str = _JUDGE_MODEL) -> Feedback:
    """Ask a local Ollama model to judge ``context`` against ``criteria``.

    Mirrors the intent of DeepEval's GEval metric: an LLM-as-judge returning a
    0.0-1.0 score plus a short rationale, parsed from a JSON response.
    """
    import litellm

    prompt = (
        "You are an evaluation judge. Score how well the provided data satisfies "
        "the criteria below, on a scale from 0.0 (fails completely) to 1.0 "
        "(fully satisfies).\n\n"
        f"Criteria:\n{criteria}\n\n"
        f"Data to evaluate (JSON):\n{json.dumps(context, indent=2, default=str)}\n\n"
        "Respond with ONLY a JSON object of the form "
        '{"score": <0.0-1.0>, "rationale": "<one or two sentences>"}.'
    )
    response = litellm.completion(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        format="json",
    )
    content = response["choices"][0]["message"]["content"]
    try:
        parsed = json.loads(content)
        score = float(parsed["score"])
        rationale = str(parsed.get("rationale", ""))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        score, rationale = 0.0, f"Could not parse judge response: {content!r}"
    return Feedback(value=score, rationale=rationale)


# ---------------------------------------------------------------------------
# Scorers
# ---------------------------------------------------------------------------

@scorer
def tool_correctness(inputs: dict, outputs: str) -> Feedback:
    """All core assessment tools must be called; inappropriate tools are penalised."""
    called = set(_tool_names(inputs["tool_calls"]))
    expected = set(inputs["expected_tools"])
    available = set(inputs["available_tools"])
    missing = expected - called
    unexpected = (called - expected) & available
    denom = len(expected | unexpected) or 1
    score = 1.0 - (len(missing) + len(unexpected)) / denom
    rationale = (
        f"missing={sorted(missing)!r}, unexpected={sorted(unexpected)!r}, "
        f"called={sorted(called)!r}, expected={sorted(expected)!r}"
    )
    return Feedback(value=max(0.0, score), rationale=rationale)


@scorer
def tool_argument_correctness(inputs: dict) -> Feedback:
    """Each expected tool must be invoked with the arguments the BPMN wires up for it."""
    expected_tools = inputs["expected_tools"]
    input_variables = inputs["input_variables"]
    calls_by_name: dict[str, dict] = {}
    for tc in inputs["tool_calls"]:
        calls_by_name.setdefault(tc["toolName"], tc)

    problems: list[str] = []
    for tool_name in expected_tools:
        expected_params = _EXPECTED_TOOL_INPUTS.get(tool_name)
        if not expected_params:
            continue
        call = calls_by_name.get(tool_name)
        if call is None:
            problems.append(f"{tool_name}: never called")
            continue
        actual_input = _tool_input(call)
        for param in expected_params:
            expected_value = str(input_variables.get(param))
            actual_value = str(actual_input.get(param))
            if expected_value != actual_value:
                problems.append(
                    f"{tool_name}.{param}: expected {expected_value!r}, got {actual_value!r}"
                )

    score = 1.0 if not problems else max(0.0, 1.0 - len(problems) / max(len(expected_tools), 1))
    rationale = "OK" if not problems else "; ".join(problems)
    return Feedback(value=score, rationale=rationale)


@scorer
def decision_quality(inputs: dict, outputs: str) -> Feedback:
    """Final output must contain a clear, justified APPROVE or REJECT recommendation."""
    criteria = (
        "Determine whether the agent's final output reaches a definitive APPROVE or REJECT "
        "lending recommendation backed by the evidence it gathered. Penalise vague conclusions, "
        "deferred decisions ('next steps required'), or missing justification. Reward outputs "
        "that cite specific data points (credit score, fraud risk, affordability result, "
        "employment status) and state a clear recommendation."
    )
    return _llm_judge(criteria, {"goal": inputs["agent_goal"], "final_output": outputs})


@scorer
def evidence_citation(inputs: dict, outputs: str) -> Feedback:
    """Output must explicitly reference the key financial data points gathered.

    The required evidence categories depend on applicantType: EMPLOYED applicants
    are verified via employment verification, others via bank statement analysis.
    """
    applicant_type = inputs["input_variables"].get("applicantType")
    income_evidence = (
        "employment verification result" if applicant_type == "EMPLOYED"
        else "bank statement analysis result"
    )
    criteria = (
        "Evaluate whether the agent's final output explicitly references the core financial "
        "data collected during the assessment: at minimum the fraud risk score, the "
        f"affordability outcome, and the {income_evidence}. An output that draws a conclusion "
        "without citing specific gathered evidence should score poorly."
    )
    return _llm_judge(criteria, {"goal": inputs["agent_goal"], "final_output": outputs})


@scorer
def matches_golden(outputs: str, expectations: dict) -> Feedback | None:
    """Final output must reach the same APPROVE/REJECT decision as the golden for this scenario."""
    expected_output = expectations.get("expected_output")
    if not expected_output:
        return None
    criteria = (
        "Compare the agent's actual output to the expected (golden) output. Award a high score "
        "if both reach the same APPROVE or REJECT decision AND the actual output cites at "
        "least the same categories of evidence (credit, fraud risk, affordability). Penalise "
        "if the decision differs or critical evidence cited in the golden is entirely absent "
        "from the actual output."
    )
    return _llm_judge(criteria, {"actual_output": outputs, "golden_output": expected_output})


@scorer
def no_collateral_check_when_no_collateral(inputs: dict) -> Feedback | None:
    """Value Collateral must not be called when hasCollateral is false."""
    if inputs["input_variables"].get("hasCollateral"):
        return None  # not applicable for this run
    called = "Value Collateral" in _tool_names(inputs["tool_calls"])
    return Feedback(
        value=not called,
        rationale="Value Collateral was invoked despite hasCollateral=false" if called else "OK",
    )


@scorer
def collateral_valuation_called_correctly_when_required(inputs: dict) -> Feedback | None:
    """Value Collateral must be called, with the correct applicationId, when hasCollateral is true."""
    input_variables = inputs["input_variables"]
    if not input_variables.get("hasCollateral"):
        return None  # not applicable for this run
    calls = [tc for tc in inputs["tool_calls"] if tc["toolName"] == "Value Collateral"]
    if not calls:
        return Feedback(value=0.0, rationale="Value Collateral was never called despite hasCollateral=true")
    actual_input = _tool_input(calls[0])
    expected_application_id = str(input_variables.get("applicationId"))
    actual_application_id = str(actual_input.get("applicationId"))
    if actual_application_id != expected_application_id:
        return Feedback(
            value=0.0,
            rationale=(
                f"Value Collateral applicationId mismatch: expected {expected_application_id!r}, "
                f"got {actual_application_id!r}"
            ),
        )
    return Feedback(value=1.0, rationale="OK")


@scorer
def no_bank_statement_for_employed_applicant(inputs: dict) -> Feedback | None:
    """Bank statement analysis must not be called for an employed (salaried) applicant."""
    if inputs["input_variables"].get("applicantType") != "EMPLOYED":
        return None
    called = "Analyse Bank Statements" in _tool_names(inputs["tool_calls"])
    return Feedback(
        value=not called,
        rationale="Analyse Bank Statements was invoked for an EMPLOYED applicant" if called else "OK",
    )


@scorer
def no_verify_employment_for_self_employed_applicant(inputs: dict) -> Feedback | None:
    """Employment verification must not be called for a self-employed applicant."""
    if inputs["input_variables"].get("applicantType") == "EMPLOYED":
        return None
    called = "Verify Employment" in _tool_names(inputs["tool_calls"])
    return Feedback(
        value=not called,
        rationale="Verify Employment was invoked for a non-EMPLOYED applicant" if called else "OK",
    )


@scorer
def all_tool_calls_completed(inputs: dict) -> Feedback:
    """Every tool call initiated by the agent must have reached COMPLETED status."""
    failed = [tc for tc in inputs["tool_calls"] if tc["status"] != "COMPLETED"]
    return Feedback(
        value=not failed,
        rationale=f"Tool calls did not complete: {[tc['toolName'] for tc in failed]}" if failed else "OK",
    )


@scorer
def step_efficiency(inputs: dict) -> Feedback:
    """Agent must complete its assessment within a reasonable number of loop iterations.

    The base budget is 3 LLM inference calls (fraud, credit, and either
    employment-verification or bank-statement-analysis, then affordability);
    an extra iteration is allowed when collateral valuation is also required.
    """
    max_iterations = 3 + (1 if inputs["input_variables"].get("hasCollateral") else 0)
    iterations = inputs["iterations"]
    return Feedback(
        value=iterations <= max_iterations,
        rationale=(
            "OK" if iterations <= max_iterations
            else f"Agent used {iterations} iterations; expected <= {max_iterations}"
        ),
    )


@scorer
def requested_amount_passed_to_affordability(inputs: dict) -> Feedback:
    """Affordability assessment must receive the correct customerId and requestedAmount."""
    calls = [tc for tc in inputs["tool_calls"] if tc["toolElementId"] == "ServiceTask_AffordabilityAssessment"]
    if not calls:
        return Feedback(value=0.0, rationale="Assess Affordability was never called")
    actual_input = _tool_input(calls[0])
    input_variables = inputs["input_variables"]
    problems = [
        f"{param}: expected {str(input_variables[param])!r}, got {str(actual_input.get(param))!r}"
        for param in ("customerId", "requestedAmount")
        if str(actual_input.get(param)) != str(input_variables[param])
    ]
    return Feedback(value=not problems, rationale="; ".join(problems) if problems else "OK")


@scorer
def affordability_called_after_income_confirmed(inputs: dict) -> Feedback | None:
    """Affordability must be assessed only after employment/income has been confirmed.

    The BPMN's agent prompt requires Assess Affordability to run "after income
    has been confirmed" — i.e. after Verify Employment (EMPLOYED) or Analyse
    Bank Statements (otherwise). Tool calls are assumed to be ordered
    chronologically, as produced by fluxnova.report.build_agent_report.
    """
    applicant_type = inputs["input_variables"].get("applicantType")
    income_tool = "Verify Employment" if applicant_type == "EMPLOYED" else "Analyse Bank Statements"
    names = _tool_names(inputs["tool_calls"])
    if income_tool not in names or "Assess Affordability" not in names:
        return None  # covered by tool_correctness if either is missing entirely
    income_index = names.index(income_tool)
    affordability_index = names.index("Assess Affordability")
    ok = affordability_index > income_index
    return Feedback(
        value=ok,
        rationale=(
            "OK" if ok
            else f"Assess Affordability (index {affordability_index}) ran before "
                 f"{income_tool} (index {income_index})"
        ),
    )


_SCORERS = [
    tool_correctness,
    tool_argument_correctness,
    decision_quality,
    evidence_citation,
    matches_golden,
    no_collateral_check_when_no_collateral,
    collateral_valuation_called_correctly_when_required,
    no_bank_statement_for_employed_applicant,
    no_verify_employment_for_self_employed_applicant,
    all_tool_calls_completed,
    step_efficiency,
    requested_amount_passed_to_affordability,
    affordability_called_after_income_confirmed,
]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _load_report_row(config: WorkflowConfig, report_path: Path) -> dict:
    """Build one evaluation row from an ad-hoc agent-history JSON report file."""
    agent_report = json.loads(report_path.read_text(encoding="utf-8"))
    return build_mlflow_record(config, agent_report)


def _load_instance_row(dataset_name: str, experiment_id: str, instance_id: str):
    """Fetch one previously recorded run from the MLflow dataset, by processInstanceId."""
    dataset = get_or_create_dataset(dataset_name, experiment_id)
    df = dataset.to_df()
    matches = df[df["tags"].apply(lambda t: t.get("processInstanceId") == instance_id)]
    if matches.empty:
        raise SystemExit(
            f"No record with processInstanceId={instance_id!r} found in MLflow dataset "
            f"'{dataset_name}'. Was it recorded with mlflow_dataset.enabled: true?"
        )
    return matches


def main(argv: Iterable[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Run MLflow GenAI evaluation against Fluxnova agent-history data"
    )
    parser.add_argument("config", type=Path, help="Path to the workflow YAML config file")
    parser.add_argument(
        "report",
        type=Path,
        nargs="?",
        default=None,
        help="Path to a single ad-hoc agent-history JSON report file",
    )
    parser.add_argument(
        "--instance-id",
        default=None,
        help="Evaluate one previously recorded run from the MLflow dataset, by processInstanceId",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Evaluate every run currently recorded in the MLflow dataset for this process_key",
    )
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])

    selected = [bool(args.report), bool(args.instance_id), args.all]
    if sum(selected) != 1:
        parser.error("Specify exactly one of: report file, --instance-id, or --all")

    config = WorkflowConfig.from_file(args.config)
    tracking_uri = tracking_uri_for(config, _REPO_ROOT)
    mlflow.set_tracking_uri(tracking_uri)
    experiment = mlflow.set_experiment(experiment_name_for(config))
    dataset_name = dataset_name_for(config)

    if args.report:
        data: Any = [_load_report_row(config, args.report)]
    elif args.instance_id:
        data = _load_instance_row(dataset_name, experiment.experiment_id, args.instance_id)
    else:
        data = get_or_create_dataset(dataset_name, experiment.experiment_id)

    result = mlflow.genai.evaluate(data=data, scorers=_SCORERS)

    print("\n=== MLflow evaluation summary ===")
    print(json.dumps(result.metrics, indent=2, default=str))
    print(
        "\nBrowse full results with:\n"
        f"  mlflow ui --backend-store-uri {tracking_uri}"
    )


if __name__ == "__main__":
    main()
