from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil

import pytest


WORKSPACE = Path(__file__).resolve().parents[2]
SCRIPT = WORKSPACE / "scripts" / "quality_toolchain_contract.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("quality_toolchain_contract", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load_module()


def _copy_workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    for relative in (
        ".github/workflows/quality.yml",
        "deployment/ci/release-toolchain-lock.json",
    ):
        source = WORKSPACE / relative
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return root


def test_quality_workflow_uses_release_toolchain_authority():
    result = MODULE.validate_static(WORKSPACE)
    assert result["status"] == "PASS"
    assert result["python_version"] == "3.12.13"
    assert result["node_version"] == "24.18.0"
    assert result["npm_version"] == "11.16.0"
    assert result["uv_version"] == "0.11.29"
    assert result["postgres_image"].startswith("pgvector/pgvector@sha256:")


def test_quality_workflow_rejects_mutable_action_tag(tmp_path: Path):
    root = _copy_workspace(tmp_path)
    workflow = root / ".github/workflows/quality.yml"
    text = workflow.read_text(encoding="utf-8")
    lock = json.loads((root / "deployment/ci/release-toolchain-lock.json").read_text(encoding="utf-8"))
    text = text.replace(lock["github_actions"]["actions/checkout"]["sha"], "v6", 1)
    workflow.write_text(text, encoding="utf-8")
    with pytest.raises(MODULE.QualityToolchainError) as caught:
        MODULE.validate_static(root)
    assert caught.value.code == "quality_action_not_sha_pinned"


def test_quality_workflow_rejects_node_drift(tmp_path: Path):
    root = _copy_workspace(tmp_path)
    workflow = root / ".github/workflows/quality.yml"
    workflow.write_text(
        workflow.read_text(encoding="utf-8").replace("node-version: '24.18.0'", "node-version: '20.x'", 1),
        encoding="utf-8",
    )
    with pytest.raises(MODULE.QualityToolchainError) as caught:
        MODULE.validate_static(root)
    assert caught.value.code == "quality_toolchain_workflow_unlocked"


def test_quality_workflow_rejects_mutable_postgres_tag(tmp_path: Path):
    root = _copy_workspace(tmp_path)
    workflow = root / ".github/workflows/quality.yml"
    lock = json.loads((root / "deployment/ci/release-toolchain-lock.json").read_text(encoding="utf-8"))
    workflow.write_text(
        workflow.read_text(encoding="utf-8").replace(lock["postgres_image"], "pgvector/pgvector:pg16"),
        encoding="utf-8",
    )
    with pytest.raises(MODULE.QualityToolchainError) as caught:
        MODULE.validate_static(root)
    assert caught.value.code in {"quality_postgres_image_unlocked", "quality_postgres_tag_forbidden"}


def test_quality_workflow_rejects_toolchain_step_reordering(tmp_path: Path):
    root = _copy_workspace(tmp_path)
    workflow = root / ".github/workflows/quality.yml"
    text = workflow.read_text(encoding="utf-8")
    bootstrap = text.index("      - name: Bootstrap locked uv")
    validate = text.index("      - name: Validate locked runtime toolchain", bootstrap)
    bootstrap_block = text[bootstrap:validate]
    validate_end = text.index("      - name: Install locked Python environments", validate)
    validate_block = text[validate:validate_end]
    text = text[:bootstrap] + validate_block + bootstrap_block + text[validate_end:]
    workflow.write_text(text, encoding="utf-8")
    with pytest.raises(MODULE.QualityToolchainError) as caught:
        MODULE.validate_static(root)
    assert caught.value.code == "quality_toolchain_step_order_invalid"
