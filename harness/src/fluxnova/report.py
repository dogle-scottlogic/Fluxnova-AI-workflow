"""Builds the agent-history report from OTLP + BPMN + core-API sources.

See docs/deepeval-otel-gap-analysis.md (phased-approach steps 4/7) for the
rationale and field-by-field mapping.
"""

from __future__ import annotations

import json
from typing import Any

from fluxnova.bpmn import BpmnLookup
from fluxnova.client import Client
from fluxnova.config import WorkflowConfig
from fluxnova.otel_client import OtelClient


def build_agent_report(
    config: WorkflowConfig,
    client: Client,
    bpmn: BpmnLookup,
    otel: OtelClient,
    instance_id: str,
) -> dict[str, Any]:
    """Compose an ``/agent-history``-shaped report for one process instance."""
    variables = client.get_variables(instance_id)
    input_variables = {name: variables[name] for name in config.variables if name in variables}

    metrics = otel.get_invoke_agent_metrics(instance_id)
    tool_spans = otel.get_tool_call_spans(instance_id)
    element_id_by_tool_name = {name: element_id for element_id, name in bpmn.tool_names().items()}

    tool_calls = [
        _build_tool_call(span, bpmn, variables, element_id_by_tool_name) for span in tool_spans
    ]

    final_output = _last_output_message(otel.get_llm_messages(instance_id))

    return {
        "processInstanceId": instance_id,
        "goal": bpmn.system_prompt(),
        "finalOutput": final_output,
        "iterations": metrics.inference_calls,
        "inputVariables": input_variables,
        "toolCalls": tool_calls,
        "model": metrics.request_model,
        "totalPromptTokens": metrics.input_tokens,
        "totalCompletionTokens": metrics.output_tokens,
        "executionTime": metrics.duration_ms,
    }


def _build_tool_call(
    span: Any,
    bpmn: BpmnLookup,
    variables: dict[str, Any],
    element_id_by_tool_name: dict[str, str],
) -> dict[str, Any]:
    """Build one ``toolCalls[]`` entry from a tool-call span + BPMN/variable data."""
    element_id = element_id_by_tool_name.get(span.tool_name) if span.tool_name else None
    tool_input: dict[str, Any] = {}
    if element_id is not None:
        params = bpmn.tool_input_output_params(element_id)
        tool_input = {name: variables.get(name) for name in params.input_params}
    return {
        "toolName": span.tool_name,
        "toolElementId": element_id,
        "toolCallId": span.tool_call_id,
        "toolInput": json.dumps(tool_input),
        "status": "COMPLETED" if span.status == "OK" else "FAILED",
        "durationMs": span.duration_ms,
    }


def _last_output_message(messages: list[Any]) -> str | None:
    """Return the most recent ``chat`` span's output text, if any."""
    for message in reversed(messages):
        if message.output_messages is not None:
            return message.output_messages
    return None
