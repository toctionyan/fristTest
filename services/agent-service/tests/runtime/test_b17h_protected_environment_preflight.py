from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts" / "protected_environment_preflight.py"


def _load_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load():
    return _load_path("protected_environment_preflight_b17h", SCRIPT)


def _toolchain():
    return _load_path("release_toolchain_contract_b17h", ROOT / "scripts" / "release_toolchain_contract.py")


def _env(**overrides: str) -> dict[str, str]:
    payload = {
        "CI": "true",
        "GITHUB_ACTIONS": "true",
        "GITHUB_REF_PROTECTED": "true",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_SHA": "a" * 40,
        "GITHUB_RUN_ID": "123456",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_REPOSITORY": "example/customer-agent",
        "REAL_MODEL_CERTIFICATION_PROVIDER": "openai",
        "OPENAI_API_BASE": "https://api.openai.com/v1",
        "OPENAI_MODEL": "gpt-5.4-mini",
        "OPENAI_API_KEY": "sk-live-" + "A" * 40,
        "EMBEDDING_PROVIDER": "openai",
        "EMBEDDING_API_BASE": "https://api.openai.com/v1",
        "EMBEDDING_MODEL": "text-embedding-3-small",
        "EMBEDDING_DIM": "1536",
        "EMBEDDING_API_KEY": "sk-embed-" + "B" * 40,
        "QUALITY_EVIDENCE_SIGNING_KEY": "S" * 40,
    }
    payload.update(overrides)
    return payload


def _prepare(monkeypatch):
    module = _load()
    monkeypatch.setattr(module.sys, "version_info", (3, 12, 13))
    monkeypatch.setattr(
        module,
        "_command_output",
        lambda command: "v24.18.0" if command[0] == "node" else "11.16.0",
    )
    return module


def test_valid_protected_environment_passes_without_emitting_credentials(monkeypatch) -> None:
    module = _prepare(monkeypatch)
    env = _env()
    result = module.validate_protected_environment(
        workspace_root=ROOT,
        env=env,
        command_lookup=lambda _name: "/usr/bin/tool",
    )
    assert result["contract"] == "protected-environment-preflight@1"
    assert result["status"] == "PASS"
    assert result["credential_values_emitted"] is False
    serialized = json.dumps(result, sort_keys=True)
    for secret_name in (
        "OPENAI_API_KEY",
        "EMBEDDING_API_KEY",
        "QUALITY_EVIDENCE_SIGNING_KEY",
    ):
        assert env[secret_name] not in serialized


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("OPENAI_API_KEY", "api_key_missing"),
        ("EMBEDDING_API_KEY", "embedding_api_key_missing"),
        ("QUALITY_EVIDENCE_SIGNING_KEY", "quality_evidence_signing_key_missing"),
    ],
)
def test_missing_protected_secret_is_an_environment_block(monkeypatch, name: str, reason: str) -> None:
    module = _prepare(monkeypatch)
    env = _env(**{name: ""})
    with pytest.raises(module.ProtectedEnvironmentPreflightError) as exc_info:
        module.validate_protected_environment(
            workspace_root=ROOT,
            env=env,
            command_lookup=lambda _name: "/usr/bin/tool",
        )
    assert exc_info.value.code == reason
    assert exc_info.value.environment_blocked is True


def test_placeholder_secret_fails_without_leaking_value(monkeypatch) -> None:
    module = _prepare(monkeypatch)
    secret = "placeholder-credential-" + "X" * 32
    with pytest.raises(module.ProtectedEnvironmentPreflightError) as exc_info:
        module.validate_protected_environment(
            workspace_root=ROOT,
            env=_env(OPENAI_API_KEY=secret),
            command_lookup=lambda _name: "/usr/bin/tool",
        )
    assert exc_info.value.code == "test_credential_forbidden"
    assert secret not in str(exc_info.value)


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"OPENAI_API_BASE": "http://api.openai.com/v1"}, "https_required"),
        ({"OPENAI_API_BASE": "https://example.com/v1"}, "unofficial_endpoint_forbidden"),
        ({"EMBEDDING_API_BASE": "https://localhost/v1"}, "protected_embedding_endpoint_local"),
        ({"EMBEDDING_DIM": "0"}, "protected_embedding_dimension_invalid"),
        ({"GITHUB_REF_PROTECTED": "false"}, "protected_environment_ref_unprotected"),
    ],
)
def test_invalid_protected_environment_fails_closed(monkeypatch, overrides: dict[str, str], reason: str) -> None:
    module = _prepare(monkeypatch)
    with pytest.raises(module.ProtectedEnvironmentPreflightError) as exc_info:
        module.validate_protected_environment(
            workspace_root=ROOT,
            env=_env(**overrides),
            command_lookup=lambda _name: "/usr/bin/tool",
        )
    assert exc_info.value.code == reason
    assert exc_info.value.environment_blocked is False


def test_deprecated_deepseek_alias_is_rejected_by_shared_identity_authority(monkeypatch) -> None:
    module = _prepare(monkeypatch)
    with pytest.raises(module.ProtectedEnvironmentPreflightError) as exc_info:
        module.validate_protected_environment(
            workspace_root=ROOT,
            env=_env(
                REAL_MODEL_CERTIFICATION_PROVIDER="deepseek",
                OPENAI_API_BASE="https://api.deepseek.com",
                OPENAI_MODEL="deepseek-chat",
            ),
            command_lookup=lambda _name: "/usr/bin/tool",
        )
    assert exc_info.value.code == "deprecated_deepseek_model_alias"
    assert exc_info.value.environment_blocked is False


def test_locked_runtime_mismatch_is_environment_blocked(monkeypatch) -> None:
    module = _load()
    monkeypatch.setattr(module.sys, "version_info", (3, 13, 5))
    with pytest.raises(module.ProtectedEnvironmentPreflightError) as exc_info:
        module.validate_protected_environment(
            workspace_root=ROOT,
            env=_env(),
            command_lookup=lambda _name: "/usr/bin/tool",
        )
    assert exc_info.value.code == "protected_environment_python_version_mismatch"
    assert exc_info.value.environment_blocked is True


def test_workflow_runs_preflight_before_expensive_dependency_install() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    preflight = workflow.index("Validate protected Environment configuration")
    install = workflow.index("Install locked Python and frontend environments")
    assert preflight < install
    assert "scripts/protected_environment_preflight.py" in workflow
    assert "protected-environment-preflight.json" in workflow
    assert "${{ secrets.PRODUCTION_MODEL_API_KEY }}" in workflow
    assert "${{ secrets.PRODUCTION_EMBEDDING_API_KEY }}" in workflow
    assert "${{ secrets.QUALITY_EVIDENCE_SIGNING_KEY }}" in workflow




def test_supply_chain_contract_locks_preflight_source_order_and_artifact() -> None:
    result = _toolchain().validate_static_contract(ROOT)
    assert result["status"] == "PASS"
    assert result["protected_environment_preflight"] == {
        "contract": "protected-environment-preflight@1",
        "job": "protected-release",
        "runs_before_dependency_install": True,
        "sanitized_failure_artifact": "protected-environment-preflight.json",
    }
    assert "scripts/protected_environment_preflight.py" in result["locked_source_sha256"]


def test_local_cli_writes_sanitized_environment_block(tmp_path: Path) -> None:
    output = tmp_path / "preflight.json"
    completed = subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "--workspace-root", str(ROOT), "--output", str(output)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 78
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "BLOCKED_BY_ENVIRONMENT"
    assert payload["reason"] == "protected_environment_ci_context_missing"
    assert payload["credential_values_emitted"] is False
