"""Tests for ``fluxnova_listener.report.build_agent_report``."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from fluxnova_listener.bpmn import BpmnLookup
from fluxnova_listener.client import ListenerClient
from fluxnova_listener.otel_client import OtelClient
from fluxnova_listener.report import build_agent_report

_BPMN_PATH = Path(__file__).resolve().parent.parent.parent / "bpmn" / "loan-assesment.bpmn"
_SUBPROCESS_ID = "AdHocSubProcess_LoanAssessmentAgent"
_NS_PER_MS = 1_000_000

_VARIABLE_NAMES = [
    "applicationId",
    "customerId",
    "applicantName",
    "requestedAmount",
    "applicantType",
    "hasCollateral",
]


def _span(
    trace_id: str,
    operation_name: str,
    attributes: dict,
    span_id: str = "span-0",
    start_ns: int = 0,
    end_ns: int = 1_000 * _NS_PER_MS,
    status_code: str = "OK",
) -> dict:
    return {
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": None,
        "name": operation_name,
        "start_time_unix_nano": start_ns,
        "end_time_unix_nano": end_ns,
        "attributes": {"gen_ai.operation.name": operation_name, **attributes},
        "resource_attributes": {},
        "status_code": status_code,
        "status_message": None,
    }


def _write_store(path: Path, spans: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(s) for s in spans) + "\n", encoding="utf-8")


@pytest.fixture
def bpmn() -> BpmnLookup:
    return BpmnLookup(_BPMN_PATH, _SUBPROCESS_ID)


@pytest.fixture
def otel(tmp_path: Path) -> OtelClient:
    store = tmp_path / "spans.jsonl"
    _write_store(store, [
        _span(
            "trace-1",
            "invoke_agent",
            {
                "gen_ai.agent.name": _SUBPROCESS_ID,
                "gen_ai.request.model": "llama3.1",
                "gen_ai.conversation.id": "proc-123",
                "gen_ai.usage.input_tokens": 1220,
                "gen_ai.usage.output_tokens": 61,
                "gen_ai.invoke_agent.inference_calls": 2,
                "gen_ai.invoke_agent.tool_calls": 1,
            },
            start_ns=0,
            end_ns=82_284 * _NS_PER_MS,
        ),
        _span(
            "trace-1",
            "execute_tool",
            {
                "gen_ai.tool.name": "Run Fraud Screening",
                "gen_ai.tool.call.id": "call-1",
                "gen_ai.agent.name": _SUBPROCESS_ID,
            },
            span_id="span-tool-1",
            start_ns=0,
            end_ns=503 * _NS_PER_MS,
            status_code="OK",
        ),
        _span(
            "trace-1",
            "chat",
            {
                "gen_ai.request.model": "llama3.1",
                "gen_ai.input.messages": "[]",
                "gen_ai.output.messages": "APPROVE — all checks passed.",
            },
            span_id="span-chat-1",
            start_ns=600 * _NS_PER_MS,
            end_ns=82_000 * _NS_PER_MS,
        ),
    ])
    return OtelClient(store_path=store)


@pytest.fixture
def client() -> Mock:
    mock = Mock(spec=ListenerClient)
    mock.get_variables.return_value = {
        "applicationId": "APP-001",
        "customerId": "C001",
        "applicantName": "Jane Smith",
        "requestedAmount": 50000,
        "applicantType": "EMPLOYED",
        "hasCollateral": False,
        # Tool-output variables — must NOT leak into inputVariables.
        "fraudRiskScore": 12,
    }
    return mock


def test_build_agent_report_shape(client: Mock, bpmn: BpmnLookup, otel: OtelClient) -> None:
    report = build_agent_report(client, bpmn, otel, "proc-123", _VARIABLE_NAMES)

    assert report["processInstanceId"] == "proc-123"
    assert report["goal"].startswith("You are a senior loan assessment analyst.")
    assert report["finalOutput"] == "APPROVE — all checks passed."
    assert report["iterations"] == 2
    assert report["model"] == "llama3.1"
    assert report["totalPromptTokens"] == 1220
    assert report["totalCompletionTokens"] == 61
    assert report["executionTime"] == 82284.0


def test_input_variables_excludes_tool_outputs(client: Mock, bpmn: BpmnLookup, otel: OtelClient) -> None:
    report = build_agent_report(client, bpmn, otel, "proc-123", _VARIABLE_NAMES)

    assert report["inputVariables"] == {
        "applicationId": "APP-001",
        "customerId": "C001",
        "applicantName": "Jane Smith",
        "requestedAmount": 50000,
        "applicantType": "EMPLOYED",
        "hasCollateral": False,
    }
    assert "fraudRiskScore" not in report["inputVariables"]


def test_tool_calls_resolve_element_id_and_input(client: Mock, bpmn: BpmnLookup, otel: OtelClient) -> None:
    report = build_agent_report(client, bpmn, otel, "proc-123", _VARIABLE_NAMES)

    assert len(report["toolCalls"]) == 1
    call = report["toolCalls"][0]
    assert call["toolName"] == "Run Fraud Screening"
    assert call["toolElementId"] == "ServiceTask_FraudScreening"
    assert call["toolCallId"] == "call-1"
    assert call["status"] == "COMPLETED"
    assert call["durationMs"] == 503.0
    assert json.loads(call["toolInput"]) == {
        "applicationId": "APP-001",
        "customerId": "C001",
    }


def test_client_get_variables_called_with_instance_id(client: Mock, bpmn: BpmnLookup, otel: OtelClient) -> None:
    build_agent_report(client, bpmn, otel, "proc-123", _VARIABLE_NAMES)
    client.get_variables.assert_called_once_with("proc-123")
