"""Shared MLflow-native judge definitions.

Kept in one place so the *same* judge definition (guideline text, name) is used
identically by:

- the offline batch regression/analysis suite (``harness/src/mlflow_eval/main.py``,
  ``mlflow-eval`` CLI), and
- the synchronous ``eval-service-worker`` (a BPMN external-task worker that scores
  one just-completed subprocess run on demand — see ``EVAL-SERVICE-WORKER-PLAN.md``).

Avoids the guideline text drifting between the two call sites.
"""

from __future__ import annotations

from mlflow.genai.scorers import Guidelines

DECISION_QUALITY_NAME = "decision_quality"

DECISION_QUALITY_GUIDELINES = [
    "The final output must reach a definitive APPROVE or REJECT lending recommendation. "
    "Vague conclusions or deferred decisions such as 'next steps required' do not satisfy "
    "this guideline.",
    "The final output must state its recommendation backed by evidence: it must cite "
    "specific data points gathered during the assessment, such as credit score, fraud "
    "risk, affordability result, or employment status.",
]


def decision_quality_judge(model: str) -> Guidelines:
    """Build the ``decision_quality`` ``Guidelines`` judge for the given model URI.

    ``model`` differs by call site: a direct ``"<provider>:/<model>"`` URI (e.g.
    ``"ollama:/llama3.2"``) for offline batch evaluation, or a
    ``"gateway:/<endpoint>"`` URI for anything routed through the MLflow AI
    Gateway (automatic/online scoring, and the synchronous eval-service-worker).
    """
    return Guidelines(
        name=DECISION_QUALITY_NAME, guidelines=DECISION_QUALITY_GUIDELINES, model=model
    )
