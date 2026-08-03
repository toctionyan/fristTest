from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import Any

try:
    from .models import ChangeContract, TargetKind
    from .product_scope import PRODUCT_PROFILES, validate_product_scope
except ImportError:  # direct script/test loading
    from models import ChangeContract, TargetKind  # type: ignore
    from product_scope import PRODUCT_PROFILES, validate_product_scope  # type: ignore

ACTIVE_CONTRACT = Path("governance/active-change.json")
SKILL_ONLY_ALLOWED = (
    "architecture-skill/**",
    "skill-system/**",
    "governance/**",
    "scripts/**",
    ".agents/**",
    ".claude/**",
    ".codex/**",
    ".github/workflows/quality.yml",
    ".github/workflows/integration-diagnostic.yml",
    "AGENTS.md",
    "CLAUDE.md",
    "README.md",
    "CHANGELOG.md",
    "PHASE_CANDIDATE_NOTICE.md",
    "PHASE_CANDIDATE_MANIFEST.json",
    "B18_STAGE_SUMMARY.json",
    "Makefile",
    "skillctl.py",
    "release/**",
)
SKILL_ONLY_FORBIDDEN = ("services/**", "web/**", "contracts/**")
PRODUCT_CONTROL_FORBIDDEN = (
    "skill-system/**",
    "architecture-skill/**",
    "governance/quality-loop-policy.json",
    "governance/evidence/**",
    "governance/repair-cases/**",
    ".quality/**",
    ".agents/**",
    ".claude/**",
    ".codex/**",
    "AGENTS.md",
    "CLAUDE.md",
)
REQUIRED_PROFILES = {
    "skill-only": (
        "skill-static",
        "skill-unit",
        "skill-host-integration",
        "skill-security",
        "project-compatibility-smoke",
    ),
    "certification": ("skill-release",),
}
WRITER_ROLES = {"none", "skill-implementer", "product-implementer"}
READ_ONLY_TARGETS = {"diagnosis", "design", "oracle-review", "certification"}
TRANSITION_TARGETS = {"repair", "migration", "revert"}


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object required: {path}")
    return payload


def _unsafe_pattern(value: str) -> bool:
    normalized = value.strip().replace("\\", "/")
    return not normalized or normalized in {"*", "**", "./**", "."} or normalized.startswith("../")


def validate_contract_payload(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required = {
        "schema_version", "change_id", "target_kind", "goal", "profile",
        "allowed_paths", "forbidden_paths", "invariants", "required_profiles",
        "writer_role", "review_roles", "review_attestations", "status",
    }
    missing = sorted(required.difference(payload))
    if missing:
        errors.append("missing_fields:" + ",".join(missing))
        return errors
    if payload.get("schema_version") != 1:
        errors.append("schema_version_must_be_1")
    try:
        target_kind = TargetKind(str(payload.get("target_kind"))).value
    except ValueError:
        target_kind = ""
        errors.append("invalid_target_kind")
    profile = str(payload.get("profile") or "")
    writer_role = str(payload.get("writer_role") or "")
    if writer_role not in WRITER_ROLES:
        errors.append("invalid_writer_role")
    if target_kind in READ_ONLY_TARGETS and writer_role != "none":
        errors.append("read_only_target_writer_role_must_be_none")
    if target_kind in TRANSITION_TARGETS:
        expected = "skill-implementer" if profile == "skill-only" else "product-implementer"
        if writer_role != expected:
            errors.append(f"transition_writer_role_must_be:{expected}")
    if payload.get("status") not in {"draft", "approved", "implementing", "review", "verified", "closed", "rejected"}:
        errors.append("invalid_status")
    attestations = payload.get("review_attestations")
    if not isinstance(attestations, list):
        errors.append("review_attestations_must_be_array")
    else:
        allowed_roles = {str(v) for v in payload.get("review_roles") or []}
        for row in attestations:
            if not isinstance(row, dict) or str(row.get("role") or "") not in allowed_roles:
                errors.append("invalid_review_attestation")
                break
            if str(row.get("decision") or "") not in {"PASS", "REJECT"}:
                errors.append("invalid_review_decision")
                break
    allowed = [str(v) for v in payload.get("allowed_paths") or []]
    forbidden = [str(v) for v in payload.get("forbidden_paths") or []]
    if not allowed or any(_unsafe_pattern(v) for v in allowed):
        errors.append("unsafe_or_empty_allowed_paths")
    if any(_unsafe_pattern(v) for v in forbidden):
        errors.append("unsafe_forbidden_paths")
    actual_profiles = {str(v) for v in payload.get("required_profiles") or []}
    if not actual_profiles:
        errors.append("required_profiles_empty")
    if profile == "skill-only":
        for required_path in SKILL_ONLY_FORBIDDEN:
            if required_path not in forbidden:
                errors.append(f"skill_only_missing_forbidden:{required_path}")
        for value in allowed:
            if any(fnmatch.fnmatchcase(value, blocked) or fnmatch.fnmatchcase(blocked, value) for blocked in SKILL_ONLY_FORBIDDEN):
                errors.append(f"skill_only_allows_product_path:{value}")
        missing_profiles = set(REQUIRED_PROFILES["skill-only"]).difference(actual_profiles)
        if missing_profiles:
            errors.append("skill_only_missing_profiles:" + ",".join(sorted(missing_profiles)))
    elif profile in PRODUCT_PROFILES:
        errors.extend(validate_product_scope(
            profile=profile,
            target_kind=target_kind,
            allowed_paths=allowed,
            forbidden_paths=forbidden,
            minimum_mode=str(payload.get("minimum_quality_mode") or ""),
        ))
        for field in ("affected_modules", "initial_source_fingerprint", "initial_source_file_count"):
            if field not in payload:
                errors.append(f"product_contract_missing:{field}")
        if target_kind in TRANSITION_TARGETS | {"certification"} and not str(payload.get("quality_target") or "").strip():
            errors.append("product_contract_missing_quality_target")
        if target_kind in TRANSITION_TARGETS and not str(payload.get("baseline_evidence") or "").strip() and payload.get("status") in {"implementing", "review", "verified", "closed"}:
            errors.append("product_transition_missing_baseline_evidence")
    elif profile == "certification":
        missing_profiles = set(REQUIRED_PROFILES["certification"]).difference(actual_profiles)
        if missing_profiles:
            errors.append("certification_missing_profiles:" + ",".join(sorted(missing_profiles)))
    else:
        errors.append(f"invalid_profile:{profile}")

    repair_governance = payload.get("repair_governance")
    if repair_governance is not None and (not isinstance(repair_governance, str) or not repair_governance.strip()):
        errors.append("repair_governance_must_be_relative_path")
    if target_kind in TRANSITION_TARGETS and payload.get("status") in {"implementing", "review", "verified", "closed"}:
        if not isinstance(repair_governance, str) or not repair_governance.strip():
            errors.append("transition_missing_repair_governance")
    consumed_at = payload.get("repair_governance_consumed_at")
    if consumed_at is not None and (not isinstance(consumed_at, str) or not consumed_at.strip()):
        errors.append("repair_governance_consumed_at_must_be_string")

    multi_agent_mode = payload.get("multi_agent_mode")
    if multi_agent_mode is not None and multi_agent_mode not in {"required", "not-applicable", "legacy"}:
        errors.append("invalid_multi_agent_mode")
    if (
        multi_agent_mode is not None
        and profile in PRODUCT_PROFILES
        and target_kind in TRANSITION_TARGETS
        and multi_agent_mode != "required"
    ):
        errors.append("product_transition_multi_agent_mode_must_be_required")

    delta = str(payload.get("architecture_policy_delta") or "").strip()
    if delta:
        if target_kind not in {"migration", "revert"}:
            errors.append("architecture_policy_delta_requires_migration_or_revert")
        if not str(payload.get("decision_record") or "").strip():
            errors.append("architecture_policy_delta_requires_decision_record")
        if not str(payload.get("baseline_policy_id") or "").strip():
            errors.append("architecture_policy_delta_requires_baseline_policy_id")
    return errors


def load_contract(workspace: Path, path: Path | None = None, *, require_approved: bool = True) -> ChangeContract:
    contract_path = (path or workspace / ACTIVE_CONTRACT).resolve()
    try:
        contract_path.relative_to(workspace.resolve())
    except ValueError as exc:
        raise ValueError("change contract must be inside the workspace") from exc
    if not contract_path.is_file():
        raise ValueError(f"active change contract does not exist: {contract_path}")
    payload = load_json(contract_path)
    errors = validate_contract_payload(payload)
    if errors:
        raise ValueError("invalid change contract: " + "; ".join(errors))
    if require_approved and payload.get("status") not in {"approved", "implementing", "review", "verified"}:
        raise ValueError(f"change contract is not approved for writes: {payload.get('status')}")
    return ChangeContract(contract_path, payload)
