"""Tests for ``EvalServiceConfig.from_file``."""

from __future__ import annotations

from pathlib import Path

from eval_service_worker.config import EvalServiceConfig


def _write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "config.yml"
    path.write_text(content, encoding="utf-8")
    return path


class TestFromFile:
    def test_applies_defaults_when_eval_service_section_absent(self, tmp_path):
        path = _write(
            tmp_path,
            """
fluxnova_url: http://localhost:8090/
process_key: loanAssessmentProcess
""",
        )
        config = EvalServiceConfig.from_file(path)

        assert config.fluxnova_url == "http://localhost:8090"
        assert config.process_key == "loanAssessmentProcess"
        assert config.topic == "agent-output-eval"
        assert config.judge_model == "gateway:/fluxnova-judge"
        assert config.lock_duration_ms == 180_000
        assert config.tracking_uri == "http://localhost:5000"
        assert config.experiment_name is None

    def test_reads_eval_service_and_mlflow_dataset_overrides(self, tmp_path):
        path = _write(
            tmp_path,
            """
fluxnova_url: http://localhost:8090
process_key: loanAssessmentProcess
eval_service:
  topic: custom-eval-topic
  judge_model: ollama:/llama3.2
  lock_duration_ms: 60000
mlflow_dataset:
  tracking_uri: http://mlflow:5000
  experiment_name: fluxnova-loanAssessmentProcess-demo
""",
        )
        config = EvalServiceConfig.from_file(path)

        assert config.topic == "custom-eval-topic"
        assert config.judge_model == "ollama:/llama3.2"
        assert config.lock_duration_ms == 60_000
        assert config.tracking_uri == "http://mlflow:5000"
        assert config.experiment_name == "fluxnova-loanAssessmentProcess-demo"
