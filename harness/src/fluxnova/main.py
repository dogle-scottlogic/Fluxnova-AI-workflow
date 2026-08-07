"""Entry point for the Fluxnova workflow harness.

Reads a YAML config file, deploys the specified BPMN to Fluxnova,
opens an SSE event stream, starts a process instance, then waits for
the process to end.  Once complete, fetches the agent history via HTTP
and writes it to ``harness/.fluxnova/<process_key>/<instance_id>.json`` in the repo root.

Usage
-----
    harness workflow.yml
    harness --skip-deploy workflow.yml
    harness --with-mock-workers workflow.yml
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from fluxnova.client import Client
from fluxnova.config import WorkflowConfig
from fluxnova.events import Event

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deploy and start a Fluxnova workflow")
    parser.add_argument("config", type=Path, help="Path to the workflow YAML config file")
    parser.add_argument(
        "--skip-deploy",
        action="store_true",
        help="Skip BPMN deployment (use if already deployed)",
    )
    parser.add_argument(
        "--with-mock-workers",
        action="store_true",
        help="Start mock external task workers alongside the process",
    )
    return parser.parse_args(argv)


def deploy(config: WorkflowConfig, client: Client, skip: bool) -> None:
    if not skip:
        print(f"Deploying {config.bpmn_path} …")
        deployment = client.deploy(config.bpmn_path, deployment_name=config.deployment_name)
        print(f"  Deployed '{deployment.get('name')}' (id={deployment.get('id')})")


def initiate(config: WorkflowConfig, client: Client) -> str:
    print(f"Starting process '{config.process_key}' …")
    instance_id = client.start_process(config.process_key, config.variables)
    print(f"  Instance ID: {instance_id}")
    return instance_id


def _stream_into_queue(client: Client, event_queue: queue.Queue[Event | None]) -> None:
    """Background thread: stream SSE events and push them onto the queue."""
    try:
        for event in client.stream_events():
            event_queue.put(event)
    finally:
        event_queue.put(None)  # sentinel — stream ended


def _log_event(event: Event) -> None:
    ts = datetime.now(UTC).strftime("%H:%M:%S")
    print(f"  [{ts}] {event}")


def listen_for_events(
    event_queue: queue.Queue[Event | None], instance_id: str
) -> tuple[str, str | None]:
    """Log all events; return when the target process instance ends.

    Returns:
        A tuple of (started_at_iso, completed_at_iso).
        ``completed_at_iso`` is None if the stream closed before a
        PROCESS_INSTANCE_END was received for this instance.
    """
    print("Waiting for events …\n")
    started_at = datetime.now(UTC).isoformat()
    completed_at: str | None = None

    try:
        while True:
            event = event_queue.get()
            if event is None:
                print("Event stream closed.")
                break
            _log_event(event)
            if (
                event.sse_type == "PROCESS_INSTANCE_END"
                and event.process_instance_id == instance_id
            ):
                completed_at = datetime.now(UTC).isoformat()
                print(f"\nProcess {instance_id} ended.")
                break
    except KeyboardInterrupt:
        print("\nInterrupted.")

    return started_at, completed_at


def write_report(process_key: str, instance_id: str, agent_history: dict) -> Path:
    """Write the agent history JSON to ``harness/.fluxnova/<process_key>/<instance_id>.json``."""
    runs_dir = _REPO_ROOT / "harness" / ".fluxnova" / process_key
    runs_dir.mkdir(parents=True, exist_ok=True)
    report_path = runs_dir / f"{instance_id}.json"
    report_path.write_text(json.dumps(agent_history, indent=2, default=str), encoding="utf-8")
    print(f"Report written → {report_path}")
    return report_path


def main(argv: Iterable[str] | None = None) -> None:
    args = parse_args(argv)

    cfg = WorkflowConfig.from_file(args.config)
    client = Client(
        base_url=cfg.fluxnova_url,
        username=os.environ.get("FLUXNOVA_USERNAME"),
        password=os.environ.get("FLUXNOVA_PASSWORD"),
    )

    deploy(config=cfg, client=client, skip=args.skip_deploy)

    if args.with_mock_workers:
        from fluxnova import mock_workers as _mw
        _mw.start_workers(
            cfg,
            username=os.environ.get("FLUXNOVA_USERNAME"),
            password=os.environ.get("FLUXNOVA_PASSWORD"),
        )

    # Start the SSE stream before initiating so no events are missed.
    event_queue: queue.Queue[Event | None] = queue.Queue()
    stream_thread = threading.Thread(
        target=_stream_into_queue,
        args=(client, event_queue),
        daemon=True,
        name="event-stream",
    )
    stream_thread.start()
    print("Event stream connected.")

    instance_id = initiate(config=cfg, client=client)
    _started_at, _completed_at = listen_for_events(event_queue, instance_id)

    if cfg.subprocess_id and _completed_at is not None:
        print(f"Fetching agent history for subprocess '{cfg.subprocess_id}' …")
        agent_history = client.get_agent_history(instance_id, cfg.subprocess_id)
        write_report(cfg.process_key, instance_id, agent_history)
    elif not cfg.subprocess_id:
        print("No subprocess_id configured — skipping report.")


if __name__ == "__main__":
    main(sys.argv[1:])
