from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]


def _load_runner():
    path = ROOT / "scripts" / "run_production_release.py"
    spec = importlib.util.spec_from_file_location("run_production_release_b17b", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_workflow_uses_one_real_production_execution_entrypoint() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "scripts/run_production_release.py" in workflow
    assert "PRODUCTION_MODEL_API_KEY" in workflow
    assert "QUALITY_EVIDENCE_SIGNING_KEY" in workflow
    assert "playwright install --with-deps chromium" in workflow
    assert "deterministic-ci-key" not in workflow
    assert "tests.integration.model_stub" not in workflow
    assert "--certification-level protected-release" not in workflow


def test_deterministic_integration_workflow_cannot_build_release_artifacts() -> None:
    diagnostic = ROOT / ".github" / "workflows" / "integration-diagnostic.yml"
    assert diagnostic.is_file()
    text = diagnostic.read_text(encoding="utf-8")
    assert "deterministic-ci-key" in text
    assert "--mode integration" in text
    assert "--mode release" not in text
    assert "build_clean_release.py" not in text
    assert "protected-release" not in text


def test_release_ci_claims_use_production_bundle_not_legacy_independent_gates() -> None:
    module_path = ROOT / "scripts" / "create_ci_quality_target.py"
    spec = importlib.util.spec_from_file_location("create_ci_quality_target_b17b", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    gates = set(module.WORKFLOW_CLAIM_GATES["release-quality"])
    assert "production-certification-bundle" in gates
    assert "clean-release-preflight" in gates
    assert "preproduction-real-model-certification-bundle" not in gates
    assert "configured-model-browser-conversation" not in gates
    assert "configured-model-browser-campaign" not in gates

    manifest = json.loads(
        (ROOT / "governance" / "claims" / "v20.6.2-project-release-certification.json").read_text(
            encoding="utf-8"
        )
    )
    required = {
        gate
        for claim in manifest["claims"]
        for gate in claim.get("required_gates", [])
    }
    assert "production-certification-bundle" in required
    assert "preproduction-real-model-certification-bundle" not in required
    assert "configured-model-browser-conversation" not in required
    assert "configured-model-browser-campaign" not in required


def test_release_runner_rejects_non_pass_release_summary() -> None:
    runner = _load_runner()
    with pytest.raises(Exception, match="release quality loop did not converge"):
        runner.validate_release_summary(
            {
                "mode": "release",
                "decision": "BLOCKED_BY_ENVIRONMENT",
                "loop_status": "TARGETED_REGRESSION_BLOCKED",
                "completion_eligible": False,
                "quality_dimensions": {
                    "production_certification": {"status": "BLOCKED_BY_ENVIRONMENT"}
                },
            }
        )


def test_release_runner_plan_never_exposes_credentials(tmp_path: Path) -> None:
    runner = _load_runner()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "target.md"
    target.write_text("target\n", encoding="utf-8")
    agent_python = workspace / "services" / "agent-service" / ".venv" / "bin" / "python"
    agent_python.parent.mkdir(parents=True)
    agent_python.write_text("#!/bin/sh\n", encoding="utf-8")
    agent_python.chmod(0o755)
    plan = runner.build_release_plan(
        workspace_root=workspace,
        target_path=target,
        evidence_dir=tmp_path / "evidence",
        output_dir=tmp_path / "output",
        artifact_name="customer_agent_workspace_v20_17_production_closed",
        python_executable=agent_python,
    )
    serialized = json.dumps(plan, sort_keys=True)
    assert "OPENAI_API_KEY" not in serialized
    assert "QUALITY_EVIDENCE_SIGNING_KEY" not in serialized
    assert "production-certification-bundle" in serialized
    assert "protected-release" in serialized


def test_release_runner_requires_immutable_ci_and_official_model(monkeypatch, tmp_path: Path) -> None:
    runner = _load_runner()
    env: dict[str, str] = {
        "OPENAI_API_KEY": "sk-real-looking-value-12345678901234567890",
        "OPENAI_MODEL": "gpt-4o-mini",
        "OPENAI_API_BASE": "https://api.openai.com/v1",
        "REAL_MODEL_CERTIFICATION_PROVIDER": "openai",
        "QUALITY_EVIDENCE_SIGNING_KEY": "x" * 40,
    }
    monkeypatch.setattr(runner.shutil, "which", lambda name: f"/usr/bin/{name}")
    with pytest.raises(Exception, match="GITHUB_SHA"):
        runner.validate_execution_environment(env=env, require_ci=True)
