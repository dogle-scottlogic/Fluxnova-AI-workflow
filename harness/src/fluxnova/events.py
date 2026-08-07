"""SSE history event types for the Fluxnova Engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Event:
    """A single SSE history event received from ``/history/event-stream``."""

    sse_type: str          # SSE ``event:`` line, e.g. ``TASK_INSTANCE_CREATE``
    id: str
    event_type: str        # JSON ``eventType`` field, e.g. ``"create"``
    process_instance_id: str
    root_process_instance_id: str
    process_definition_id: str
    process_definition_key: str
    execution_id: str
    raw: dict[str, Any] = field(repr=False)

    @classmethod
    def from_sse(cls, sse_type: str, data: dict[str, Any]) -> Event:
        return cls(
            sse_type=sse_type,
            id=data.get("id", ""),
            event_type=data.get("eventType", ""),
            process_instance_id=data.get("processInstanceId", ""),
            root_process_instance_id=data.get("rootProcessInstanceId", ""),
            process_definition_id=data.get("processDefinitionId", ""),
            process_definition_key=data.get("processDefinitionKey", ""),
            execution_id=data.get("executionId", ""),
            raw=data,
        )

    def __str__(self) -> str:
        parts = [f"{self.sse_type:<36} pid={self.process_instance_id}"]
        r = self.raw
        if name := r.get("taskName"):
            parts.append(f"task={name!r}")
        if key := r.get("taskDefinitionKey"):
            parts.append(f"key={key}")
        if assignee := r.get("assignee"):
            parts.append(f"assignee={assignee}")
        if state := r.get("state"):
            parts.append(f"state={state}")
        if var := r.get("variableName"):
            parts.append(f"var={var}={r.get('textValue') or r.get('longValue')!r}")
        if msg := r.get("incidentMessage"):
            parts.append(f"message={msg!r}")
        if op := r.get("operationType"):
            parts.append(f"op={op} {r.get('property')}={r.get('newValue')!r}")
        return "  ".join(parts)
