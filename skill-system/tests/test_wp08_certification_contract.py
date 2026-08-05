from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil

import pytest


WORKSPACE = Path(__file__).resolve().parents[2]
SCRIPT = WORKSPACE / "scripts" / "wp08_certification_contract.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("wp08_certification_contract", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load_module()


def _copy(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    for relative in (
        ".github/workflows/wp08-certification.yml",
        "deployment/ci/release-toolchain-lock.json",
        "deployment/ci/wp08-certification-batches.json",
        "scripts/run_wp08_certification.py",
        "scripts/prepare_wp08_resume.py",
    ):
        source = WORKSPACE / relative
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return root


def test_wp08_certification_assets_follow_locked_authority() -> None:
    result = MODULE.validate_static(WORKSPACE)
    assert result["status"] == "PASS"
    assert result["production_closed"] is False
    assert result["configuration_authority"] == "production-certification-environment"
    assert result["dispatch_inputs"] is False
    assert result["batch_ids"] == [
        "protected-environment-preflight",
        "postgres-pgvector-recovery",
        "real-model-rag",
        "browser-full-stack",
    ]


def test_wp08_workflow_rejects_mutable_action(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    workflow = root / ".github/workflows/wp08-certification.yml"
    lock = json.loads((root / "deployment/ci/release-toolchain-lock.json").read_text(encoding="utf-8"))
    workflow.write_text(
        workflow.read_text(encoding="utf-8").replace(lock["github_actions"]["actions/checkout"]["sha"], "v6", 1),
        encoding="utf-8",
    )
    with pytest.raises(MODULE.WP08ContractError) as caught:
        MODULE.validate_static(root)
    assert caught.value.code == "wp08_action_not_sha_pinned"


def test_wp08_workflow_rejects_missing_always_upload(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    workflow = root / ".github/workflows/wp08-certification.yml"
    workflow.write_text(workflow.read_text(encoding="utf-8").replace("        if: always()\n", "", 1), encoding="utf-8")
    with pytest.raises(MODULE.WP08ContractError) as caught:
        MODULE.validate_static(root)
    assert caught.value.code == "wp08_workflow_contract_missing"


def test_wp08_config_rejects_unbounded_timeout(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    config_path = root / "deployment" / "ci" / "wp08-certification-batches.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["batches"][0]["timeout_seconds"] = 0
    config_path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(MODULE.WP08ContractError) as caught:
        MODULE.validate_static(root)
    assert caught.value.code == "wp08_batch_timeout_invalid"


def test_wp08_workflow_cannot_claim_production_closed(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    workflow = root / ".github/workflows/wp08-certification.yml"
    workflow.write_text(workflow.read_text(encoding="utf-8") + "\n# production_closed=true\n", encoding="utf-8")
    with pytest.raises(MODULE.WP08ContractError) as caught:
        MODULE.validate_static(root)
    assert caught.value.code == "wp08_production_claim_forbidden"


def test_wp08_workflow_rejects_missing_cross_run_resume_action(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    workflow = root / ".github/workflows/wp08-certification.yml"
    text = workflow.read_text(encoding="utf-8")
    start = text.index("      - name: Download previous WP-08 checkpoint\n")
    end = text.index("      - name: Validate and restore previous WP-08 checkpoint\n")
    workflow.write_text(text[:start] + text[end:], encoding="utf-8")
    with pytest.raises(MODULE.WP08ContractError) as caught:
        MODULE.validate_static(root)
    assert caught.value.code in {"wp08_workflow_contract_missing", "wp08_resume_action_unlocked"}


def test_wp08_static_contract_reports_cross_run_resume() -> None:
    result = MODULE.validate_static(WORKSPACE)
    assert result["cross_run_resume"] is True
    assert result["resume_validator"] == "scripts/prepare_wp08_resume.py"


def test_wp08_workflow_rejects_manual_runtime_configuration_inputs(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    workflow = root / ".github/workflows/wp08-certification.yml"
    text = workflow.read_text(encoding="utf-8")
    text = text.replace(
        "  workflow_dispatch:\n",
        "  workflow_dispatch:\n    inputs:\n      model:\n        required: true\n        type: string\n",
        1,
    )
    text = text.replace("vars.OPENAI_MODEL", "inputs.model", 1)
    workflow.write_text(text, encoding="utf-8")
    with pytest.raises(MODULE.WP08ContractError) as caught:
        MODULE.validate_static(root)
    assert caught.value.code == "wp08_dispatch_inputs_forbidden"


def test_wp08_workflow_records_nonsecret_environment_configuration_evidence() -> None:
    text = (WORKSPACE / ".github/workflows/wp08-certification.yml").read_text(encoding="utf-8")
    assert "Resolve protected environment configuration" in text
    assert "wp08-environment-config.json" in text
    assert "PRODUCTION_MODEL_API_KEY" in text
    assert "PRODUCTION_EMBEDDING_API_KEY" in text
    assert "QUALITY_EVIDENCE_SIGNING_KEY" in text
    assert "inputs." not in text
