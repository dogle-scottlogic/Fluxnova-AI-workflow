"""Configuration loaded from a YAML workflow config file."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class WorkflowConfig:
    """All settings needed to deploy and start one workflow run."""

    fluxnova_url: str
    bpmn_path: Path
    process_key: str
    deployment_name: str
    subprocess_id: str | None = None
    variables: dict[str, Any] = field(default_factory=dict)
    mock_workers: dict[str, dict[str, Any]] = field(default_factory=dict)
    user_tasks: dict[str, dict[str, Any]] = field(default_factory=dict)


    @classmethod
    def from_file(cls, path: Path) -> WorkflowConfig:
        """Load and validate a YAML config file.

        The ``bpmn_path`` value is resolved relative to the config file's
        directory if it is not absolute.
        """
        script_path = Path(__file__).resolve()
        root_dir = script_path.parent.parent.parent.parent
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        bpmn_path = Path(raw["bpmn_path"])
        bpmn_path = root_dir / bpmn_path
        return cls(
            fluxnova_url=raw["fluxnova_url"].rstrip("/"),
            bpmn_path=bpmn_path,
            process_key=raw["process_key"],
            variables=raw.get("variables") or {},
            deployment_name=raw.get("deployment_name"),
            subprocess_id=raw.get("subprocess_id"),
            mock_workers=raw.get("mock_workers") or {},
            user_tasks=raw.get("user_tasks") or {},
        )
