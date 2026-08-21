"""Standalone Fluxnova listener service.

Runs in the background and:

1. Accepts OTLP/HTTP trace exports (``POST /v1/traces``) the same way
   ``otel-receiver`` does, appending spans to a local JSON store.
2. Polls that store for ``invoke_agent`` spans whose ``gen_ai.agent.name``
   matches one of the ``watch[].subprocess_id`` entries in the config — i.e.
   BPMN ad-hoc subprocess ids such as ``AdHocSubProcess_LoanAssessmentAgent``.
   A span only appears in the store once its subprocess has ended, so this
   *is* the "completed" signal — no separate Camunda polling is needed.
3. For each newly-completed run not already recorded, builds an
   agent-history report (BPMN + OTLP + core API) and upserts it into the
   configured MLflow evaluation dataset (skipped if a record for that
   ``processInstanceId`` already exists — idempotent under restarts/retries).
   Optionally also writes the report as a JSON file.

Usage
-----
    fluxnova-listener fluxnova-listener/config/listener.yml

Run it detached/in the background (e.g. ``nohup`` / a systemd unit / a
Windows scheduled task) — it runs until interrupted (Ctrl+C) or killed.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from collections.abc import Iterable
from pathlib import Path

from fluxnova_mlflow_dataset import build_mlflow_record, write_to_mlflow_dataset

from fluxnova_listener.bpmn import BpmnLookup
from fluxnova_listener.client import ListenerClient
from fluxnova_listener.config import ListenerConfig, WatchedSubprocess
from fluxnova_listener.otel_client import OtelClient
from fluxnova_listener.otel_receiver import make_server
from fluxnova_listener.report import build_agent_report

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _start_otel_receiver(store_path: Path, port: int) -> None:
    """Start the OTLP/HTTP receiver server on a background daemon thread."""
    server = make_server(store_path, port)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="otel-receiver")
    thread.start()
    print(f"OTLP trace receiver listening on :{port} (POST /v1/traces) — writing to {store_path}")


def _write_report_file(report_dir: Path, instance_id: str, agent_history: dict) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{instance_id}.json"
    report_path.write_text(json.dumps(agent_history, indent=2, default=str), encoding="utf-8")
    return report_path


def _process_run(
    watched: WatchedSubprocess,
    client: ListenerClient,
    otel: OtelClient,
    instance_id: str,
    tracking_uri: str,
) -> None:
    """Build a report for one completed run and record it into the MLflow dataset."""
    print(f"[{watched.subprocess_id}] completed run detected: processInstanceId={instance_id}")
    bpmn = BpmnLookup(watched.bpmn_path, watched.subprocess_id)

    agent_history = build_agent_report(client, bpmn, otel, instance_id, watched.variables)

    record = build_mlflow_record(
        process_key=watched.process_key,
        process_instance_id=instance_id,
        agent_goal=agent_history["goal"],
        input_variables=agent_history["inputVariables"],
        tool_calls=agent_history["toolCalls"],
        iterations=agent_history["iterations"],
        final_output=agent_history["finalOutput"],
        available_tools=list(watched.available_tools.values()),
        expected_tool_rules=watched.expected_tools,
        dataset_path=watched.dataset_path,
    )
    dataset_name, record_id, written = write_to_mlflow_dataset(
        tracking_uri=tracking_uri,
        process_key=watched.process_key,
        dataset_name=watched.dataset_name,
        record=record,
    )
    if written:
        print(f"  -> MLflow dataset record written: dataset='{dataset_name}' record_id={record_id}")
    else:
        print(f"  -> Already recorded in MLflow dataset '{dataset_name}' - skipped")

    if watched.also_write_json_report:
        report_dir = watched.report_dir or (
            _REPO_ROOT / "fluxnova-listener" / ".data" / "reports" / watched.process_key
        )
        report_path = _write_report_file(report_dir, instance_id, agent_history)
        print(f"  -> JSON report written: {report_path}")


def _poll_loop(config: ListenerConfig, tracking_uri: str) -> None:
    client = ListenerClient(base_url=config.fluxnova_url)
    otel = OtelClient(store_path=config.otel_store_path)
    watched_by_id = {w.subprocess_id: w for w in config.watch}
    seen: set[str] = set()

    print(
        f"Watching subprocesses: {', '.join(watched_by_id)} "
        f"(poll every {config.poll_interval_seconds}s)"
    )
    while True:
        try:
            for instance_id, agent_name in otel.find_completed_runs(set(watched_by_id)):
                if instance_id in seen:
                    continue
                seen.add(instance_id)
                watched = watched_by_id[agent_name]
                try:
                    _process_run(watched, client, otel, instance_id, tracking_uri)
                except Exception as exc:  # noqa: BLE001 - keep the loop alive on a single bad run
                    print(f"  ! Failed to process run {instance_id}: {exc}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - keep the service alive on transient errors
            print(f"! Poll iteration failed: {exc}", file=sys.stderr)
        time.sleep(config.poll_interval_seconds)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the standalone Fluxnova listener service")
    parser.add_argument("config", type=Path, help="Path to the listener YAML config file")
    return parser.parse_args(list(argv) if argv is not None else None)


def _default_tracking_uri() -> str:
    from fluxnova_mlflow_dataset import default_tracking_uri

    return default_tracking_uri(_REPO_ROOT)


def main(argv: Iterable[str] | None = None) -> None:
    args = parse_args(argv)
    config = ListenerConfig.from_file(args.config)
    tracking_uri = config.mlflow_tracking_uri or _default_tracking_uri()

    _start_otel_receiver(config.otel_store_path, config.otel_port)
    try:
        _poll_loop(config, tracking_uri)
    except KeyboardInterrupt:
        print("\nShutting down listener.")


if __name__ == "__main__":
    main(sys.argv[1:])
