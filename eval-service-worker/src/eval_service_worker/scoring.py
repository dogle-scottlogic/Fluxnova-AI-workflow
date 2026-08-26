"""Scores one just-completed agentic subprocess run using the ``decision_quality``
judge, reading trace data only (no BPMN parsing, no Fluxnova REST API calls) —
see EVAL-SERVICE-WORKER-PLAN.md's "Resolved scope for this first pass".
"""

from __future__ import annotations

from dataclasses import dataclass

import mlflow
from fluxnova_mlflow_dataset import (
    MlflowTraceReader,
    decision_quality_judge,
    experiment_name_for,
    write_to_mlflow_dataset,
)
from mlflow.entities.assessment_source import AssessmentSource, AssessmentSourceType

# The Guidelines judge reports a categorical "yes"/"no" adherence value (not a
# numeric score) — confirmed by inspecting a real logged `decision_quality`
# assessment during this session's investigation.
_PASS_VALUE = "yes"

# Tag value used to distinguish worker-triggered assessments/dataset records
# from ones written by MLflow's built-in automatic-judge scheduler (which uses
# the same judge name but runs on a sample of traces, unprompted).
_SOURCE_TAG = "eval-service-worker"


class NoFinalOutputError(Exception):
    """Raised when the completed subprocess run has no output text to judge yet."""


@dataclass
class EvalOutcome:
    """The result of scoring one process instance's agentic subprocess run."""

    process_instance_id: str
    eval_passed: bool
    eval_rationale: str
    final_output: str


def score_process_instance(
    process_instance_id: str,
    *,
    tracking_uri: str,
    process_key: str,
    experiment_name: str | None,
    judge_model: str,
) -> EvalOutcome:
    """Fetch a completed subprocess run's final output from MLflow's trace store,
    run the ``decision_quality`` judge against it, and record the result.

    Logs the judge's ``Feedback`` back onto the trace as an assessment (tagged
    with ``_SOURCE_TAG``) and merges a lightweight record into the MLflow
    evaluation dataset, then returns the pass/fail outcome for the caller (the
    BPMN worker) to act on.
    """
    mlflow.set_tracking_uri(tracking_uri)
    experiment = mlflow.set_experiment(experiment_name_for(process_key, experiment_name))

    reader = MlflowTraceReader(tracking_uri, experiment.experiment_id)
    final_output = reader.get_final_output(process_instance_id)
    if not final_output:
        raise NoFinalOutputError(
            f"No final output found for process instance {process_instance_id!r} — "
            "has the agentic subprocess actually completed and been exported to "
            "MLflow yet?"
        )

    judge = decision_quality_judge(judge_model)
    # `Guidelines` requires an `inputs` field to be present (even though our
    # guideline text only ever references the final output) — an empty dict
    # satisfies the field-presence check without adding anything the judge uses.
    feedback = judge(inputs={}, outputs=final_output)
    eval_passed = str(feedback.value).strip().lower() == _PASS_VALUE
    rationale = feedback.rationale or ""

    _log_trace_assessment(process_instance_id, reader, feedback, judge_model)
    _log_dataset_record(
        process_key=process_key,
        experiment_name=experiment_name,
        tracking_uri=tracking_uri,
        process_instance_id=process_instance_id,
        final_output=final_output,
        eval_passed=eval_passed,
        rationale=rationale,
        judge_model=judge_model,
    )

    return EvalOutcome(
        process_instance_id=process_instance_id,
        eval_passed=eval_passed,
        eval_rationale=rationale,
        final_output=final_output,
    )


def _log_trace_assessment(
    process_instance_id: str,
    reader: MlflowTraceReader,
    feedback,
    judge_model: str,
) -> None:
    """Attach the judge's feedback onto the run's trace as an assessment."""
    trace_id = reader.get_trace_id(process_instance_id)
    mlflow.log_feedback(
        trace_id=trace_id,
        name=feedback.name,
        value=feedback.value,
        rationale=feedback.rationale,
        source=AssessmentSource(source_type=AssessmentSourceType.LLM_JUDGE, source_id=judge_model),
        metadata={"triggered_by": _SOURCE_TAG},
    )


def _log_dataset_record(
    *,
    process_key: str,
    experiment_name: str | None,
    tracking_uri: str,
    process_instance_id: str,
    final_output: str,
    eval_passed: bool,
    rationale: str,
    judge_model: str,
) -> None:
    """Merge a lightweight evaluation-dataset record for this run.

    Unlike ``fluxnova_mlflow_dataset.store.build_mlflow_record`` (used by the
    offline collector), this worker has no BPMN/Fluxnova REST access to enrich
    the record with ``agent_goal``/``input_variables``/``tool_calls`` — only
    the trace-derived output and this judge's verdict are recorded.
    """
    record = {
        "inputs": {"process_instance_id": process_instance_id},
        "outputs": final_output,
        "expectations": {},
        "tags": {
            "processInstanceId": process_instance_id,
            "processKey": process_key,
            "source": _SOURCE_TAG,
            "decision_quality": "yes" if eval_passed else "no",
            "decision_quality_rationale": rationale,
            "judge_model": judge_model,
        },
    }
    write_to_mlflow_dataset(
        tracking_uri=tracking_uri,
        process_key=process_key,
        dataset_name=None,
        record=record,
        skip_if_exists=False,
        experiment_name=experiment_name,
    )
