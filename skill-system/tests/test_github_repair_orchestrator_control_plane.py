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


def _write(workspace: Path, relative: str, content: str = "x = 1\n") -> None:
    path = workspace / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_stage2_intersects_evidence_candidates_with_source_changes() -> None:
    normalized = MODULE.normalize_failure_case(_report())
    assert normalized["candidate_paths"] == [
        "services/agent-service/app/__init__.py"
    ]
    assert normalized["failure_signature"] == "a" * 64
    scope = normalized["stage2_scope_normalization"]
    assert scope["schema"] == "stage2-scope-normalization@2"
    assert scope["scope_expanded"] is False
    assert scope["evidence_paths"] == normalized["candidate_paths"]
    assert "scripts/verify_task_ledger.py" in scope["evidence_candidates"]


def test_repair_scope_separates_product_source_from_test_oracles(tmp_path: Path) -> None:
    source = "services/agent-service/src/agent_core/runtime/example.py"
    oracle = "services/agent-service/tests/runtime/test_example.py"
    _write(tmp_path, source)
    _write(tmp_path, oracle, "def test_example():\n    assert True\n")
    report = {
        "schema": "github-failure-ingest@1",
        "candidate_paths": [source, oracle],
        "source_changed_files": [source, oracle],
    }

    compiled = MODULE.compile_repair_scope(report, workspace=tmp_path)
    scope = compiled["stage2_scope_normalization"]
    assert compiled["candidate_paths"] == [source]
    assert scope["evidence_paths"] == [source, oracle]
    assert scope["writable_paths"] == [source]
    assert scope["protected_oracle_paths"] == [oracle]
    assert scope["repair_scope_status"] == "REPAIRABLE_WITH_PROTECTED_ORACLES"
    assert scope["scope_expanded"] is False


def test_test_only_evidence_never_becomes_writable_scope(tmp_path: Path) -> None:
    oracle = "services/agent-service/tests/architecture/test_invariant.py"
    _write(tmp_path, oracle, "def test_invariant():\n    assert True\n")
    report = {
        "schema": "github-failure-ingest@1",
        "candidate_paths": [oracle],
        "source_changed_files": [oracle],
    }

    compiled = MODULE.compile_repair_scope(report, workspace=tmp_path)
    scope = compiled["stage2_scope_normalization"]
    assert compiled["candidate_paths"] == []
    assert scope["writable_paths"] == []
    assert scope["protected_oracle_paths"] == [oracle]
    assert scope["repair_scope_status"] == "TEST_CONTRACT_REVIEW_REQUIRED"
    assert scope["excluded_paths"][0]["path"] == oracle


def test_protected_and_manifest_paths_remain_evidence_but_not_write_authority(tmp_path: Path) -> None:
    source = "services/agent-service/src/agent_core/runtime/example.py"
    manifest = "services/agent-service/package.json"
    _write(tmp_path, source)
    _write(tmp_path, manifest, "{}\n")
    report = {
        "schema": "github-failure-ingest@1",
        "candidate_paths": [source, manifest],
        "source_changed_files": [source, manifest],
    }

    compiled = MODULE.compile_repair_scope(report, workspace=tmp_path)
    scope = compiled["stage2_scope_normalization"]
    assert compiled["candidate_paths"] == [source]
    assert scope["evidence_paths"] == [source, manifest]
    assert scope["protected_oracle_paths"] == []
    assert [row["path"] for row in scope["excluded_paths"]] == [manifest]


def test_changed_files_never_add_a_candidate() -> None:
    report = _report()
    report["candidate_paths"] = []
    report["source_changed_files"] = ["services/agent-service/app/__init__.py"]
    normalized = MODULE.normalize_failure_case(report)
    assert normalized["candidate_paths"] == []
    assert normalized["stage2_scope_normalization"]["evidence_paths"] == []


def test_push_without_changed_metadata_retains_evidence_scope() -> None:
    report = _report()
    report["source_changed_files"] = []
    normalized = MODULE.normalize_failure_case(report)
    assert normalized["candidate_paths"] == report["candidate_paths"]
    assert normalized["stage2_scope_normalization"]["evidence_paths"] == report["candidate_paths"]


def test_scope_normalization_does_not_hide_traversal() -> None:
    report = {
        "schema": "github-failure-ingest@1",
        "candidate_paths": ["../services/agent-service/app/__init__.py"],
        "source_changed_files": ["../services/agent-service/app/__init__.py"],
    }
    normalized = MODULE.normalize_failure_case(report)
    assert normalized["candidate_paths"] == [
        "../services/agent-service/app/__init__.py"
    ]


def test_invalid_schema_is_rejected() -> None:
    with pytest.raises(MODULE.ScopeNormalizationError):
        MODULE.normalize_failure_case({"schema": "unknown"})
