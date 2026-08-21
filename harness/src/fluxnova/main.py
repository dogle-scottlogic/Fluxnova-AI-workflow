"""Entry point for the Fluxnova workflow harness.

Reads a YAML config file, deploys the specified BPMN to Fluxnova,
starts a process instance, polls until it completes, then builds an agent
report (BPMN + OTLP + core API — see fluxnova.report) and writes it to
``harness/.fluxnova/<process_key>/<instance_id>.json`` in the repo root.

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
import sys
from collections.abc import Iterable
from pathlib import Path

from fluxnova.bpmn import BpmnLookup
from fluxnova.client import Client
from fluxnova.config import WorkflowConfig
from fluxnova.otel_client import OtelClient
from fluxnova.report import build_agent_report

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

    instance_id = initiate(config=cfg, client=client)

    print("Waiting for process to complete …")
    client.wait_for_completion(instance_id)
    print(f"Process {instance_id} completed.")

    if cfg.subprocess_id:
        print(f"Building agent report for subprocess '{cfg.subprocess_id}' …")
        bpmn = BpmnLookup(cfg.bpmn_path, cfg.subprocess_id)
        otel = OtelClient()
        agent_history = build_agent_report(cfg, client, bpmn, otel, instance_id)
        write_report(cfg.process_key, instance_id, agent_history)
        print(f"Deep Eval: deep-eval config/loan-assesment.yml .fluxnova/{cfg.process_key}/{instance_id}.json")
    else:
        print("No subprocess_id configured — skipping report.")


if __name__ == "__main__":
    main(sys.argv[1:])

