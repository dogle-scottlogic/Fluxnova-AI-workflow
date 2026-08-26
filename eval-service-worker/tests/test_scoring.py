"""Tests for ``score_process_instance`` (mocks MLflow + trace-reader boundaries)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from eval_service_worker import scoring


@dataclass
class _FakeExperiment:
    experiment_id: str = "1"


@dataclass
class _FakeFeedback:
    name: str = "decision_quality"
    value: str = "yes"
    rationale: str = "The output is clear and well-reasoned."


class _FakeTraceReader:
    def __init__(self, tracking_uri: str, experiment_id: str) -> None:
        self.tracking_uri = tracking_uri
        self.experiment_id = experiment_id

    def get_final_output(self, process_instance_id: str) -> str | None:
        return _OUTPUTS.get(process_instance_id)

    def get_trace_id(self, process_instance_id: str) -> str:
        return f"tr-{process_instance_id}"


_OUTPUTS: dict[str, str | None] = {}


def _stub_common(monkeypatch: pytest.MonkeyPatch, feedback: _FakeFeedback) -> dict[str, Any]:
    calls: dict[str, Any] = {}

    monkeypatch.setattr(scoring.mlflow, "set_tracking_uri", lambda uri: calls.setdefault("tracking_uri", uri))
    monkeypatch.setattr(scoring.mlflow, "set_experiment", lambda name: (calls.setdefault("experiment_name", name), _FakeExperiment())[1])
    monkeypatch.setattr(
        scoring.mlflow,
        "log_feedback",
        lambda **kwargs: calls.setdefault("log_feedback_kwargs", kwargs),
    )
    monkeypatch.setattr(scoring, "MlflowTraceReader", _FakeTraceReader)
    monkeypatch.setattr(scoring, "decision_quality_judge", lambda model: lambda inputs, outputs: feedback)
    monkeypatch.setattr(
        scoring,
        "write_to_mlflow_dataset",
        lambda **kwargs: calls.setdefault("dataset_kwargs", kwargs),
    )
    return calls


class TestScoreProcessInstance:
    def test_passes_when_judge_says_yes(self, monkeypatch):
        _OUTPUTS["proc-1"] = "APPROVE: strong credit history"
        calls = _stub_common(monkeypatch, _FakeFeedback(value="yes", rationale="Looks good."))

        outcome = scoring.score_process_instance(
            "proc-1",
            tracking_uri="http://localhost:5000",
            process_key="loanAssessmentProcess",
            experiment_name=None,
            judge_model="gateway:/fluxnova-judge",
        )

        assert outcome.eval_passed is True
        assert outcome.eval_rationale == "Looks good."
        assert outcome.final_output == "APPROVE: strong credit history"
        assert calls["log_feedback_kwargs"]["trace_id"] == "tr-proc-1"
        assert calls["log_feedback_kwargs"]["value"] == "yes"
        assert calls["dataset_kwargs"]["record"]["tags"]["processInstanceId"] == "proc-1"
        assert calls["dataset_kwargs"]["record"]["tags"]["decision_quality"] == "yes"

    def test_fails_when_judge_says_no(self, monkeypatch):
        _OUTPUTS["proc-2"] = "REJECT: insufficient income"
        calls = _stub_common(monkeypatch, _FakeFeedback(value="no", rationale="Missing justification."))

        outcome = scoring.score_process_instance(
            "proc-2",
            tracking_uri="http://localhost:5000",
            process_key="loanAssessmentProcess",
            experiment_name=None,
            judge_model="gateway:/fluxnova-judge",
        )

        assert outcome.eval_passed is False
        assert calls["dataset_kwargs"]["record"]["tags"]["decision_quality"] == "no"

    def test_judge_value_comparison_is_case_insensitive(self, monkeypatch):
        _OUTPUTS["proc-3"] = "APPROVE"
        _stub_common(monkeypatch, _FakeFeedback(value="YES", rationale="ok"))

        outcome = scoring.score_process_instance(
            "proc-3",
            tracking_uri="http://localhost:5000",
            process_key="loanAssessmentProcess",
            experiment_name=None,
            judge_model="gateway:/fluxnova-judge",
        )

        assert outcome.eval_passed is True

    def test_raises_no_final_output_error_when_run_has_no_output_yet(self, monkeypatch):
        _OUTPUTS["proc-4"] = None
        _stub_common(monkeypatch, _FakeFeedback())

        with pytest.raises(scoring.NoFinalOutputError, match="proc-4"):
            scoring.score_process_instance(
                "proc-4",
                tracking_uri="http://localhost:5000",
                process_key="loanAssessmentProcess",
                experiment_name=None,
                judge_model="gateway:/fluxnova-judge",
            )
