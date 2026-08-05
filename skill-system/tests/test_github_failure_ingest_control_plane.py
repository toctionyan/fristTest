from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
SCRIPT = SCRIPTS / "github_failure_ingest_control_plane.py"


def _load():
    spec = importlib.util.spec_from_file_location(
        "github_failure_ingest_control_plane_test", SCRIPT
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load()


def _event(*, conclusion: str = "failure") -> dict:
    return {
        "repository": {"full_name": "owner/repo"},
        "workflow_run": {
            "id": 30972234843,
            "run_attempt": 1,
            "name": "quality",
            "event": "pull_request",
            "conclusion": conclusion,
            "head_sha": "a" * 40,
            "head_branch": "canary/stage4",
            "head_repository": {"full_name": "owner/repo"},
            "html_url": "https://example.invalid/run/30972234843",
            "pull_requests": [
                {
                    "number": 143,
                    "head": {"ref": "canary/stage4"},
                    "base": {"ref": "main"},
                }
            ],
        },
    }


def _source(tmp_path: Path, relative: str) -> None:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x = 1\n", encoding="utf-8")


def test_exact_product_source_drift_marker_authorizes_same_repo_repair(
    tmp_path: Path,
) -> None:
    source = "services/agent-service/app/__init__.py"
    _source(tmp_path, source)
    log = (
        '"status": "FAIL"\n'
        '"errors": [\n'
        f'  "product_source_changed:{source}"\n'
        ']\n'
    )
    result = MODULE.build_report(
        _event(),
        workspace=tmp_path,
        artifact_files=[(tmp_path / "skill-control-plane.log", log)],
        changed_files=[source],
    )
    assert result["classification"] == "code_or_contract"
    assert result["repair_allowed"] is True
    assert result["candidate_paths"] == [source]
    assert result["failed_gates"] == [
        {
            "gate_id": "project-compatibility-smoke",
            "status": "FAIL",
            "category": "contract",
            "owner": "skill-control-plane",
            "failure_kind": "product_source_drift",
            "summary": f"product_source_changed:{source}",
        }
    ]


def test_incidental_verifier_path_is_removed_from_pr_repair_scope(
    tmp_path: Path,
) -> None:
    source = "services/agent-service/app/__init__.py"
    verifier = "scripts/verify_task_ledger.py"
    _source(tmp_path, source)
    _source(tmp_path, verifier)
    log = (
        f"product_source_changed:{source}\n"
        f"python {verifier} failed while reporting {source}\n"
    )
    result = MODULE.build_report(
        _event(),
        workspace=tmp_path,
        artifact_files=[(tmp_path / "skill-control-plane.log", log)],
        changed_files=[source],
    )
    assert result["repair_allowed"] is True
    assert result["candidate_paths"] == [source]
    assert verifier not in result["candidate_paths"]


def test_repeated_control_plane_marker_is_deduplicated(tmp_path: Path) -> None:
    source = "services/agent-service/app/main.py"
    _source(tmp_path, source)
    marker = f"product_source_changed:{source}"
    rows = MODULE._control_plane_failures(
        [
            (tmp_path / "one.log", marker + "\n" + marker),
            (tmp_path / "two.log", marker),
        ]
    )
    assert len(rows) == 1
    assert rows[0]["summary"] == marker


def test_changed_file_metadata_remains_non_authoritative(tmp_path: Path) -> None:
    source = "services/agent-service/app/main.py"
    _source(tmp_path, source)
    result = MODULE.build_report(
        _event(),
        workspace=tmp_path,
        artifact_files=[(tmp_path / "job.log", "process failed")],
        changed_files=[source],
    )
    assert result["classification"] == "unknown_failure_without_gate_evidence"
    assert result["candidate_paths"] == []
    assert result["repair_allowed"] is False


def test_non_product_marker_cannot_create_repair_authority(tmp_path: Path) -> None:
    protected = "governance/task-ledger.json"
    _source(tmp_path, protected)
    result = MODULE.build_report(
        _event(),
        workspace=tmp_path,
        artifact_files=[
            (tmp_path / "job.log", f"product_source_changed:{protected}")
        ],
        changed_files=[protected],
    )
    assert result["failed_gates"] == []
    assert result["candidate_paths"] == []
    assert result["repair_allowed"] is False


def test_environment_evidence_still_overrides_product_drift(tmp_path: Path) -> None:
    source = "services/agent-service/app/main.py"
    _source(tmp_path, source)
    log = (
        f"product_source_changed:{source}\n"
        "ERROR missing secret PRODUCTION_MODEL_API_KEY\n"
    )
    result = MODULE.build_report(
        _event(),
        workspace=tmp_path,
        artifact_files=[(tmp_path / "job.log", log)],
        changed_files=[source],
    )
    assert result["classification"] == "environment"
    assert result["repair_allowed"] is False


def test_adapter_protects_all_governed_bridge_entrypoints() -> None:
    MODULE.install()
    for path in MODULE.BRIDGE_PROTECTED_EXACT:
        assert path in MODULE.base.PROTECTED_EXACT
    assert "scripts/github_repair_orchestrator_control_plane.py" in MODULE.BRIDGE_PROTECTED_EXACT
