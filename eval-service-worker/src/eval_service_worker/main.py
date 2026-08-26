"""CLI entry point for the eval-service-worker.

Usage
-----
    eval-service-worker config/loan-assesment.yml
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Iterable
from pathlib import Path

from eval_service_worker.config import EvalServiceConfig
from eval_service_worker.worker import run


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the eval-service-worker: scores completed agentic subprocess "
        "runs against the decision_quality MLflow judge."
    )
    parser.add_argument("config", type=Path, help="Path to the workflow YAML config file")
    parser.add_argument(
        "--log-level",
        default=os.environ.get("EVAL_SERVICE_LOG_LEVEL", "INFO"),
        help="Python logging level (default: INFO, or $EVAL_SERVICE_LOG_LEVEL)",
    )
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = EvalServiceConfig.from_file(args.config)
    run(
        config,
        username=os.environ.get("FLUXNOVA_USERNAME"),
        password=os.environ.get("FLUXNOVA_PASSWORD"),
    )


if __name__ == "__main__":
    main(sys.argv[1:])
