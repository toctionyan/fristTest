from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOAL_GRAPH_INIT = ROOT / "services/agent-service/src/agent_core/goal_graph/__init__.py"
ATTESTATION_MODULE = ROOT / "services/agent-service/src/agent_core/goal_graph/dependency_authority.py"
POLICY_PATH = ROOT / "services/agent-service/src/agent_core/lifecycle/pretool_execution_policy.py"
TEST_PATH = ROOT / "services/agent-service/tests/runtime/test_typed_goal_dependency_authority_attestation.py"
BASELINE_PATH = ROOT / "skill-system/registry/product-source-baseline.json"


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}:expected_exactly_one_match:{count}")
    return source.replace(old, new, 1)


def apply() -> None:
    if ATTESTATION_MODULE.exists():
        raise SystemExit("dependency_authority_module_already_exists")
    if TEST_PATH.exists():
        raise SystemExit("dependency_authority_attestation_test_already_exists")

    ATTESTATION_MODULE.write_text('''from __future__ import annotations

"""Immutable evidence contract for a future dependency-authority cutover.

This module never chooses tools, blocks execution, creates permits or changes
runtime dependency authority.  It only seals the already-audited Stage2C
comparison together with the identities that a later explicit cutover would
have to prove again.
"""

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any, Iterable

DEPENDENCY_AUTHORITY_ATTESTATION_VERSION = "typed-dependency-authority-attestation@1"
DEPENDENCY_AUTHORITY_ATTESTATION_AUTHORITY = "immutable_audit_evidence_not_cutover_authority"


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


def _text(value: Any, *, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _normalized_completed_goal_ids(values: Iterable[str]) -> list[str]:
    return sorted({_text(value, limit=200) for value in values if _text(value, limit=200)})


def _shadow_integrity_errors(shadow: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    stored = _text(shadow.get("shadow_digest"), limit=128)
    if not stored:
        errors.append("DEPENDENCY_SHADOW_DIGEST_REQUIRED")
    else:
        payload = deepcopy(shadow)
        payload.pop("shadow_digest", None)
        if stored != _digest(payload):
            errors.append("DEPENDENCY_SHADOW_DIGEST_INVALID")

    if _text(shadow.get("authority"), limit=240) != "audit_only_current_dependency_enforcement_unchanged":
        errors.append("DEPENDENCY_SHADOW_AUTHORITY_INVALID")
    if bool(shadow.get("cutover_performed")):
        errors.append("DEPENDENCY_SHADOW_MUST_NOT_PERFORM_CUTOVER")
    if bool(shadow.get("changes_current_dependency_blocking")):
        errors.append("DEPENDENCY_SHADOW_MUST_NOT_CHANGE_BLOCKING")
    if bool(shadow.get("changes_allowed_capability_tools")):
        errors.append("DEPENDENCY_SHADOW_MUST_NOT_CHANGE_TOOL_SURFACE")
    if bool(shadow.get("blocks_execution")):
        errors.append("DEPENDENCY_SHADOW_MUST_NOT_BLOCK_EXECUTION")
    if bool(shadow.get("creates_permit")):
        errors.append("DEPENDENCY_SHADOW_MUST_NOT_CREATE_PERMIT")
    return errors


def build_dependency_authority_attestation(
    *,
    dependency_shadow: dict[str, Any],
    semantic_contract_id: str,
    semantic_digest: str,
    capability_registry_version: str,
    completed_goal_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """Seal Stage2C evidence without granting dependency authority."""

    shadow = deepcopy(dependency_shadow) if isinstance(dependency_shadow, dict) else {}
    completed = _normalized_completed_goal_ids(completed_goal_ids)
    evidence_errors = _shadow_integrity_errors(shadow)

    semantic_contract_id = _text(semantic_contract_id, limit=500)
    semantic_digest = _text(semantic_digest, limit=128)
    registry_version = _text(capability_registry_version, limit=300)
    graph_id = _text(shadow.get("typed_graph_id"), limit=500)
    graph_digest = _text(shadow.get("typed_graph_digest"), limit=128)
    coverage_digest = _text(shadow.get("typed_coverage_digest"), limit=128)

    if not semantic_contract_id:
        evidence_errors.append("SEMANTIC_CONTRACT_ID_REQUIRED")
    if not semantic_digest:
        evidence_errors.append("SEMANTIC_DIGEST_REQUIRED")
    if not registry_version:
        evidence_errors.append("CAPABILITY_REGISTRY_VERSION_REQUIRED")
    if not graph_id:
        evidence_errors.append("TYPED_GRAPH_ID_REQUIRED")
    if not graph_digest:
        evidence_errors.append("TYPED_GRAPH_DIGEST_REQUIRED")
    if not coverage_digest:
        evidence_errors.append("TYPED_COVERAGE_DIGEST_REQUIRED")

    source_status = _text(shadow.get("status"), limit=120)
    source_cutover_eligible = bool(shadow.get("cutover_eligible"))
    if evidence_errors:
        eligibility_status = "EVIDENCE_INVALID"
    elif source_status == "MATCHED" and source_cutover_eligible:
        eligibility_status = "ELIGIBLE_EVIDENCE_ONLY"
    else:
        eligibility_status = "NOT_ELIGIBLE"

    completion_snapshot = {
        "completed_goal_ids": completed,
        "authority": "validated_goal_lifecycle_projection",
    }
    payload: dict[str, Any] = {
        "version": DEPENDENCY_AUTHORITY_ATTESTATION_VERSION,
        "authority": DEPENDENCY_AUTHORITY_ATTESTATION_AUTHORITY,
        "immutable": True,
        "eligibility_status": eligibility_status,
        "source_dependency_shadow_status": source_status or None,
        "source_dependency_shadow_digest": shadow.get("shadow_digest"),
        "source_shadow_cutover_eligible": source_cutover_eligible,
        "semantic_contract_id": semantic_contract_id or None,
        "semantic_digest": semantic_digest or None,
        "typed_graph_id": graph_id or None,
        "typed_graph_digest": graph_digest or None,
        "typed_coverage_digest": coverage_digest or None,
        "capability_registry_version": registry_version or None,
        "completion_snapshot": completion_snapshot,
        "completion_snapshot_digest": _digest(completion_snapshot),
        "evidence_errors": sorted(set(evidence_errors)),
        "cutover_authority_granted": False,
        "cutover_performed": False,
        "changes_current_dependency_blocking": False,
        "changes_allowed_capability_tools": False,
        "blocks_execution": False,
        "creates_permit": False,
        "mutates_semantics": False,
        "mutates_business_state": False,
    }
    payload["attestation_digest"] = _digest(payload)
    return payload


def dependency_authority_attestation_integrity(attestation: dict[str, Any] | None) -> dict[str, Any]:
    row = deepcopy(attestation) if isinstance(attestation, dict) else {}
    errors: list[str] = []
    if row.get("version") != DEPENDENCY_AUTHORITY_ATTESTATION_VERSION:
        errors.append("ATTESTATION_VERSION_INVALID")
    if row.get("authority") != DEPENDENCY_AUTHORITY_ATTESTATION_AUTHORITY:
        errors.append("ATTESTATION_AUTHORITY_INVALID")
    if row.get("immutable") is not True:
        errors.append("ATTESTATION_IMMUTABLE_REQUIRED")

    stored = _text(row.get("attestation_digest"), limit=128)
    if not stored:
        errors.append("ATTESTATION_DIGEST_REQUIRED")
    else:
        payload = deepcopy(row)
        payload.pop("attestation_digest", None)
        if stored != _digest(payload):
            errors.append("ATTESTATION_DIGEST_INVALID")

    completion_snapshot = row.get("completion_snapshot") if isinstance(row.get("completion_snapshot"), dict) else {}
    expected_completion_digest = _digest(completion_snapshot)
    if _text(row.get("completion_snapshot_digest"), limit=128) != expected_completion_digest:
        errors.append("COMPLETION_SNAPSHOT_DIGEST_INVALID")

    for field in (
        "semantic_contract_id",
        "semantic_digest",
        "typed_graph_id",
        "typed_graph_digest",
        "typed_coverage_digest",
        "capability_registry_version",
        "source_dependency_shadow_digest",
    ):
        if not _text(row.get(field), limit=500):
            errors.append(f"{field.upper()}_REQUIRED")

    for field in (
        "cutover_authority_granted",
        "cutover_performed",
        "changes_current_dependency_blocking",
        "changes_allowed_capability_tools",
        "blocks_execution",
        "creates_permit",
        "mutates_semantics",
        "mutates_business_state",
    ):
        if bool(row.get(field)):
            errors.append(f"{field.upper()}_MUST_BE_FALSE")

    eligibility_status = _text(row.get("eligibility_status"), limit=120)
    if eligibility_status not in {"ELIGIBLE_EVIDENCE_ONLY", "NOT_ELIGIBLE", "EVIDENCE_INVALID"}:
        errors.append("ELIGIBILITY_STATUS_INVALID")
    if eligibility_status == "ELIGIBLE_EVIDENCE_ONLY":
        if _text(row.get("source_dependency_shadow_status"), limit=120) != "MATCHED":
            errors.append("ELIGIBLE_REQUIRES_MATCHED_SHADOW")
        if row.get("source_shadow_cutover_eligible") is not True:
            errors.append("ELIGIBLE_REQUIRES_SOURCE_ELIGIBILITY")
        if list(row.get("evidence_errors") or []):
            errors.append("ELIGIBLE_REQUIRES_NO_EVIDENCE_ERRORS")

    return {
        "ok": not errors,
        "errors": sorted(set(errors)),
        "version": row.get("version"),
        "attestation_digest": stored or None,
    }


__all__ = [
    "DEPENDENCY_AUTHORITY_ATTESTATION_AUTHORITY",
    "DEPENDENCY_AUTHORITY_ATTESTATION_VERSION",
    "build_dependency_authority_attestation",
    "dependency_authority_attestation_integrity",
]
''', encoding="utf-8")

    init_text = GOAL_GRAPH_INIT.read_text(encoding="utf-8")
    init_text = replace_once(
        init_text,
        "from .contracts import (\n",
        "from .dependency_authority import (\n"
        "    DEPENDENCY_AUTHORITY_ATTESTATION_AUTHORITY,\n"
        "    DEPENDENCY_AUTHORITY_ATTESTATION_VERSION,\n"
        "    build_dependency_authority_attestation,\n"
        "    dependency_authority_attestation_integrity,\n"
        ")\n"
        "from .contracts import (\n",
        "goal_graph_import",
    )
    init_text = replace_once(
        init_text,
        '    "TYPED_GOAL_CAPABILITY_COVERAGE_VERSION",\n',
        '    "TYPED_GOAL_CAPABILITY_COVERAGE_VERSION",\n'
        '    "DEPENDENCY_AUTHORITY_ATTESTATION_AUTHORITY",\n'
        '    "DEPENDENCY_AUTHORITY_ATTESTATION_VERSION",\n',
        "goal_graph_constants_export",
    )
    init_text = replace_once(
        init_text,
        '    "build_typed_goal_capability_coverage",\n',
        '    "build_typed_goal_capability_coverage",\n'
        '    "build_dependency_authority_attestation",\n'
        '    "dependency_authority_attestation_integrity",\n',
        "goal_graph_functions_export",
    )
    GOAL_GRAPH_INIT.write_text(init_text, encoding="utf-8")

    policy = POLICY_PATH.read_text(encoding="utf-8")
    policy = replace_once(
        policy,
        "from agent_core.kernel.capability_registry import CapabilityRegistry\n",
        "from agent_core.goal_graph.dependency_authority import build_dependency_authority_attestation\n"
        "from agent_core.kernel.capability_registry import CapabilityRegistry\n",
        "policy_import",
    )
    policy = replace_once(
        policy,
        "    if evidence_errors:\n        # Treat invalid prior progress as zero progress.",
        "    dependency_authority_attestation = (\n"
        "        build_dependency_authority_attestation(\n"
        "            dependency_shadow=dependency_authority_shadow,\n"
        "            semantic_contract_id=str(plan.get(\"formal_semantic_contract_id\") or \"\"),\n"
        "            semantic_digest=str(plan.get(\"formal_semantic_digest\") or \"\"),\n"
        "            capability_registry_version=capability_registry.version,\n"
        "            completed_goal_ids=completed_goal_ids,\n"
        "        )\n"
        "        if dependency_authority_shadow is not None\n"
        "        else None\n"
        "    )\n\n"
        "    if evidence_errors:\n        # Treat invalid prior progress as zero progress.",
        "policy_attestation_build",
    )
    policy = replace_once(
        policy,
        "    if dependency_authority_shadow is not None:\n"
        "        payload[\"typed_dependency_authority_shadow\"] = dependency_authority_shadow\n"
        "    payload[\"policy_digest\"] = _digest(payload)\n",
        "    if dependency_authority_shadow is not None:\n"
        "        payload[\"typed_dependency_authority_shadow\"] = dependency_authority_shadow\n"
        "    if dependency_authority_attestation is not None:\n"
        "        payload[\"typed_dependency_authority_attestation\"] = dependency_authority_attestation\n"
        "    payload[\"policy_digest\"] = _digest(payload)\n",
        "policy_attestation_attach",
    )
    POLICY_PATH.write_text(policy, encoding="utf-8")

    TEST_PATH.write_text('''from __future__ import annotations

from copy import deepcopy

from agent_core.goal_graph.dependency_authority import (
    build_dependency_authority_attestation,
    dependency_authority_attestation_integrity,
)
from agent_core.lifecycle.pretool_execution_policy import (
    build_pretool_execution_policy,
    execution_policy_prompt_projection,
)
from tests.runtime.test_pretool_execution_policy import _contract, _goal, _registry


_SCOPE = {
    "current_tenant_id": "tenant-1",
    "current_user_id": "u001",
    "current_thread_id": "web-u001-stage2d",
}


def _state(contract: dict, **extra) -> dict:
    return {
        "frozen_semantic_contract": contract,
        **_SCOPE,
        **extra,
    }


def test_matching_dependency_shadow_is_sealed_as_evidence_only() -> None:
    contract = _contract([
        _goal("details", domain="order", operation="query_details")
    ])
    policy = build_pretool_execution_policy(
        state=_state(contract),
        capability_registry=_registry(),
    )

    attestation = policy["typed_dependency_authority_attestation"]
    assert attestation["eligibility_status"] == "ELIGIBLE_EVIDENCE_ONLY"
    assert attestation["cutover_authority_granted"] is False
    assert attestation["cutover_performed"] is False
    assert attestation["changes_current_dependency_blocking"] is False
    assert attestation["changes_allowed_capability_tools"] is False
    assert attestation["blocks_execution"] is False
    assert attestation["creates_permit"] is False
    assert attestation["semantic_contract_id"] == contract["semantic_contract_id"]
    assert attestation["semantic_digest"] == contract["semantic_digest"]
    assert attestation["capability_registry_version"] == _registry().version
    assert dependency_authority_attestation_integrity(attestation)["ok"] is True


def test_open_dataflow_produces_not_eligible_attestation_without_changing_blocking() -> None:
    contract = _contract([
        _goal("refund", domain="refund", operation="create"),
        _goal("invoice", domain="invoice", operation="create", depends_on=("refund",)),
    ])
    policy = build_pretool_execution_policy(
        state=_state(contract, goal_records=[]),
        capability_registry=_registry(),
    )
    by_goal = {row["goal_id"]: row for row in policy["goal_policies"]}

    assert by_goal["invoice"]["status"] == "BLOCKED_BY_GOAL_DEPENDENCY"
    attestation = policy["typed_dependency_authority_attestation"]
    assert attestation["eligibility_status"] == "NOT_ELIGIBLE"
    assert attestation["source_dependency_shadow_status"] == "NOT_READY_DATAFLOW_OPEN"
    assert attestation["cutover_authority_granted"] is False
    assert dependency_authority_attestation_integrity(attestation)["ok"] is True


def test_tampered_dependency_shadow_cannot_be_sealed_as_eligible() -> None:
    contract = _contract([
        _goal("details", domain="order", operation="query_details")
    ])
    policy = build_pretool_execution_policy(
        state=_state(contract),
        capability_registry=_registry(),
    )
    shadow = deepcopy(policy["typed_dependency_authority_shadow"])
    shadow["cutover_eligible"] = False

    attestation = build_dependency_authority_attestation(
        dependency_shadow=shadow,
        semantic_contract_id=contract["semantic_contract_id"],
        semantic_digest=contract["semantic_digest"],
        capability_registry_version=_registry().version,
        completed_goal_ids=(),
    )

    assert attestation["eligibility_status"] == "EVIDENCE_INVALID"
    assert "DEPENDENCY_SHADOW_DIGEST_INVALID" in attestation["evidence_errors"]
    assert attestation["cutover_authority_granted"] is False


def test_attestation_digest_detects_post_build_tampering() -> None:
    contract = _contract([
        _goal("details", domain="order", operation="query_details")
    ])
    policy = build_pretool_execution_policy(
        state=_state(contract),
        capability_registry=_registry(),
    )
    tampered = deepcopy(policy["typed_dependency_authority_attestation"])
    tampered["typed_graph_digest"] = "0" * 64

    integrity = dependency_authority_attestation_integrity(tampered)
    assert integrity["ok"] is False
    assert "ATTESTATION_DIGEST_INVALID" in integrity["errors"]


def test_completion_snapshot_is_identity_bound_and_changes_attestation_digest() -> None:
    contract = _contract([
        _goal("details", domain="order", operation="query_details")
    ])
    policy = build_pretool_execution_policy(
        state=_state(contract),
        capability_registry=_registry(),
    )
    shadow = policy["typed_dependency_authority_shadow"]
    base = build_dependency_authority_attestation(
        dependency_shadow=shadow,
        semantic_contract_id=contract["semantic_contract_id"],
        semantic_digest=contract["semantic_digest"],
        capability_registry_version=_registry().version,
        completed_goal_ids=(),
    )
    changed = build_dependency_authority_attestation(
        dependency_shadow=shadow,
        semantic_contract_id=contract["semantic_contract_id"],
        semantic_digest=contract["semantic_digest"],
        capability_registry_version=_registry().version,
        completed_goal_ids=("details",),
    )

    assert base["completion_snapshot_digest"] != changed["completion_snapshot_digest"]
    assert base["attestation_digest"] != changed["attestation_digest"]
    assert base["cutover_authority_granted"] is False
    assert changed["cutover_authority_granted"] is False


def test_attestation_is_not_projected_into_model_prompt() -> None:
    contract = _contract([
        _goal("details", domain="order", operation="query_details")
    ])
    policy = build_pretool_execution_policy(
        state=_state(contract),
        capability_registry=_registry(),
    )
    projection = execution_policy_prompt_projection(policy)

    assert "typed_dependency_authority_attestation" in policy
    assert "typed_dependency_authority_attestation" not in projection
    assert "typed_dependency_authority_shadow" not in projection
''', encoding="utf-8")


def refresh_baseline() -> None:
    payload = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    previous = {str(k): str(v) for k, v in (payload.get("files") or {}).items()}
    if int(payload.get("file_count") or 0) != 583 or len(previous) != 583:
        raise SystemExit(
            f"unexpected_previous_baseline:{payload.get('file_count')}/{len(previous)}"
        )
    protected_roots = tuple(str(v) for v in payload.get("protected_roots") or ())
    if protected_roots != ("services", "web", "contracts"):
        raise SystemExit(f"unexpected_protected_roots:{protected_roots!r}")

    raw = subprocess.check_output(
        ["git", "ls-files", "-z", "--", *protected_roots], cwd=ROOT
    )
    tracked = sorted(item.decode("utf-8") for item in raw.split(b"\0") if item)
    current = {
        relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        for relative in tracked
    }
    if len(current) != 585:
        raise SystemExit(f"unexpected_protected_file_count:{len(current)}")

    changed = {
        path
        for path in set(current) & set(previous)
        if current[path] != previous[path]
    }
    added = set(current) - set(previous)
    removed = set(previous) - set(current)
    expected_changed = {
        "services/agent-service/src/agent_core/goal_graph/__init__.py",
        "services/agent-service/src/agent_core/lifecycle/pretool_execution_policy.py",
    }
    expected_added = {
        "services/agent-service/src/agent_core/goal_graph/dependency_authority.py",
        "services/agent-service/tests/runtime/test_typed_goal_dependency_authority_attestation.py",
    }
    if changed != expected_changed:
        raise SystemExit("unexpected_changed_existing:" + ",".join(sorted(changed)))
    if added != expected_added:
        raise SystemExit("unexpected_added:" + ",".join(sorted(added)))
    if removed:
        raise SystemExit("unexpected_removed:" + ",".join(sorted(removed)))

    code_commit = str(os.environ.get("CODE_COMMIT") or "").strip()
    if len(code_commit) != 40:
        raise SystemExit("code_commit_identity_missing")
    payload["file_count"] = len(current)
    payload["files"] = dict(sorted(current.items()))
    payload["generated_at"] = (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    payload["generated_from"] = "git:" + code_commit
    BASELINE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": "PASS",
        "file_count": len(current),
        "generated_from": payload["generated_from"],
        "changed": sorted(changed),
        "added": sorted(added),
        "removed": sorted(removed),
    }, ensure_ascii=False, indent=2))


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"apply", "refresh-baseline"}:
        raise SystemExit("usage: typed_goal_stage2d_helper.py apply|refresh-baseline")
    if sys.argv[1] == "apply":
        apply()
    else:
        refresh_baseline()


if __name__ == "__main__":
    main()
