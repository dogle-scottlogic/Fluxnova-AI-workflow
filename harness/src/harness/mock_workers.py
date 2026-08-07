"""Mock external task workers driven by a workflow YAML config.

Subscribes to every topic listed under ``mock_workers`` in the config file and
immediately completes each task with the configured output variables.

Usage
-----
    mock-workers harness/config/loan-assesment.yml
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from camunda.external_task.external_task import ExternalTask
from camunda.external_task.external_task_worker import ExternalTaskWorker

from harness.config import WorkflowConfig

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _to_camunda_vars(variables: dict[str, Any]) -> dict[str, dict[str, Any]]:
    _type_map = {str: "String", int: "Integer", float: "Double", bool: "Boolean"}
    return {
        name: {"value": value, "type": _type_map.get(type(value), "String")}
        for name, value in variables.items()
    }


def _make_handler(topic: str, outputs: dict[str, Any]):
    def handle(task: ExternalTask):
        print(f"[{_now()}] LOCKED  topic={topic} taskId={task.get_task_id()}")
        result = task.complete(global_variables=_to_camunda_vars(outputs))
        print(f"[{_now()}] DONE    topic={topic} outputs={outputs}")
        return result

    return handle


def run(cfg: WorkflowConfig, username: str | None = None, password: str | None = None) -> None:
    """Start one worker thread per topic and block until interrupted."""
    if not cfg.mock_workers:
        print("No mock_workers defined in config — nothing to do.")
        return

    worker_config: dict[str, Any] = {
        "maxTasks": 1,
        "lockDuration": 10_000,
        "asyncResponseTimeout": 10_000,
        "retries": 3,
        "retryTimeout": 5_000,
        "sleepSeconds": 2,
    }
    if username or password:
        worker_config["auth_basic"] = {"username": username or "", "password": password or ""}

    threads: list[threading.Thread] = []
    for i, (topic, outputs) in enumerate(cfg.mock_workers.items()):
        worker = ExternalTaskWorker(
            worker_id=str(i),
            base_url=cfg.fluxnova_url,
            config=worker_config,
        )
        t = threading.Thread(
            target=worker.subscribe,
            args=([topic], _make_handler(topic, outputs or {})),
            daemon=True,
            name=f"worker-{topic}",
        )
        threads.append(t)

    for t in threads:
        t.start()

    print(f"Mock workers running. Topics: {', '.join(cfg.mock_workers)}")

    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        print("\nShutting down mock workers.")


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run mock external task workers for a Fluxnova workflow")
    parser.add_argument("config", type=Path, help="Path to the workflow YAML config file")
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> None:
    args = parse_args(argv)
    cfg = WorkflowConfig.from_file(args.config)
    run(
        cfg,
        username=os.environ.get("FLUXNOVA_USERNAME"),
        password=os.environ.get("FLUXNOVA_PASSWORD"),
    )


if __name__ == "__main__":
    main(sys.argv[1:])
