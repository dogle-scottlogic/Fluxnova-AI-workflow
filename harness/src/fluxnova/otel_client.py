"""Local OTLP trace-store client for the GenAI signals emitted by ``agentic-subprocess``.

Backend-agnostic: reads spans from the local JSON store written by
``fluxnova.otel_receiver`` instead of any vendor trace-store API. See
docs/deepeval-otel-gap-analysis.md and GENAI_SEMCONV_ALIGNMENT.md for rationale.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_DEFAULT_STORE = Path("harness/.fluxnova/otel-spans.json")

# Attribute set on every span by ``AgentOtelTracing`` — see
# GENAI_SEMCONV_ALIGNMENT.md's "Source -> signal mapping" table.
_OP_NAME_ATTR = "gen_ai.operation.name"
_CONVERSATION_ID_ATTR = "gen_ai.conversation.id"
_INVOKE_AGENT_OP = "invoke_agent"
_EXECUTE_TOOL_OP = "execute_tool"
_CHAT_OP = "chat"


class OtelClientError(Exception):
    """Raised when the trace store has no data (or incomplete data) for a run."""


@dataclass
class InvokeAgentMetrics:
    """Data read off the ``invoke_agent`` span for one subprocess execution."""

    agent_name: str | None
    request_model: str | None
    conversation_id: str | None
    input_tokens: int | None
    output_tokens: int | None
    inference_calls: int | None
    tool_calls: int | None
    duration_ms: float | None


@dataclass
class ToolCallSpan:
    """Data read off one ``execute_tool`` child span."""

    tool_name: str | None
    tool_call_id: str | None
    agent_name: str | None
    status: str
    error_type: str | None
    duration_ms: float | None


@dataclass
class ChatMessages:
    """Input/output message content read off one ``chat`` span, in call order."""

    model: str | None
    input_messages: str | None
    output_messages: str | None
    start_time_unix_nano: int


class OtelClient:
    """Thin wrapper over the local OTLP trace-store JSON file."""

    def __init__(self, store_path: Path | str = _DEFAULT_STORE) -> None:
        self._store_path = Path(store_path)

    def get_invoke_agent_metrics(self, correlation_id: str) -> InvokeAgentMetrics:
        """Return iteration/tool-call counts, tokens, and duration for one run.

        Args:
            correlation_id: The process instance id — matches ``gen_ai.conversation.id``
                             on the run's ``invoke_agent`` span.
        """
        spans = self._spans_for_correlation_id(correlation_id)
        span = self._find_span(spans, operation_name=_INVOKE_AGENT_OP)
        if span is None:
            raise OtelClientError(
                f"No invoke_agent span found for correlation id '{correlation_id}'"
            )
        attrs = span["attributes"]
        return InvokeAgentMetrics(
            agent_name=attrs.get("gen_ai.agent.name"),
            request_model=attrs.get("gen_ai.request.model"),
            conversation_id=attrs.get(_CONVERSATION_ID_ATTR),
            input_tokens=attrs.get("gen_ai.usage.input_tokens"),
            output_tokens=attrs.get("gen_ai.usage.output_tokens"),
            inference_calls=attrs.get("gen_ai.invoke_agent.inference_calls"),
            tool_calls=attrs.get("gen_ai.invoke_agent.tool_calls"),
            duration_ms=_duration_ms(span),
        )

    def get_tool_call_spans(self, correlation_id: str) -> list[ToolCallSpan]:
        """Return one ``ToolCallSpan`` per ``execute_tool`` child span for the run."""
        spans = self._spans_for_correlation_id(correlation_id)
        return [
            ToolCallSpan(
                tool_name=span["attributes"].get("gen_ai.tool.name"),
                tool_call_id=span["attributes"].get("gen_ai.tool.call.id"),
                agent_name=span["attributes"].get("gen_ai.agent.name"),
                status=span["status_code"],
                error_type=span["attributes"].get("error.type"),
                duration_ms=_duration_ms(span),
            )
            for span in spans
            if span["attributes"].get(_OP_NAME_ATTR) == _EXECUTE_TOOL_OP
        ]

    def get_llm_messages(self, correlation_id: str) -> list[ChatMessages]:
        """Return each ``chat`` span's input/output messages, ordered by call time."""
        spans = self._spans_for_correlation_id(correlation_id)
        chat_spans = sorted(
            (s for s in spans if s["attributes"].get(_OP_NAME_ATTR) == _CHAT_OP),
            key=lambda s: s.get("start_time_unix_nano", 0),
        )
        return [
            ChatMessages(
                model=span["attributes"].get("gen_ai.request.model"),
                input_messages=span["attributes"].get("gen_ai.input.messages"),
                output_messages=span["attributes"].get("gen_ai.output.messages"),
                start_time_unix_nano=span.get("start_time_unix_nano", 0),
            )
            for span in chat_spans
        ]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _spans_for_correlation_id(self, correlation_id: str) -> list[dict[str, Any]]:
        """Return every span sharing a trace_id with the invoke_agent span for this run."""
        all_spans = self._read_all_spans()
        matching_trace_ids = {
            span["trace_id"]
            for span in all_spans
            if span["attributes"].get(_CONVERSATION_ID_ATTR) == correlation_id
        }
        if not matching_trace_ids:
            raise OtelClientError(
                f"No spans found for correlation id '{correlation_id}' in {self._store_path}. "
                "Has the collector's harness exporter delivered this run's traces yet?"
            )
        return [span for span in all_spans if span["trace_id"] in matching_trace_ids]

    def _read_all_spans(self) -> list[dict[str, Any]]:
        if not self._store_path.exists():
            raise OtelClientError(f"OTLP span store not found: {self._store_path}")
        with self._store_path.open(encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    @staticmethod
    def _find_span(spans: list[dict[str, Any]], operation_name: str) -> dict[str, Any] | None:
        for span in spans:
            if span["attributes"].get(_OP_NAME_ATTR) == operation_name:
                return span
        return None


def _duration_ms(span: dict[str, Any]) -> float | None:
    start = span.get("start_time_unix_nano")
    end = span.get("end_time_unix_nano")
    if start is None or end is None:
        return None
    return (end - start) / 1e6
