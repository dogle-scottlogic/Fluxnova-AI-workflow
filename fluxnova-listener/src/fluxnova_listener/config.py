"""Configuration for the standalone Fluxnova listener service.

Unlike the harness's per-workflow config (one YAML = one deploy/eval target),
the listener's config lists *multiple* agentic subprocesses to watch by BPMN
id, since it runs as a single long-lived process independent of any one
workflow run.

Example
-------
    fluxnova_url: http://localhost:8080/engine-rest
    poll_interval_seconds: 5

    otel:
      port: 4319
      store_path: fluxnova-listener/.data/otel-spans.json

    mlflow:
      tracking_uri: sqlite:///harness/.mlflow/mlflow.db

    watch:
      - subprocess_id: AdHocSubProcess_LoanAssessmentAgent
        process_key: loanAssessmentProcess
        bpmn_path: bpmn/loan-assesment.bpmn
        variables: [applicantType, hasCollateral, requestedAmount, applicationId, customerId]
        available_tools:
          ServiceTask_CreditScoreCheck: Check Credit Score
        expected_tools:
          - tool: Check Credit Score
        dataset_path: datasets/loan-assessment/goldens.json
        dataset_name: fluxnova-loanAssessmentProcess   # optional, defaults to fluxnova-<process_key>
        also_write_json_report: true
        report_dir: fluxnova-listener/.data/reports/loanAssessmentProcess
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from fluxnova_mlflow_dataset import ExpectedToolRule

_DEFAULT_OTEL_PORT = 4319
_DEFAULT_OTEL_STORE = Path("fluxnova-listener/.data/otel-spans.json")
_DEFAULT_POLL_INTERVAL = 5.0


@dataclass
class WatchedSubprocess:
    """One agentic ad-hoc subprocess the listener records completions for."""

    subprocess_id: str
    process_key: str
    bpmn_path: Path
    variables: list[str] = field(default_factory=list)
    available_tools: dict[str, str] = field(default_factory=dict)
    expected_tools: list[ExpectedToolRule] = field(default_factory=list)
    dataset_path: Path | None = None
    dataset_name: str | None = None
    also_write_json_report: bool = True
    report_dir: Path | None = None


@dataclass
class ListenerConfig:
    """All settings for one run of the listener service."""

    fluxnova_url: str
    otel_port: int
    otel_store_path: Path
    poll_interval_seconds: float
    mlflow_tracking_uri: str | None
    watch: list[WatchedSubprocess]

    @classmethod
    def from_file(cls, path: Path) -> ListenerConfig:
        """Load and parse a listener YAML config file.

        Relative paths (``bpmn_path``, ``dataset_path``, ``report_dir``,
        ``otel.store_path``) are resolved relative to the repo root (four
        directories up from this file, same convention as the harness).
        """
        root_dir = Path(__file__).resolve().parent.parent.parent.parent
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))

        otel_raw = raw.get("otel") or {}
        mlflow_raw = raw.get("mlflow") or {}

        watch = [_parse_watched_subprocess(entry, root_dir) for entry in raw.get("watch") or []]
        if not watch:
            raise ValueError(f"Listener config {path} must define at least one 'watch' entry")

        return cls(
            fluxnova_url=raw["fluxnova_url"].rstrip("/"),
            otel_port=otel_raw.get("port", _DEFAULT_OTEL_PORT),
            otel_store_path=root_dir / Path(otel_raw.get("store_path", _DEFAULT_OTEL_STORE)),
            poll_interval_seconds=raw.get("poll_interval_seconds", _DEFAULT_POLL_INTERVAL),
            mlflow_tracking_uri=mlflow_raw.get("tracking_uri"),
            watch=watch,
        )


def _parse_watched_subprocess(entry: dict[str, Any], root_dir: Path) -> WatchedSubprocess:
    raw_dataset_path = entry.get("dataset_path")
    raw_report_dir = entry.get("report_dir")
    expected_tools = [
        ExpectedToolRule(tool=r["tool"], if_expr=r.get("if"))
        for r in (entry.get("expected_tools") or [])
    ]
    return WatchedSubprocess(
        subprocess_id=entry["subprocess_id"],
        process_key=entry["process_key"],
        bpmn_path=root_dir / Path(entry["bpmn_path"]),
        variables=entry.get("variables") or [],
        available_tools=entry.get("available_tools") or {},
        expected_tools=expected_tools,
        dataset_path=(root_dir / raw_dataset_path) if raw_dataset_path else None,
        dataset_name=entry.get("dataset_name"),
        also_write_json_report=entry.get("also_write_json_report", True),
        report_dir=(root_dir / raw_report_dir) if raw_report_dir else None,
    )
