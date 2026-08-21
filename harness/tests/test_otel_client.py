"""Tests for OtelClient (reads the local OTLP span-store JSONL file)."""

import json
from pathlib import Path

import pytest

from fluxnova.otel_client import (
    InvokeAgentMetrics,
    OtelClient,
    OtelClientError,
    ToolCallSpan,
)

_NS_PER_MS = 1_000_000


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


class TestGetInvokeAgentMetrics:
    def test_returns_metrics_from_invoke_agent_span(self, tmp_path: Path):
        store = tmp_path / "spans.jsonl"
        _write_store(store, [
            _span(
                "trace-1",
                "invoke_agent",
                {
                    "gen_ai.agent.name": "AdHocSubProcess_LoanAssessmentAgent",
                    "gen_ai.request.model": "llama3.1",
                    "gen_ai.conversation.id": "proc-123",
                    "gen_ai.usage.input_tokens": 100,
                    "gen_ai.usage.output_tokens": 50,
                    "gen_ai.invoke_agent.inference_calls": 3,
                    "gen_ai.invoke_agent.tool_calls": 2,
                },
                start_ns=0,
                end_ns=2_500 * _NS_PER_MS,
            ),
        ])
        client = OtelClient(store_path=store)
        result = client.get_invoke_agent_metrics("proc-123")

        assert result == InvokeAgentMetrics(
            agent_name="AdHocSubProcess_LoanAssessmentAgent",
            request_model="llama3.1",
            conversation_id="proc-123",
            input_tokens=100,
            output_tokens=50,
            inference_calls=3,
            tool_calls=2,
            duration_ms=2500.0,
        )

    def test_raises_when_store_missing(self, tmp_path: Path):
        client = OtelClient(store_path=tmp_path / "missing.jsonl")
        with pytest.raises(OtelClientError, match="not found"):
            client.get_invoke_agent_metrics("proc-123")

    def test_raises_when_no_matching_correlation_id(self, tmp_path: Path):
        store = tmp_path / "spans.jsonl"
        _write_store(store, [
            _span("trace-1", "invoke_agent", {"gen_ai.conversation.id": "other-proc"}),
        ])
        client = OtelClient(store_path=store)
        with pytest.raises(OtelClientError, match="No spans found"):
            client.get_invoke_agent_metrics("proc-123")

    def test_raises_when_trace_has_no_invoke_agent_span(self, tmp_path: Path):
        store = tmp_path / "spans.jsonl"
        _write_store(store, [
            _span("trace-1", "chat", {"gen_ai.conversation.id": "proc-123"}),
        ])
        client = OtelClient(store_path=store)
        with pytest.raises(OtelClientError, match="No invoke_agent span"):
            client.get_invoke_agent_metrics("proc-123")

    def test_ignores_spans_from_other_traces(self, tmp_path: Path):
        store = tmp_path / "spans.jsonl"
        _write_store(store, [
            _span("trace-1", "invoke_agent", {"gen_ai.conversation.id": "proc-123"}),
            _span("trace-2", "invoke_agent", {"gen_ai.conversation.id": "proc-999"}),
        ])
        client = OtelClient(store_path=store)
        result = client.get_invoke_agent_metrics("proc-123")
        assert result.conversation_id == "proc-123"


class TestGetToolCallSpans:
    def test_returns_one_entry_per_execute_tool_span_in_same_trace(self, tmp_path: Path):
        store = tmp_path / "spans.jsonl"
        _write_store(store, [
            _span("trace-1", "invoke_agent", {"gen_ai.conversation.id": "proc-123"}),
            _span("trace-1", "chat", {}, span_id="span-chat"),
            _span(
                "trace-1",
                "execute_tool",
                {
                    "gen_ai.tool.name": "Check Credit Score",
                    "gen_ai.tool.call.id": "call-1",
                    "gen_ai.agent.name": "AdHocSubProcess_LoanAssessmentAgent",
                },
                span_id="span-tool-1",
                start_ns=0,
                end_ns=500 * _NS_PER_MS,
                status_code="OK",
            ),
            _span(
                "trace-1",
                "execute_tool",
                {
                    "gen_ai.tool.name": "Assess Affordability",
                    "gen_ai.tool.call.id": "call-2",
                    "error.type": "tool_error",
                },
                span_id="span-tool-2",
                start_ns=0,
                end_ns=100 * _NS_PER_MS,
                status_code="ERROR",
            ),
            # A different trace's tool span must not leak in.
            _span("trace-2", "execute_tool", {"gen_ai.tool.name": "Other Run Tool"}, span_id="span-other"),
        ])
        client = OtelClient(store_path=store)
        result = client.get_tool_call_spans("proc-123")

        assert result == [
            ToolCallSpan(
                tool_name="Check Credit Score",
                tool_call_id="call-1",
                agent_name="AdHocSubProcess_LoanAssessmentAgent",
                status="OK",
                error_type=None,
                duration_ms=500.0,
            ),
            ToolCallSpan(
                tool_name="Assess Affordability",
                tool_call_id="call-2",
                agent_name=None,
                status="ERROR",
                error_type="tool_error",
                duration_ms=100.0,
            ),
        ]

    def test_empty_list_when_no_tool_spans(self, tmp_path: Path):
        store = tmp_path / "spans.jsonl"
        _write_store(store, [
            _span("trace-1", "invoke_agent", {"gen_ai.conversation.id": "proc-123"}),
        ])
        client = OtelClient(store_path=store)
        assert client.get_tool_call_spans("proc-123") == []


class TestGetLlmMessages:
    def test_returns_chat_spans_ordered_by_start_time(self, tmp_path: Path):
        store = tmp_path / "spans.jsonl"
        _write_store(store, [
            _span("trace-1", "invoke_agent", {"gen_ai.conversation.id": "proc-123"}),
            _span(
                "trace-1",
                "chat",
                {
                    "gen_ai.request.model": "llama3.1",
                    "gen_ai.input.messages": "[{\"role\":\"USER\",\"content\":\"hi\"}]",
                },
                span_id="span-chat-2",
                start_ns=2_000 * _NS_PER_MS,
            ),
            _span(
                "trace-1",
                "chat",
                {
                    "gen_ai.request.model": "llama3.1",
                    "gen_ai.input.messages": "[]",
                    "gen_ai.output.messages": "final answer text",
                },
                span_id="span-chat-1",
                start_ns=1_000 * _NS_PER_MS,
            ),
        ])
        client = OtelClient(store_path=store)
        result = client.get_llm_messages("proc-123")

        assert [m.start_time_unix_nano for m in result] == [1_000 * _NS_PER_MS, 2_000 * _NS_PER_MS]
        assert result[0].output_messages == "final answer text"
        assert result[1].output_messages is None

    def test_empty_list_when_no_chat_spans(self, tmp_path: Path):
        store = tmp_path / "spans.jsonl"
        _write_store(store, [
            _span("trace-1", "invoke_agent", {"gen_ai.conversation.id": "proc-123"}),
        ])
        client = OtelClient(store_path=store)
        assert client.get_llm_messages("proc-123") == []
