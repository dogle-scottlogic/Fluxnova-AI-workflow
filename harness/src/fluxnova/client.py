"""Fluxnova REST API client.

Provides helpers for:
- Deploying a BPMN file
- Starting a process instance
- Streaming history events via SSE
- Polling until the instance completes TODO
- Fetching final process variables TODO
"""

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import requests

from fluxnova.events import Event


class ApiError(Exception):
    """Raised when the Fluxnova REST API returns an unexpected response."""


class Client:
    """Thin wrapper around the Fluxnova Engine REST API."""

    def __init__(
            self,
            base_url: str,
            root: Path | None = None,
            username: str | None = None,
            password: str | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._agent_base = self._base.removesuffix("/engine-rest")
        self._root = root or Path.cwd()
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})
        if username is not None or password is not None:
            self._session.auth = (username or "", password or "")

    def deploy(self, bpmn_path: Path, deployment_name: str) -> dict[str, Any]:
        """Deploy a BPMN file and return the deployment resource.

        Args:
            bpmn_path: Path to the .bpmn file, relative to the client root directory.
            deployment_name: Human-readable name shown in Fluxnova Cockpit.

        Returns:
            The deployment response dict from Fluxnova.
        """
        name = deployment_name
        resolved = self._root / bpmn_path
        with resolved.open("rb") as fh:
            response = self._session.post(
                f"{self._base}/deployment/create",
                data={"deployment-name": name, "enable-duplicate-filtering": "true"},
                files={"upload": (resolved.name, fh, "application/octet-stream")},
                headers={"Content-Type": None},  # type: ignore[arg-type]
            )
        self._raise_for_status(response, "deploy BPMN")
        return response.json()

    def start_process(
            self,
            process_key: str,
            variables: dict[str, Any] | None = None,
    ) -> str:
        """Start a new process instance and return its instance ID.

        Args:
            process_key: The process definition key (``id`` attribute on
                         ``<process>`` in the BPMN).
            variables: Initial process variables as a plain Python dict.
                       Values are automatically wrapped in Camunda variable format.

        Returns:
            The new process instance ID.
        """
        body: dict[str, Any] = {}
        if variables:
            body["variables"] = _to_camunda_vars(variables)

        response = self._session.post(
            f"{self._base}/process-definition/key/{process_key}/start",
            json=body,
        )
        self._raise_for_status(response, "start process")
        return response.json()["id"]

    def claim_task(self, task_id: str, user_id: str) -> dict[str, Any]:
        """Claim a task and return its instance ID."""
        response = self._session.post(
            url=f"{self._base}/task/{task_id}/claim",
            data={"userId": user_id},
            headers={"Content-Type": "application/json"}
        )
        self._raise_for_status(response, "claim a task")
        return response.json()

    def complete_task(self, task_id: str, variables) -> dict[str, Any]:
        """Complete a task and return its instance ID."""
        response = self._session.post(
            url=f"{self._base}/task/{task_id}/complete",
            data={"variables": variables,
                  "withVariablesInReturn": True
                  },
            headers={"Content-Type": "application/json"}
        )
        self._raise_for_status(response, "complete a task")
        return response.json()

    def get_instance(self, instance_id: str) -> dict[str, Any] | None:
        """Return the process instance dict, or *None* if it has ended."""
        response = self._session.get(f"{self._base}/process-instance/{instance_id}")
        if response.status_code == 404:
            return None
        self._raise_for_status(response, "get process instance")
        return response.json()

    def get_history(self, instance_id: str) -> dict[str, Any]:
        """Return the historic process instance (always available, even after completion)."""
        response = self._session.get(f"{self._base}/history/process-instance/{instance_id}")
        self._raise_for_status(response, "get historic process instance")
        return response.json()

    def get_variables(self, instance_id: str) -> dict[str, Any]:
        """Return the current (or final) variables for an instance.

        Queries the historic variable API so this works after the instance ends.
        Returns a plain Python dict of ``{name: value}``.
        """
        response = self._session.get(
            f"{self._base}/history/variable-instance",
            params={"processInstanceId": instance_id},
        )
        self._raise_for_status(response, "get variables")
        return {item["name"]: item["value"] for item in response.json()}

    def get_agent_history(self, instance_id: str, subprocess_id: str) -> dict[str, Any]:
        """Fetch the agent history for a completed subprocess.

        Args:
            instance_id:  The process instance ID.
            subprocess_id: The BPMN element ID of the ad-hoc subprocess
                           (e.g. ``AdHocSubProcess_LoanAssessmentAgent``).

        Returns:
            The raw JSON response from the agent-history endpoint.
        """
        response = self._session.get(
            f"{self._agent_base}/agent-history/process/{instance_id}/subprocess/{subprocess_id}",
        )
        self._raise_for_status(response, "get agent history")
        return response.json()

    # ------------------------------------------------------------------
    # Polling helper
    # ------------------------------------------------------------------

    def wait_for_completion(
            self,
            instance_id: str,
            poll_interval: float = 2.0,
            timeout: float = 300.0,
    ) -> dict[str, Any]:
        """Block until the process instance finishes, then return its final variables.

        Args:
            instance_id: The process instance ID to monitor.
            poll_interval: Seconds between status checks.
            timeout: Maximum seconds to wait before raising ``TimeoutError``.

        Returns:
            A plain Python dict of final process variables.

        Raises:
            TimeoutError: If the instance has not completed within *timeout* seconds.
            ApiError: If the instance ended with an error/incident state.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            instance = self.get_instance(instance_id)
            if instance is None:
                # Instance no longer active — check historic record
                history = self.get_history(instance_id)
                state = history.get("state", "UNKNOWN")
                if state not in {"COMPLETED", "EXTERNALLY_TERMINATED"}:
                    raise ApiError(f"Process instance {instance_id} ended in state '{state}'")
                return self.get_variables(instance_id)
            time.sleep(poll_interval)

        raise TimeoutError(f"Process instance {instance_id} did not complete within {timeout}s")

    # ------------------------------------------------------------------
    # Event streaming
    # ------------------------------------------------------------------

    def stream_events(self) -> Iterator[Event]:
        """Open an SSE connection and yield events as they arrive.

        Subscribes to the fixed set of event types defined in
        :data:`_STREAM_EVENT_TYPES`.

        Yields:
            :class:`~fluxnova.events.Event` for each SSE message received.

        Raises:
            ApiError: If the server returns a non-2xx response on connect.
        """
        params = [("eventTypes", t) for t in _STREAM_EVENT_TYPES]

        response = self._session.get(
            f"{self._base}/history/event-stream",
            params=params,
            stream=True,
            headers={"Accept": "text/event-stream"},
        )
        self._raise_for_status(response, "stream events")
        yield from _parse_sse(response)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _raise_for_status(response: requests.Response, action: str) -> None:
        if not response.ok:
            raise ApiError(f"Failed to {action}: HTTP {response.status_code} — {response.text}")


def _to_camunda_vars(variables: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Convert a plain dict to Camunda variable format.

    Each value is wrapped as ``{"value": ..., "type": ...}``.
    Supported Python types: str, int, float, bool.
    Everything else is serialised as a String.
    """
    _type_map = {
        str: "String",
        int: "Integer",
        float: "Double",
        bool: "Boolean",
    }
    result = {}
    for name, value in variables.items():
        camunda_type = _type_map.get(type(value), "String")
        result[name] = {"value": value, "type": camunda_type}
    return result


_STREAM_EVENT_TYPES: tuple[str, ...] = (
    "TASK_INSTANCE_CREATE",
    "TASK_INSTANCE_COMPLETE",
    "INCIDENT_CREATE",
    "JOB_FAIL",
    "EXTERNAL_TASK_FAIL",
    "PROCESS_INSTANCE_END",
    "agent-subprocess:start",
    "agent-subprocess:end",
    "agent-llm:request",
    "agent-llm:response",
    "agent-tool-call:requested",
    "agent-tool-call:completed",
    "agent-tool-call:failed",
    "agent-loop:start",
    "agent-loop:end",
)


def _parse_sse(response: requests.Response) -> Iterator[Event]:
    """Parse an SSE response stream and yield :class:`Event` objects."""
    sse_type: str | None = None
    for raw_line in response.iter_lines():
        line: str = raw_line.decode() if isinstance(raw_line, bytes) else str(raw_line)
        if line.startswith("event:"):
            sse_type = line[6:].strip()
        elif line.startswith("data:") and sse_type is not None:
            data: dict[str, Any] = json.loads(line[5:].strip())
            yield Event.from_sse(sse_type, data)
            sse_type = None
