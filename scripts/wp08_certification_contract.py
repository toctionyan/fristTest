#!/usr/bin/env python3
"""Static contract for the WP-08 resumable full-stack certification assets."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

CONTRACT = "wp08-certification-static-contract@1"
PROFILE_CONTRACT = "wp08-production-profile@1"
PROFILE_RELATIVE_PATH = Path("deployment/ci/wp08-production-profile.json")


class WP08ContractError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _load(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WP08ContractError("wp08_json_invalid", f"invalid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise WP08ContractError("wp08_json_invalid", f"JSON object required: {path}")
    return payload


def _validate_production_profile(profile: dict[str, Any]) -> None:
    if profile.get("contract") != PROFILE_CONTRACT:
        raise WP08ContractError("wp08_production_profile_contract_invalid", "WP-08 production profile contract is invalid")
    if profile.get("production_closed") is not False:
        raise WP08ContractError("wp08_production_profile_claim_forbidden", "WP-08 production profile cannot close production")

    forbidden_tokens = ("secret", "api_key", "token", "password", "credential")
    forbidden_fields = [
        str(key) for key in profile
        if any(token in str(key).casefold() for token in forbidden_tokens)
    ]
    if forbidden_fields:
        raise WP08ContractError(
            "wp08_production_profile_secret_forbidden",
            "WP-08 production profile must not contain secret material fields: " + ", ".join(sorted(forbidden_fields)),
        )

    provider = str(profile.get("model_provider") or "").strip().casefold()
    model_id = str(profile.get("model_id") or "").strip()
    model_api_base = str(profile.get("model_api_base") or "").strip().rstrip("/")
    embedding_provider = str(profile.get("embedding_provider") or "").strip().casefold()
    embedding_model = str(profile.get("embedding_model") or "").strip()
    embedding_api_base = str(profile.get("embedding_api_base") or "").strip().rstrip("/")

    if provider == "openai":
        if model_api_base != "https://api.openai.com/v1":
            raise WP08ContractError("wp08_production_profile_model_base_invalid", "OpenAI production profile must use the official API base")
    elif provider == "deepseek":
        if model_api_base not in {"https://api.deepseek.com", "https://api.deepseek.com/v1"}:
            raise WP08ContractError("wp08_production_profile_model_base_invalid", "DeepSeek production profile must use the official API base")
    else:
        raise WP08ContractError("wp08_production_profile_provider_invalid", "production profile provider must be openai or deepseek")

    if not model_id:
        raise WP08ContractError("wp08_production_profile_model_invalid", "production profile model ID is empty")
    if not embedding_provider or not embedding_model:
        raise WP08ContractError("wp08_production_profile_embedding_invalid", "production profile embedding provider/model is empty")
    if not embedding_api_base.startswith("https://"):
        raise WP08ContractError("wp08_production_profile_embedding_base_invalid", "production profile embedding API base must use HTTPS")

    dimension = profile.get("embedding_dimension")
    batch_size = profile.get("embedding_batch_size")
    if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension < 1:
        raise WP08ContractError("wp08_production_profile_embedding_dimension_invalid", "production profile embedding dimension must be a positive integer")
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
        raise WP08ContractError("wp08_production_profile_embedding_batch_invalid", "production profile embedding batch size must be a positive integer")


def validate_static(workspace_root: Path) -> dict[str, Any]:
    root = Path(workspace_root).resolve()
    lock = _load(root / "deployment" / "ci" / "release-toolchain-lock.json")
    config = _load(root / "deployment" / "ci" / "wp08-certification-batches.json")
    profile_path = root / PROFILE_RELATIVE_PATH
    workflow_path = root / ".github" / "workflows" / "wp08-certification.yml"
    runner_path = root / "scripts" / "run_wp08_certification.py"
    resume_path = root / "scripts" / "prepare_wp08_resume.py"
    if not workflow_path.is_file() or not runner_path.is_file() or not resume_path.is_file() or not profile_path.is_file():
        raise WP08ContractError("wp08_asset_missing", "WP-08 workflow, production profile, runner or resume validator is missing")

    profile = _load(profile_path)
    _validate_production_profile(profile)
    text = workflow_path.read_text(encoding="utf-8")

    required_fragments = [
        "name: wp08-full-stack-certification",
        "workflow_dispatch:",
        "runs-on: ubuntu-24.04",
        "environment: production-certification",
        "timeout-minutes: 360",
        "python-version: '3.12.13'",
        "node-version: '24.18.0'",
        "--require-hashes --only-binary=:all:",
        "scripts/release_toolchain_contract.py",
        "Capture locked release toolchain provenance",
        "Resolve versioned production profile and protected environment overrides",
        "deployment/ci/wp08-production-profile.json",
        "WP08_PRODUCTION_PROFILE",
        "vars.REAL_MODEL_CERTIFICATION_PROVIDER",
        "vars.OPENAI_MODEL",
        "vars.OPENAI_API_BASE",
        "vars.EMBEDDING_PROVIDER",
        "vars.EMBEDDING_MODEL",
        "vars.EMBEDDING_DIM",
        "vars.EMBEDDING_API_BASE",
        "vars.EMBEDDING_BATCH_SIZE",
        "EMBEDDING_BATCH_SIZE",
        "secrets.PRODUCTION_MODEL_API_KEY",
        "secrets.PRODUCTION_EMBEDDING_API_KEY",
        "secrets.QUALITY_EVIDENCE_SIGNING_KEY",
        "wp08-environment-config.json",
        "versioned-production-profile-with-protected-environment-overrides",
        "scripts/run_wp08_certification.py",
        "deployment/ci/wp08-certification-batches.json",
        "if: always()",
        "persist-credentials: false",
        "actions: read",
        "vars.WP08_RESUME_RUN_ID",
        "vars.WP08_RESUME_RUN_ATTEMPT",
        "scripts/prepare_wp08_resume.py",
        "--resume",
        "continue-on-error: true",
        "wp08-resume-provenance.json",
        "github.ref_protected == true",
        "github.ref == 'refs/heads/main'",
        "PRODUCTION_RELEASE_EXPECTED_WORKFLOW_FILE: .github/workflows/wp08-certification.yml",
        "PRODUCTION_RELEASE_EXPECTED_WORKFLOW: wp08-full-stack-certification",
        "PRODUCTION_RELEASE_EXPECTED_JOB: certify",
    ]
    missing = [fragment for fragment in required_fragments if fragment not in text]
    if missing:
        raise WP08ContractError("wp08_workflow_contract_missing", "missing workflow fragments: " + ", ".join(missing))

    if "inputs." in text or "\n    inputs:\n" in text:
        raise WP08ContractError(
            "wp08_dispatch_inputs_forbidden",
            "WP-08 runtime configuration must not come from manual dispatch inputs",
        )

    actions = lock.get("github_actions") if isinstance(lock.get("github_actions"), dict) else {}
    for name in (
        "actions/checkout",
        "actions/setup-python",
        "actions/setup-node",
        "actions/upload-artifact",
    ):
        row = actions.get(name) if isinstance(actions.get(name), dict) else {}
        sha = str(row.get("sha") or "")
        if not sha or f"{name}@{sha}" not in text:
            raise WP08ContractError("wp08_action_not_sha_pinned", f"{name} is not pinned to release authority")

    wp08_lock = lock.get("wp08_certification") if isinstance(lock.get("wp08_certification"), dict) else {}
    resume_action = wp08_lock.get("resume_action") if isinstance(wp08_lock.get("resume_action"), dict) else {}
    run_identity = wp08_lock.get("run_identity") if isinstance(wp08_lock.get("run_identity"), dict) else {}
    resume_name = str(resume_action.get("name") or "")
    resume_sha = str(resume_action.get("sha") or "")
    resume_version = str(resume_action.get("version") or "")
    if (
        wp08_lock.get("contract") != "wp08-certification-toolchain@1"
        or wp08_lock.get("cross_run_resume") is not True
        or wp08_lock.get("same_repository") is not True
        or wp08_lock.get("same_commit") is not True
        or resume_name != "actions/download-artifact"
        or not resume_sha
        or f"{resume_name}@{resume_sha}" not in text
        or resume_version not in text
        or run_identity.get("event_name") != "workflow_dispatch"
        or run_identity.get("workflow_name") != "wp08-full-stack-certification"
        or run_identity.get("workflow_file") != ".github/workflows/wp08-certification.yml"
        or run_identity.get("job") != "certify"
        or run_identity.get("ref") != "refs/heads/main"
        or run_identity.get("require_protected_ref") is not True
    ):
        raise WP08ContractError("wp08_resume_action_unlocked", "WP-08 resume action is not locked to the cross-run authority")

    for key, fragment in (
        ("python_version", "python-version: '"),
        ("node_version", "node-version: '"),
    ):
        value = str(lock.get(key) or "")
        if f"{fragment}{value}'" not in text:
            raise WP08ContractError("wp08_toolchain_drift", f"workflow does not use locked {key}")

    if "production_closed=true" in text.casefold() or '"production_closed": true' in text.casefold():
        raise WP08ContractError("wp08_production_claim_forbidden", "WP-08 diagnostics cannot close production")

    if config.get("contract") != "wp08-certification-batches@1":
        raise WP08ContractError("wp08_batch_contract_invalid", "batch config contract is invalid")
    rows = config.get("batches") if isinstance(config.get("batches"), list) else []
    by_id = {str(row.get("id") or ""): row for row in rows if isinstance(row, dict)}
    expected = {
        "protected-environment-preflight": None,
        "postgres-pgvector-recovery": "postgres",
        "real-model-rag": "real_model",
        "browser-full-stack": "browser",
    }
    if set(by_id) != set(expected):
        raise WP08ContractError("wp08_batch_set_invalid", "WP-08 batch set is incomplete or contains unknown batches")
    for batch_id, component in expected.items():
        row = by_id[batch_id]
        if int(row.get("timeout_seconds") or 0) < 1:
            raise WP08ContractError("wp08_batch_timeout_invalid", f"{batch_id} has no bounded timeout")
        if bool(row.get("required", True)) is not True:
            raise WP08ContractError("wp08_batch_optional_forbidden", f"{batch_id} must remain required")
        actual = row.get("production_component")
        if actual != component:
            raise WP08ContractError("wp08_component_mapping_invalid", f"{batch_id} component mapping is invalid")

    return {
        "contract": CONTRACT,
        "status": "PASS",
        "workflow": workflow_path.relative_to(root).as_posix(),
        "production_profile": profile_path.relative_to(root).as_posix(),
        "runner": runner_path.relative_to(root).as_posix(),
        "resume_validator": resume_path.relative_to(root).as_posix(),
        "cross_run_resume": True,
        "configuration_authority": "versioned-production-profile-with-protected-environment-overrides",
        "environment_overrides": True,
        "dispatch_inputs": False,
        "batch_ids": list(expected),
        "python_version": lock.get("python_version"),
        "node_version": lock.get("node_version"),
        "uv_version": lock.get("uv_version"),
        "production_closed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", default=".")
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        result = validate_static(Path(args.workspace_root))
    except WP08ContractError as exc:
        result = {"contract": CONTRACT, "status": "FAIL", "reason": exc.code, "error": str(exc), "production_closed": False}
    if args.output:
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
