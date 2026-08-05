from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "skill-system" / "controller"
SCRIPTS = ROOT / "scripts"
for entry in (str(CONTROL), str(SCRIPTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

SCRIPT = ROOT / "scripts" / "github_repair_orchestrator.py"


def _load():
    spec = importlib.util.spec_from_file_location("github_repair_orchestrator_binding", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MODULE = _load()


class _Task:
    def __init__(self, binding):
        self.payload = {"binding": binding}


def _report():
    return {
        "repository": "owner/repo",
        "workflow_name": "quality",
        "workflow_run_id": "123",
        "workflow_run_attempt": "2",
        "head_sha": "a" * 40,
        "failure_signature": "f" * 64,
    }


def test_exact_stage1_task_binding_is_accepted() -> None:
    report = _report()
    MODULE._validate_task_binding(_Task(dict(report)), report)


def test_failure_signature_or_commit_drift_is_rejected() -> None:
    report = _report()
    binding = dict(report)
    binding["failure_signature"] = "0" * 64
    with pytest.raises(MODULE.OrchestratorError, match="TaskRun binding mismatch"):
        MODULE._validate_task_binding(_Task(binding), report)

    binding = dict(report)
    binding["head_sha"] = "b" * 40
    with pytest.raises(MODULE.OrchestratorError, match="TaskRun binding mismatch"):
        MODULE._validate_task_binding(_Task(binding), report)
