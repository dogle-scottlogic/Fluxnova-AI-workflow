"""Tests for ``fluxnova_listener.config.ListenerConfig``."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from fluxnova_listener.config import ListenerConfig


def _config_file(tmp_path: Path) -> Path:
    bpmn = tmp_path / "my.bpmn"
    bpmn.touch()
    cfg = tmp_path / "listener.yml"
    cfg.write_text(
        textwrap.dedent(f"""\
            fluxnova_url: http://localhost:8080/engine-rest/
            poll_interval_seconds: 3

            otel:
              port: 5000
              store_path: {tmp_path.as_posix()}/spans.jsonl

            mlflow:
              tracking_uri: sqlite:///{tmp_path.as_posix()}/mlflow.db

            watch:
              - subprocess_id: AdHocSubProcess_LoanAssessmentAgent
                process_key: loanAssessmentProcess
                bpmn_path: {bpmn.as_posix()}
                variables: [applicantType, hasCollateral]
                available_tools:
                  ServiceTask_CreditScoreCheck: Check Credit Score
                expected_tools:
                  - tool: Check Credit Score
                  - if: '$hasCollateral == true'
                    tool: Value Collateral
        """)
    )
    return cfg


def test_loads_top_level_fields(tmp_path: Path) -> None:
    config = ListenerConfig.from_file(_config_file(tmp_path))
    assert config.fluxnova_url == "http://localhost:8080/engine-rest"
    assert config.poll_interval_seconds == 3
    assert config.otel_port == 5000
    assert config.mlflow_tracking_uri.startswith("sqlite:///")


def test_loads_one_watched_subprocess(tmp_path: Path) -> None:
    config = ListenerConfig.from_file(_config_file(tmp_path))
    assert len(config.watch) == 1
    watched = config.watch[0]
    assert watched.subprocess_id == "AdHocSubProcess_LoanAssessmentAgent"
    assert watched.process_key == "loanAssessmentProcess"
    assert watched.variables == ["applicantType", "hasCollateral"]
    assert watched.available_tools == {"ServiceTask_CreditScoreCheck": "Check Credit Score"}
    assert [r.tool for r in watched.expected_tools] == ["Check Credit Score", "Value Collateral"]
    assert watched.also_write_json_report is True


def test_expected_tool_rule_conditions_evaluated(tmp_path: Path) -> None:
    config = ListenerConfig.from_file(_config_file(tmp_path))
    rule = config.watch[0].expected_tools[1]
    assert rule.matches({"hasCollateral": True})
    assert not rule.matches({"hasCollateral": False})


def test_raises_when_no_watch_entries(tmp_path: Path) -> None:
    cfg = tmp_path / "empty.yml"
    cfg.write_text("fluxnova_url: http://localhost:8080/engine-rest\nwatch: []\n")
    with pytest.raises(ValueError, match="at least one 'watch' entry"):
        ListenerConfig.from_file(cfg)
