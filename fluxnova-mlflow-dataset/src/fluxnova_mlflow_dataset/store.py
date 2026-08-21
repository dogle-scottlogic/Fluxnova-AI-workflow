"""Read/write helpers for the persistent MLflow evaluation dataset.

MLflow's ``EvaluationDataset.merge_records`` enforces a strict input schema:
a record's ``inputs`` dict is classified as either "trace" (any keys) or
"session" (only from ``{goal, persona, context, simulation_guidelines}`` —
intended for multi-turn agent simulation), and forbids mixing "session"
identifier keys (like ``goal``) with other custom keys. To stay in the
permissive "trace" bucket we deliberately avoid the reserved key name
``goal`` in favour of ``agent_goal``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fluxnova_mlflow_dataset.goldens import load_goldens, match_golden
from fluxnova_mlflow_dataset.tools import ExpectedToolRule, resolve_expected_tools

if TYPE_CHECKING:
    from mlflow.genai.datasets import EvaluationDataset

# Sanitises a process_key into a safe MLflow dataset/experiment name.
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


def experiment_name_for(process_key: str) -> str:
    """The MLflow experiment name used for a workflow's runs/records."""
    return f"fluxnova-{_SAFE_NAME.sub('-', process_key)}"


def dataset_name_for(process_key: str, name_override: str | None = None) -> str:
    """The persistent MLflow evaluation dataset name for a workflow.

    Defaults to the experiment name, but callers can override it (e.g. via a
    ``dataset_name``/``mlflow_dataset.name`` config field).
    """
    return name_override or experiment_name_for(process_key)


def default_tracking_uri(repo_root: Path) -> str:
    """The local SQLite MLflow tracking store shared by writers and readers."""
    db_path = repo_root / "harness" / ".mlflow" / "mlflow.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{db_path.as_posix()}"


def tracking_uri_for(tracking_uri_override: str | None, repo_root: Path) -> str:
    return tracking_uri_override or default_tracking_uri(repo_root)


# ---------------------------------------------------------------------------
# Record building
# ---------------------------------------------------------------------------

def build_mlflow_record(
    *,
    process_key: str,
    process_instance_id: str,
    agent_goal: str,
    input_variables: dict[str, Any],
    tool_calls: list[dict[str, Any]],
    iterations: int | None,
    final_output: str | None,
    available_tools: list[str],
    expected_tool_rules: list[ExpectedToolRule],
    dataset_path: Path | None,
) -> dict[str, Any]:
    """Shape one completed run as an MLflow dataset record.

    The resulting dict is valid input to both
    ``EvaluationDataset.merge_records([...])`` and ``mlflow.genai.evaluate(data=[...])``.
    """
    goldens = load_goldens(dataset_path)
    golden = match_golden(goldens, input_variables)

    return {
        "inputs": {
            "agent_goal": agent_goal,
            "input_variables": input_variables,
            "tool_calls": tool_calls,
            "iterations": iterations,
            "available_tools": available_tools,
            "expected_tools": resolve_expected_tools(expected_tool_rules, input_variables),
        },
        "outputs": final_output,
        "expectations": {
            "expected_output": golden["expected_output"] if golden else None,
        },
        "tags": {
            "processInstanceId": process_instance_id,
            "processKey": process_key,
            "applicantType": input_variables.get("applicantType"),
            "hasCollateral": input_variables.get("hasCollateral"),
        },
    }


# ---------------------------------------------------------------------------
# Dataset access (lazy mlflow import — only needed when actually reading/writing)
# ---------------------------------------------------------------------------

def get_or_create_dataset(name: str, experiment_id: str) -> EvaluationDataset:
    """Fetch the named MLflow evaluation dataset, creating it if it doesn't exist yet."""
    import mlflow.genai.datasets as datasets
    from mlflow.exceptions import MlflowException

    try:
        return datasets.get_dataset(name=name)
    except MlflowException:
        return datasets.create_dataset(name=name, experiment_id=[experiment_id])


def record_exists(dataset: EvaluationDataset, process_instance_id: str) -> bool:
    """Return True if a record tagged with this ``processInstanceId`` is already stored."""
    df = dataset.to_df()
    if df.empty:
        return False
    matches = df["tags"].apply(lambda t: (t or {}).get("processInstanceId") == process_instance_id)
    return bool(matches.any())


def write_to_mlflow_dataset(
    *,
    tracking_uri: str,
    process_key: str,
    dataset_name: str | None,
    record: dict[str, Any],
    skip_if_exists: bool = True,
) -> tuple[str, str | None, bool]:
    """Merge ``record`` into the configured MLflow dataset.

    Returns ``(dataset_name, dataset_record_id, written)``. ``written`` is
    False (and ``dataset_record_id`` is None) when ``skip_if_exists`` is True
    and a record with this ``processInstanceId`` tag is already present —
    this is how repeated writes for the same run are made idempotent
    (dataset records have no natural "update" operation, so we skip instead).
    """
    import mlflow

    mlflow.set_tracking_uri(tracking_uri)
    experiment = mlflow.set_experiment(experiment_name_for(process_key))

    resolved_name = dataset_name_for(process_key, dataset_name)
    dataset = get_or_create_dataset(resolved_name, experiment.experiment_id)

    instance_id = record["tags"]["processInstanceId"]
    if skip_if_exists and record_exists(dataset, instance_id):
        return resolved_name, None, False

    dataset.merge_records([record])

    df = dataset.to_df()
    matches = df[df["tags"].apply(lambda t: (t or {}).get("processInstanceId") == instance_id)]
    record_id = matches.iloc[-1]["dataset_record_id"] if not matches.empty else None
    return resolved_name, record_id, True
