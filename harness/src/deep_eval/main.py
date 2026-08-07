"""
DeepEval tests for the Fluxnova loan assessment agent subprocess.

Loads the fixed agent-history JSON produced by the harness and evaluates
the agent's tool usage and final output using metrics appropriate for an
agentic BPMN subprocess.

Run with:
    pytest src/deep_eval/main.py -v
  or via the deepeval runner:
    deepeval test run src/deep_eval/main.py
"""

from __future__ import annotations

import json
from pathlib import Path

from deepeval import assert_test
from deepeval.metrics import GEval, ToolCorrectnessMetric
from deepeval.models import OllamaModel
from deepeval.test_case import LLMTestCase, SingleTurnParams, ToolCall

# ---------------------------------------------------------------------------
# Load fixture
# ---------------------------------------------------------------------------

_REPORT_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / ".fluxnova"
    / "loanAssessmentProcess"
    / "5abaa25b-9557-11f1-93e8-94b609a26547.json"
)

with _REPORT_PATH.open(encoding="utf-8") as _f:
    _REPORT: dict = json.load(_f)

# The report currently duplicates every entry — deduplicate on toolCallId.
_seen_ids: set[str] = set()
TOOL_CALLS: list[dict] = []
for _tc in _REPORT["toolCalls"]:
    if _tc["toolCallId"] not in _seen_ids:
        _seen_ids.add(_tc["toolCallId"])
        TOOL_CALLS.append(_tc)

GOAL: str = _REPORT["goal"]
FINAL_OUTPUT: str = _REPORT["finalOutput"]
ITERATIONS: int = _REPORT["iterations"]

# ---------------------------------------------------------------------------
# Shared objects
# ---------------------------------------------------------------------------

ollama = OllamaModel(model="llama3.2")

# All tools exposed inside the ad-hoc subprocess (from the BPMN definition)
_AVAILABLE_TOOLS = [
    ToolCall(name="Check Credit Score"),
    ToolCall(name="Verify Employment"),
    ToolCall(name="Analyse Bank Statements"),
    ToolCall(name="Run Fraud Screening"),
    ToolCall(name="Value Collateral"),
    ToolCall(name="Assess Affordability"),
]

# Tools the agent actually invoked (from the deduplicated run report)
_TOOLS_CALLED = [ToolCall(name=tc["toolName"]) for tc in TOOL_CALLS]

# Minimum set expected for an EMPLOYED applicant with no collateral:
#   - bank-statement analysis applies to self-employed / variable-income applicants only
#   - collateral valuation is only relevant when hasCollateral=true
_EXPECTED_TOOLS = [
    ToolCall(name="Check Credit Score"),
    ToolCall(name="Verify Employment"),
    ToolCall(name="Run Fraud Screening"),
    ToolCall(name="Assess Affordability"),
]

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_tool_correctness():
    """All core assessment tools must be called; unnecessary tools are penalised."""
    metric = ToolCorrectnessMetric(
        threshold=0.7,
        model=ollama,
        available_tools=_AVAILABLE_TOOLS,
    )
    test_case = LLMTestCase(
        input=GOAL,
        actual_output=FINAL_OUTPUT,
        tools_called=_TOOLS_CALLED,
        expected_tools=_EXPECTED_TOOLS,
    )
    assert_test(test_case, [metric])


def test_decision_quality():
    """Final output must contain a clear, justified APPROVE or REJECT recommendation."""
    metric = GEval(
        name="Decision Quality",
        criteria=(
            "Determine whether the agent's final output reaches a definitive APPROVE or REJECT "
            "lending recommendation backed by the evidence it gathered. "
            "Penalise vague conclusions, deferred decisions ('next steps required'), or missing "
            "justification. Reward outputs that cite specific data points (credit score, fraud "
            "risk, affordability result, employment status) and state a clear recommendation."
        ),
        evaluation_params=[SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT],
        threshold=0.6,
        model=ollama,
    )
    test_case = LLMTestCase(input=GOAL, actual_output=FINAL_OUTPUT)
    assert_test(test_case, [metric])


def test_evidence_citation():
    """Agent output must explicitly reference the key financial data points it gathered."""
    metric = GEval(
        name="Evidence Citation",
        criteria=(
            "Evaluate whether the agent's final output explicitly references the core financial data "
            "collected during the assessment: at minimum the fraud risk score, affordability outcome, "
            "and employment verification result. An output that draws a conclusion without citing "
            "specific gathered evidence should score poorly."
        ),
        evaluation_params=[SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT],
        threshold=0.6,
        model=ollama,
    )
    test_case = LLMTestCase(input=GOAL, actual_output=FINAL_OUTPUT)
    assert_test(test_case, [metric])


def test_bank_statement_not_called_for_employed_applicant():
    """Bank statement analysis must not be called for an employed (salaried) applicant."""
    tool_names = [tc["toolName"] for tc in TOOL_CALLS]
    assert "Analyse Bank Statements" not in tool_names, (
        "Bank statement analysis was invoked for an employed applicant — "
        "it is only appropriate for self-employed or variable-income applicants."
    )


def test_all_tool_calls_completed():
    """Every tool call initiated by the agent must have reached COMPLETED status."""
    failed = [tc for tc in TOOL_CALLS if tc["status"] != "COMPLETED"]
    assert not failed, (
        f"Tool calls did not complete successfully: {[tc['toolName'] for tc in failed]}"
    )


def test_step_efficiency():
    """Agent must complete its assessment within a reasonable number of loop iterations."""
    assert ITERATIONS <= 3, (
        f"Agent used {ITERATIONS} loop iterations; "
        "expected ≤ 3 for a straightforward employed applicant."
    )
