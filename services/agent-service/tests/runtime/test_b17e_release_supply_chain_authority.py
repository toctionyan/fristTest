from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]


def _load(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _toolchain():
    return _load("release_toolchain_contract_b17e", "scripts/release_toolchain_contract.py")


def _production_contract():
    return _load("production_certification_contract_b17e", "scripts/production_certification_contract.py")


def _release_controller():
    return _load("run_production_release_b17e", "scripts/run_production_release.py")


def _identity(toolchain_fingerprint: str = "d" * 64) -> dict[str, str]:
    return {
        "provider": "openai",
        "endpoint": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "credential_fingerprint_sha256_16": "0123456789abcdef",
        "toolchain_fingerprint_sha256": toolchain_fingerprint,
        "run_identity_fingerprint_sha256": "f" * 64,
    }


def _release_summary(*, production_toolchain: str = "d" * 64, real_model_toolchain: str | None = None) -> dict:
    real_model_toolchain = real_model_toolchain or production_toolchain
    identity = _identity(production_toolchain)
    return {
        "mode": "release",
        "decision": "PASS",
        "loop_status": "CI_VERIFIED",
        "completion_eligible": True,
        "missing_prerequisites": [],
        "unverified_claim_ids": [],
        "workspace_snapshot_fingerprint": "a" * 64,
        "ci_run_identity_fingerprint_sha256": "f" * 64,
        "quality_dimensions": {
            "production_certification": {
                "status": "PASS",
                "contract": "production-certification-dimension@1",
                "session_id": "prodcert-" + "b" * 48,
                "workspace_fingerprint_sha256": "c" * 64,
                "toolchain_fingerprint_sha256": production_toolchain,
                "real_model_identity": identity,
            },
            "real_model_certification": {
                "status": "PASS",
                "contract": "real-model-certification-dimension@3",
                "bundle_contract": "production-certification-bundle@1",
                "session_id": "prodcert-" + "b" * 48,
                "workspace_fingerprint_sha256": "c" * 64,
                "toolchain_fingerprint_sha256": real_model_toolchain,
                "identity": identity,
            },
        },
    }


def test_release_static_supply_chain_contract_passes() -> None:
    contract = _toolchain()
    result = contract.validate_static_contract(ROOT)
    assert result["status"] == "PASS"
    assert result["python_version"] == "3.12.13"
    assert result["node_version"] == "24.18.0"
    assert result["npm_version"] == "11.16.0"
    assert result["uv_version"] == "0.11.29"
    assert set(result["action_pins"]) == {
        "actions/checkout",
        "actions/setup-python",
        "actions/setup-node",
        "actions/upload-artifact",
    }
    assert all(len(value) == 40 for value in result["action_pins"].values())
    assert result["postgres_image"] == "pgvector/pgvector@sha256:1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb"


def test_release_workflow_contains_no_mutable_remote_action_reference() -> None:
    contract = _toolchain()
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    rows = contract._workflow_actions(workflow)
    assert rows
    assert all(__import__("re").fullmatch(r"[0-9a-f]{40}", ref) for _, ref, _ in rows)
    assert "actions/checkout@v" not in workflow
    assert "actions/setup-python@v" not in workflow
    assert "actions/setup-node@v" not in workflow
    assert "actions/upload-artifact@v" not in workflow


def test_mutable_action_reference_is_a_red_counterexample(tmp_path: Path) -> None:
    contract = _toolchain()
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    rows = contract._workflow_actions(workflow.replace(
        "actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd",
        "actions/checkout@v6",
    ))
    mutable = [(name, ref) for name, ref, _ in rows if not __import__("re").fullmatch(r"[0-9a-f]{40}", ref)]
    assert mutable
    assert set(mutable) == {("actions/checkout", "v6")}
    assert len(mutable) == sum(1 for name, _, _ in rows if name == "actions/checkout")


def test_uv_bootstrap_is_exact_and_hash_locked() -> None:
    lock = (ROOT / "deployment/ci/uv-requirements-linux-x86_64.txt").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "uv==0.11.29" in lock
    assert "sha256:eec03a8b63d55915694db3af4e91324b39ced49e2aeac7af37851c7eb3f470ea" in lock
    assert "pip install --disable-pip-version-check --require-hashes --only-binary=:all:" in workflow
    assert "pip install uv" not in workflow
    evidence_upload = workflow.split("- name: Upload signed production evidence", 1)[1].split("- name: Upload production closed artifacts", 1)[0]
    assert "include-hidden-files: true" in evidence_upload


def test_toolchain_provenance_tamper_is_rejected(tmp_path: Path) -> None:
    contract = _toolchain()
    run_identity = {"contract": "release-run-identity@1", "status": "PASS"}
    run_identity["run_identity_fingerprint_sha256"] = contract._canonical_sha256(run_identity)
    payload = {
        "contract": contract.CONTRACT,
        "status": "PASS",
        "ci_run_identity": run_identity,
        "runner": "ubuntu-24.04",
        "versions": {"python": "3.12.13", "node": "24.18.0", "npm": "11.16.0", "uv": "0.11.29"},
        "executables": {},
        "static_contract": contract.validate_static_contract(ROOT),
        "python_environments": {},
        "frontend_environment": {},
    }
    payload["toolchain_fingerprint_sha256"] = contract._canonical_sha256(payload)
    evidence = tmp_path / "toolchain.json"
    evidence.write_text(json.dumps(payload), encoding="utf-8")
    contract.validate_runtime_evidence(ROOT, evidence, expected_fingerprint=payload["toolchain_fingerprint_sha256"], validate_live_runtime=False)

    payload["versions"]["node"] = "24.18.1"
    evidence.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(Exception, match="fingerprint is invalid"):
        contract.validate_runtime_evidence(ROOT, evidence, expected_fingerprint=payload["toolchain_fingerprint_sha256"], validate_live_runtime=False)


def test_production_session_requires_locked_toolchain_fingerprint() -> None:
    contract = _production_contract()
    env = {
        contract.SESSION_ENV: "prodcert-" + "1" * 48,
        contract.WORKSPACE_ENV: "2" * 64,
        contract.STARTED_ENV: datetime.now(timezone.utc).isoformat(),
        contract.COMPONENT_ENV: "real_model",
    }
    with pytest.raises(Exception, match="all production certification session variables"):
        contract.production_session_from_environment(component="real_model", env=env)


def test_component_from_another_toolchain_cannot_join_bundle() -> None:
    contract = _production_contract()
    session = {
        "contract": contract.SESSION_CONTRACT,
        "mode": "bundle",
        "session_id": "prodcert-" + "1" * 48,
        "workspace_fingerprint_sha256": "2" * 64,
        "toolchain_fingerprint_sha256": "3" * 64,
        "component": "real_model",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "emitted_at": datetime.now(timezone.utc).isoformat(),
    }
    with pytest.raises(Exception, match="another release toolchain"):
        contract._validate_session(
            {"production_session": session},
            component="real_model",
            session_id=session["session_id"],
            workspace_fingerprint_sha256=session["workspace_fingerprint_sha256"],
            toolchain_fingerprint_sha256="4" * 64,
            now=datetime.now(timezone.utc),
        )


def test_release_summary_rejects_cross_toolchain_composition() -> None:
    controller = _release_controller()
    with pytest.raises(Exception, match="did not converge"):
        controller.validate_release_summary(
            _release_summary(production_toolchain="d" * 64, real_model_toolchain="e" * 64),
            expected_identity=_identity("d" * 64),
        )


def test_protected_postgres_image_is_immutable_and_digest_locked() -> None:
    contract = _toolchain()
    lock = json.loads((ROOT / "deployment/ci/release-toolchain-lock.json").read_text(encoding="utf-8"))
    managed = (ROOT / "scripts/run_managed_quality_integration.py").read_text(encoding="utf-8")
    image = str(lock["postgres_image"])
    assert __import__("re").fullmatch(r"pgvector/pgvector@sha256:[0-9a-f]{64}", image)
    assert f'DEFAULT_POSTGRES_IMAGE = "{image}"' in managed
    assert "pgvector/pgvector:pg16" not in managed
    assert "pgvector/pgvector:latest" not in managed
    assert contract.validate_static_contract(ROOT)["postgres_image"] == image


def test_mutable_postgres_image_reference_is_a_red_counterexample(tmp_path: Path) -> None:
    del tmp_path
    contract = _toolchain()
    with pytest.raises(Exception, match="pinned by manifest digest"):
        contract._validate_postgres_image_reference("pgvector/pgvector:pg16")


def test_postgres_and_browser_from_different_container_images_cannot_form_bundle() -> None:
    contract = _production_contract()
    b17a = _load(
        "test_b17a_production_certification_authority_for_b17e",
        "services/agent-service/tests/runtime/test_b17a_production_certification_authority.py",
    )
    now = datetime.now(timezone.utc)
    session_id = "prodcert-" + "8" * 48
    workspace_fingerprint = "7" * 64
    toolchain_fingerprint = "6" * 64
    base_env = {
        "PRODUCTION_CERTIFICATION_SESSION_ID": session_id,
        "PRODUCTION_CERTIFICATION_WORKSPACE_FINGERPRINT": workspace_fingerprint,
        "PRODUCTION_CERTIFICATION_SESSION_STARTED_AT": now.isoformat(),
        "PRODUCTION_CERTIFICATION_TOOLCHAIN_FINGERPRINT": toolchain_fingerprint,
    }
    components = {
        name: b17a._component_payload(name, {**base_env, "PRODUCTION_CERTIFICATION_COMPONENT": name})
        for name in ("real_model", "postgres", "browser")
    }
    components["browser"]["container_image_id_sha256"] = "sha256:" + "5" * 64
    with pytest.raises(Exception, match="same immutable PostgreSQL image"):
        contract.validate_production_components(
            components=components,
            session_id=session_id,
            workspace_fingerprint_sha256=workspace_fingerprint,
            toolchain_fingerprint_sha256=toolchain_fingerprint,
            started_at=now,
            completed_workspace_fingerprint_sha256=workspace_fingerprint,
        )
