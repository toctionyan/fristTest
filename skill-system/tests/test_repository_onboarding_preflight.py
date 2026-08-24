from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/repository_onboarding_preflight.py"


def _load():
    spec = importlib.util.spec_from_file_location("repository_onboarding_preflight", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load()


def _copy_workspace(tmp_path: Path) -> Path:
    target = tmp_path / "workspace"
    files = set(MODULE.REQUIRED_ROOT_FILES) | set(MODULE.REQUIRED_WORKFLOWS)
    for relative in files:
        source = ROOT / relative
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return target


def _ready_metadata() -> dict:
    return {
        "repository_full_name": "owner/customer-agent-workspace",
        "default_branch": "main",
        "visibility": "private",
        "permissions": {"admin": True, "maintain": True, "push": True},
        "is_empty": True,
        "branch_protection": {"main": True},
        "environments": ["production-certification"],
        "secret_names": [
            "PRODUCTION_MODEL_API_KEY",
            "PRODUCTION_EMBEDDING_API_KEY",
            "QUALITY_EVIDENCE_SIGNING_KEY",
        ],
    }


def test_current_workspace_static_contract_is_valid_and_external_repo_is_explicit_blocker(tmp_path: Path) -> None:
    result = MODULE.evaluate(_copy_workspace(tmp_path))
    assert result["status"] == "BLOCKED_BY_ENVIRONMENT"
    assert result["errors"] == []
    assert result["blockers"] == ["repository_metadata_missing"]
    assert result["production_closed"] is False


def test_private_empty_protected_repository_with_required_environment_is_ready(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    result = MODULE.evaluate(workspace, repository_metadata=_ready_metadata())
    assert result["status"] == "PASS"
    assert result["errors"] == []
    assert result["blockers"] == []
    assert result["workspace_identity"]["production_closed"] is False


def test_git_checkout_metadata_is_not_a_runtime_payload(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    git_metadata = workspace / ".git/objects"
    git_metadata.mkdir(parents=True)
    (git_metadata / "placeholder").write_bytes(b"checkout metadata")
    result = MODULE.evaluate(workspace, repository_metadata=_ready_metadata())
    assert result["status"] == "PASS"
    assert result["errors"] == []


def test_current_identity_is_exact_release_manifest_bytes(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    identity = MODULE._workspace_identity(workspace)
    assert identity["phase"] == "B28"
    assert identity["manifest_sha256"] == MODULE._sha256(workspace / "release/MANIFEST.json")
    assert not (workspace / "PHASE_CANDIDATE_MANIFEST.json").exists()


def test_runtime_directories_and_symlink_outside_git_remain_forbidden(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    forbidden = [
        "services/agent-service/.venv",
        "services/agent-service/frontend/node_modules",
        ".pytest_cache",
        "scripts/__pycache__",
    ]
    for relative in forbidden:
        (workspace / relative).mkdir(parents=True)
    (workspace / "unsafe-link").symlink_to(workspace / "release/MANIFEST.json")
    result = MODULE.evaluate(workspace, repository_metadata=_ready_metadata())
    assert result["status"] == "FAIL"
    assert {f"runtime_directory_forbidden:{relative}" for relative in forbidden} <= set(
        result["errors"]
    )
    assert "symlink_forbidden:unsafe-link" in result["errors"]


def test_missing_or_malformed_release_manifest_fails_closed(tmp_path: Path) -> None:
    missing_workspace = _copy_workspace(tmp_path / "missing")
    (missing_workspace / "release/MANIFEST.json").unlink()
    missing = MODULE.evaluate(missing_workspace, repository_metadata=_ready_metadata())
    assert missing["status"] == "FAIL"
    assert "required_file_missing:release/MANIFEST.json" in missing["errors"]

    malformed_workspace = _copy_workspace(tmp_path / "malformed")
    (malformed_workspace / "release/MANIFEST.json").write_text("not-json\n", encoding="utf-8")
    malformed = MODULE.evaluate(malformed_workspace, repository_metadata=_ready_metadata())
    assert malformed["status"] == "FAIL"
    assert "workspace_identity_invalid:json_invalid" in malformed["errors"]


def test_public_repository_requires_explicit_approval(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    metadata = _ready_metadata(); metadata["visibility"] = "public"
    blocked = MODULE.evaluate(workspace, repository_metadata=metadata)
    assert blocked["status"] == "BLOCKED_BY_ENVIRONMENT"
    assert "public_repository_requires_explicit_approval" in blocked["blockers"]
    allowed = MODULE.evaluate(workspace, repository_metadata=metadata, allow_public=True)
    assert allowed["status"] == "PASS"


def test_nonempty_unrelated_repository_is_rejected(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    metadata = _ready_metadata(); metadata["is_empty"] = False
    metadata["workspace_marker"] = {
        "workspace": "different-project",
        "version": "0",
        "skill_version": "0",
        "manifest_sha256": "0" * 64,
    }
    result = MODULE.evaluate(workspace, repository_metadata=metadata)
    assert result["status"] == "FAIL"
    assert "nonempty_repository_unrelated" in result["errors"]


def test_nonempty_matching_repository_can_be_resumed(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    identity = MODULE._workspace_identity(workspace)
    metadata = _ready_metadata(); metadata["is_empty"] = False; metadata["workspace_marker"] = identity
    result = MODULE.evaluate(workspace, repository_metadata=metadata)
    assert result["status"] == "PASS"


def test_missing_protection_environment_and_secret_names_are_independent_blockers(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    metadata = _ready_metadata()
    metadata["branch_protection"] = {"main": False}
    metadata["environments"] = []
    metadata["secret_names"] = ["PRODUCTION_MODEL_API_KEY"]
    result = MODULE.evaluate(workspace, repository_metadata=metadata)
    assert result["status"] == "BLOCKED_BY_ENVIRONMENT"
    assert "protected_main_missing" in result["blockers"]
    assert "environment_missing:production-certification" in result["blockers"]
    assert "secret_missing:PRODUCTION_EMBEDDING_API_KEY" in result["blockers"]
    assert "secret_missing:QUALITY_EVIDENCE_SIGNING_KEY" in result["blockers"]


def test_real_env_file_is_rejected_before_import(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    env_file = workspace / "services/agent-service/.env"
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text("OPENAI_API_KEY=secret\n", encoding="utf-8")
    result = MODULE.evaluate(workspace, repository_metadata=_ready_metadata())
    assert result["status"] == "FAIL"
    assert "real_env_file_forbidden:services/agent-service/.env" in result["errors"]


def test_integration_diagnostic_uses_release_toolchain_authority(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    assert MODULE._integration_workflow_authority(workspace) == []
    workflow = workspace / ".github/workflows/integration-diagnostic.yml"
    workflow.write_text(workflow.read_text(encoding="utf-8").replace("node-version: '24.18.0'", "node-version: '20.x'"), encoding="utf-8")
    errors = MODULE._integration_workflow_authority(workspace)
    assert "integration_workflow_unlocked:node" in errors


def test_skill_only_contract_explicitly_allows_integration_diagnostic_workflow() -> None:
    import sys
    controller = ROOT / "skill-system/controller"
    if str(controller) not in sys.path:
        sys.path.insert(0, str(controller))
    from contract import validate_contract_payload  # type: ignore
    payload = {
        "schema_version": 1, "change_id": "repair-test", "target_kind": "repair",
        "goal": "lock integration diagnostic", "profile": "skill-only",
        "allowed_paths": [".github/workflows/integration-diagnostic.yml"],
        "forbidden_paths": ["services/**", "web/**", "contracts/**"],
        "invariants": ["product unchanged"],
        "required_profiles": ["skill-static", "skill-unit", "skill-host-integration", "skill-security", "project-compatibility-smoke"],
        "writer_role": "skill-implementer", "review_roles": [], "review_attestations": [],
        "status": "approved",
    }
    assert validate_contract_payload(payload) == []
