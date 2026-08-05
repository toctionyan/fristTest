from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
SCRIPT = SCRIPTS / "github_repair_orchestrator_control_plane.py"


def _load():
    spec = importlib.util.spec_from_file_location(
        "github_repair_orchestrator_control_plane_test", SCRIPT
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load()


def _report() -> dict:
    return {
        "schema": "github-failure-ingest@1",
        "failure_signature": "a" * 64,
        "candidate_paths": [
            "services/agent-service/app/__init__.py",
            "scripts/verify_task_ledger.py",
        ],
        "source_changed_files": ["services/agent-service/app/__init__.py"],
    }


def test_stage2_intersects_evidence_candidates_with_source_changes() -> None:
    normalized = MODULE.normalize_failure_case(_report())
    assert normalized["candidate_paths"] == [
        "services/agent-service/app/__init__.py"
    ]
    assert normalized["failure_signature"] == "a" * 64
    scope = normalized["stage2_scope_normalization"]
    assert scope["scope_expanded"] is False
    assert scope["effective_candidates"] == normalized["candidate_paths"]
    assert "scripts/verify_task_ledger.py" in scope["evidence_candidates"]


def test_changed_files_never_add_a_candidate() -> None:
    report = _report()
    report["candidate_paths"] = []
    report["source_changed_files"] = ["services/agent-service/app/__init__.py"]
    normalized = MODULE.normalize_failure_case(report)
    assert normalized["candidate_paths"] == []


def test_push_without_changed_metadata_retains_evidence_scope() -> None:
    report = _report()
    report["source_changed_files"] = []
    normalized = MODULE.normalize_failure_case(report)
    assert normalized["candidate_paths"] == report["candidate_paths"]


def test_invalid_schema_is_rejected() -> None:
    with pytest.raises(MODULE.ScopeNormalizationError):
        MODULE.normalize_failure_case({"schema": "unknown"})
