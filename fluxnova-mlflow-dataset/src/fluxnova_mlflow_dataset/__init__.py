"""Shared MLflow evaluation-dataset logic for the Fluxnova services.

Used by:
- ``fluxnova_listener`` — writes one record per completed agentic subprocess
  run into a persistent MLflow evaluation dataset.
- ``harness``'s ``mlflow_eval`` suite — reads records back (single instance or
  the whole dataset) and scores them.

Keeping this logic in one standalone, installable package (rather than
duplicated across the two consumers) guarantees what gets written matches
what gets read/evaluated, independent of either consumer's own config schema.
"""

from fluxnova_mlflow_dataset.goldens import load_goldens, match_golden
from fluxnova_mlflow_dataset.store import (
    build_mlflow_record,
    dataset_name_for,
    default_tracking_uri,
    experiment_name_for,
    get_or_create_dataset,
    record_exists,
    tracking_uri_for,
    write_to_mlflow_dataset,
)
from fluxnova_mlflow_dataset.tools import ExpectedToolRule, resolve_expected_tools

__all__ = [
    "ExpectedToolRule",
    "resolve_expected_tools",
    "load_goldens",
    "match_golden",
    "build_mlflow_record",
    "dataset_name_for",
    "default_tracking_uri",
    "experiment_name_for",
    "get_or_create_dataset",
    "record_exists",
    "tracking_uri_for",
    "write_to_mlflow_dataset",
]
