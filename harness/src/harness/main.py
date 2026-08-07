"""Entry point for the Fluxnova workflow harness.

Reads a YAML config file, deploys the specified BPMN to Fluxnova, and
starts a new process instance with the configured variables.

Usage
-----
    harness workflow.yml
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Iterable

from harness.client import Client
from harness.config import WorkflowConfig


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deploy and start a Fluxnova workflow")
    parser.add_argument("config", type=Path, help="Path to the workflow YAML config file")
    parser.add_argument(
        "--skip-deploy",
        action="store_true",
        help="Skip BPMN deployment (use if already deployed)",
    )
    return parser.parse_args(argv)


def deploy(config: WorkflowConfig, client: Client, skip: bool) -> None:
    if not skip:
        print(f"Deploying {config.bpmn_path} …")
        deployment = client.deploy(config.bpmn_path, deployment_name=config.deployment_name)
        print(f"  Deployed '{deployment.get('name')}' (id={deployment.get('id')})")


def initiate(config: WorkflowConfig, client: Client) -> None:
    print(f"Starting process '{config.process_key}' …")
    instance_id = client.start_process(config.process_key, config.variables)
    print(f"  Instance ID: {instance_id}")


def main(argv: Iterable[str] | None = None) -> None:
    args = parse_args(argv)

    cfg = WorkflowConfig.from_file(args.config)
    client = Client(
        base_url=cfg.fluxnova_url,
        username=os.environ.get("FLUXNOVA_USERNAME"),
        password=os.environ.get("FLUXNOVA_PASSWORD"),
    )
    deploy(config=cfg, client=client, skip=args.skip_deploy)
    # initiate(config=cfg, client=client)

    # handle tasks
    # await complete (how?)


if __name__ == "__main__":
    main(sys.argv[1:])
