"""Configuration for the ``eval-service-worker`` standalone service.

Reads the same workflow YAML config file used by ``fluxnova-runner``/``harness``
(e.g. ``harness/config/loan-assesment.yml``) rather than inventing a new file —
extra keys those tools use (``bpmn_path``, ``available_tools``, etc.) are simply
ignored here. Two new, optional sections are read:

    eval_service:
      topic: agent-output-eval       # external-task topic to subscribe to
      judge_model: gateway:/fluxnova-judge   # MLflow judge model URI
      lock_duration_ms: 180000       # generous — judge calls can take 90s+

    mlflow_dataset:
      tracking_uri: http://localhost:5000    # must be the MLflow *server's*
                                              # HTTP(S) address for gateway-routed
                                              # judge models to work (see
                                              # EDD-AND-PRODUCTION-EVAL-ANALYSIS.md)
      experiment_name: fluxnova-loanAssessmentProcess

(the ``mlflow_dataset`` section is the same one ``fluxnova.config.WorkflowConfig``
already reads — this loader mirrors just the two fields it needs.)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

_DEFAULT_TOPIC = "agent-output-eval"
_DEFAULT_JUDGE_MODEL = "gateway:/fluxnova-judge"
# Judge calls routed through the gateway to a local Ollama model have been
# observed taking 90+ seconds in this environment (see
# EDD-AND-PRODUCTION-EVAL-ANALYSIS.md / EVAL-SERVICE-WORKER-PLAN.md) — the lock
# duration must comfortably exceed that so Fluxnova doesn't reassign the task
# to another worker mid-scoring.
_DEFAULT_LOCK_DURATION_MS = 180_000
_DEFAULT_TRACKING_URI = "http://localhost:5000"


@dataclass
class EvalServiceConfig:
    """All settings needed to run the eval-service-worker against one workflow."""

    fluxnova_url: str
    process_key: str
    topic: str = _DEFAULT_TOPIC
    judge_model: str = _DEFAULT_JUDGE_MODEL
    lock_duration_ms: int = _DEFAULT_LOCK_DURATION_MS
    tracking_uri: str = _DEFAULT_TRACKING_URI
    experiment_name: str | None = None

    @classmethod
    def from_file(cls, path: Path) -> EvalServiceConfig:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        eval_service = raw.get("eval_service") or {}
        mlflow_dataset = raw.get("mlflow_dataset") or {}
        return cls(
            fluxnova_url=raw["fluxnova_url"].rstrip("/"),
            process_key=raw["process_key"],
            topic=eval_service.get("topic", _DEFAULT_TOPIC),
            judge_model=eval_service.get("judge_model", _DEFAULT_JUDGE_MODEL),
            lock_duration_ms=eval_service.get("lock_duration_ms", _DEFAULT_LOCK_DURATION_MS),
            tracking_uri=mlflow_dataset.get("tracking_uri", _DEFAULT_TRACKING_URI),
            experiment_name=mlflow_dataset.get("experiment_name"),
        )
