from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

BASE_POLICY = Path("governance/architecture-policy.json")
ACTIVE_CONTRACT = Path("governance/active-change.json")

_LIST_OPERATIONS = {
    "required_workspace_paths": ("add_required_workspace_paths", "retire_required_workspace_paths"),
    "forbidden_paths": ("add_forbidden_paths", "retire_forbidden_paths"),
    "allowed_core_dirs": ("add_allowed_core_dirs", "retire_allowed_core_dirs"),
    "allowed_core_root_modules": (
        "add_allowed_core_root_modules",
        "retire_allowed_core_root_modules",
    ),
}
_OWNER_FIELDS = {"single_graph_update_owner", "composition_dir"}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object required: {path}")
    return payload


def _normalise_path(value: object) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    while raw.startswith("./"):
        raw = raw[2:]
    if not raw or raw in {".", "*", "**"} or raw.startswith("../") or "/../" in raw:
        raise ValueError(f"unsafe architecture policy path: {value!r}")
    return raw.rstrip("/")


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def validate_baseline(policy: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if policy.get("schema_version") != 2:
        errors.append("architecture_policy_schema_version_must_be_2")
    if policy.get("policy_kind") != "project-architecture-baseline":
        errors.append("architecture_policy_kind_invalid")
    for field in ("policy_id", "project_version", "required_workspace_paths", "forbidden_paths", "allowed_core_dirs"):
        if field not in policy:
            errors.append(f"architecture_policy_missing:{field}")
    protected = set(policy.get("non_variance_fields") or [])
    required_protected = {
        "policy_kind",
        "project_version",
        "source_roots",
        "runtime_roots",
        "configuration",
        "banned_universal_tools",
    }
    if not required_protected.issubset(protected):
        errors.append("architecture_policy_non_variance_fields_incomplete")
    return errors


def validate_delta(delta: dict[str, Any], *, base_policy: dict[str, Any], contract: dict[str, Any] | None = None) -> list[str]:
    errors: list[str] = []
    required = {
        "schema_version",
        "delta_id",
        "change_id",
        "base_policy_id",
        "rationale",
        "operations",
        "cutover",
        "required_evidence",
        "rollback_plan",
        "expiry_or_review_date",
        "status",
    }
    missing = sorted(required.difference(delta))
    if missing:
        return ["architecture_delta_missing:" + ",".join(missing)]
    if delta.get("schema_version") != 1:
        errors.append("architecture_delta_schema_version_must_be_1")
    if delta.get("base_policy_id") != base_policy.get("policy_id"):
        errors.append("architecture_delta_base_policy_mismatch")
    if delta.get("status") not in {"approved", "applied", "retired"}:
        errors.append("architecture_delta_status_invalid")
    if contract is not None:
        if delta.get("change_id") != contract.get("change_id"):
            errors.append("architecture_delta_change_id_mismatch")
        if contract.get("target_kind") not in {"migration", "revert"}:
            errors.append("architecture_delta_requires_migration_or_revert")
        if str(contract.get("architecture_policy_delta") or "") == "":
            errors.append("architecture_delta_not_bound_to_contract")
    operations = delta.get("operations")
    if not isinstance(operations, dict):
        return errors + ["architecture_delta_operations_must_be_object"]
    allowed_operation_keys = {
        name for pair in _LIST_OPERATIONS.values() for name in pair
    } | {"upsert_line_limits", "retire_line_limit_paths", "owner_changes"}
    unknown = sorted(set(operations) - allowed_operation_keys)
    if unknown:
        errors.append("architecture_delta_unknown_operations:" + ",".join(unknown))
    for _field, (add_name, retire_name) in _LIST_OPERATIONS.items():
        try:
            additions = {_normalise_path(v) for v in operations.get(add_name, []) or []}
            retirements = {_normalise_path(v) for v in operations.get(retire_name, []) or []}
        except ValueError as exc:
            errors.append(str(exc))
            continue
        overlap = sorted(additions & retirements)
        if overlap:
            errors.append(f"architecture_delta_add_retire_overlap:{add_name}:{','.join(overlap)}")
    owner_changes = operations.get("owner_changes") or {}
    if not isinstance(owner_changes, dict):
        errors.append("architecture_delta_owner_changes_must_be_object")
    else:
        unknown_owners = sorted(set(owner_changes) - _OWNER_FIELDS)
        if unknown_owners:
            errors.append("architecture_delta_unknown_owner_fields:" + ",".join(unknown_owners))
        for value in owner_changes.values():
            try:
                _normalise_path(value)
            except ValueError as exc:
                errors.append(str(exc))
    cutover = delta.get("cutover")
    if not isinstance(cutover, dict):
        errors.append("architecture_delta_cutover_must_be_object")
    else:
        required_cutover = {
            "current_formal_owner",
            "target_formal_owner",
            "shadow_is_read_only",
            "cutover_condition",
            "rollback_condition",
            "cleanup_condition",
            "sunset_date",
        }
        if required_cutover.difference(cutover):
            errors.append("architecture_delta_cutover_incomplete")
        if cutover.get("shadow_is_read_only") is not True:
            errors.append("architecture_delta_shadow_must_be_read_only")
    evidence = delta.get("required_evidence")
    if not isinstance(evidence, list) or not evidence or any(not str(v).strip() for v in evidence):
        errors.append("architecture_delta_required_evidence_invalid")
    return errors


def apply_delta(base_policy: dict[str, Any], delta: dict[str, Any]) -> dict[str, Any]:
    errors = validate_delta(delta, base_policy=base_policy)
    if errors:
        raise ValueError("invalid architecture policy delta: " + "; ".join(errors))
    policy = copy.deepcopy(base_policy)
    operations = delta["operations"]
    for field, (add_name, retire_name) in _LIST_OPERATIONS.items():
        values = [_normalise_path(v) for v in policy.get(field, [])]
        retired = {_normalise_path(v) for v in operations.get(retire_name, []) or []}
        additions = [_normalise_path(v) for v in operations.get(add_name, []) or []]
        policy[field] = _dedupe([v for v in values if v not in retired] + additions)

    limits = {
        str(row.get("path")): dict(row)
        for row in policy.get("line_limits", [])
        if isinstance(row, dict) and row.get("path")
    }
    for raw in operations.get("retire_line_limit_paths", []) or []:
        limits.pop(_normalise_path(raw), None)
    for row in operations.get("upsert_line_limits", []) or []:
        if not isinstance(row, dict) or not row.get("path") or int(row.get("max_lines") or 0) <= 0:
            raise ValueError("invalid line-limit operation")
        path = _normalise_path(row["path"])
        limits[path] = {"path": path, "max_lines": int(row["max_lines"])}
    policy["line_limits"] = [limits[key] for key in sorted(limits)]

    for field, value in (operations.get("owner_changes") or {}).items():
        policy[field] = _normalise_path(value)

    policy["effective_policy"] = {
        "base_policy_id": base_policy.get("policy_id"),
        "delta_id": delta.get("delta_id"),
        "change_id": delta.get("change_id"),
        "applied_at": _now(),
    }
    return policy


def load_effective_policy(
    workspace: Path,
    policy_path: Path | None = None,
    *,
    contract_path: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    workspace = workspace.resolve()
    baseline_path = (policy_path or workspace / BASE_POLICY).resolve()
    base = _json(baseline_path)
    baseline_errors = validate_baseline(base)
    if baseline_errors:
        raise ValueError("invalid architecture baseline: " + "; ".join(baseline_errors))
    metadata: dict[str, Any] = {
        "mode": "baseline",
        "policy_path": baseline_path.relative_to(workspace).as_posix(),
        "policy_id": base.get("policy_id"),
    }
    active_path = (contract_path or workspace / ACTIVE_CONTRACT).resolve()
    if not active_path.is_file():
        return base, metadata
    contract = _json(active_path)
    raw_delta = str(contract.get("architecture_policy_delta") or "").strip()
    if not raw_delta:
        return base, metadata
    if contract.get("status") not in {"approved", "implementing", "review", "verified"}:
        raise ValueError("architecture policy delta is bound to an inactive Change Contract")
    if contract.get("target_kind") not in {"migration", "revert"}:
        raise ValueError("architecture policy delta requires migration or revert")
    decision_path = str(contract.get("decision_record") or "").strip()
    if not decision_path or not (workspace / decision_path).is_file():
        raise ValueError("architecture policy delta requires an Architecture Decision")
    delta_path = (workspace / raw_delta).resolve()
    try:
        delta_path.relative_to(workspace)
    except ValueError as exc:
        raise ValueError("architecture policy delta must stay inside workspace") from exc
    if not delta_path.is_file():
        raise ValueError(f"architecture policy delta missing: {delta_path}")
    delta = _json(delta_path)
    errors = validate_delta(delta, base_policy=base, contract=contract)
    if errors:
        raise ValueError("invalid architecture policy delta: " + "; ".join(errors))
    delta_rel = delta_path.relative_to(workspace).as_posix()
    variance_bound = False
    for raw_variance in contract.get("variance_records", []) or []:
        variance_path = (workspace / str(raw_variance)).resolve()
        if not variance_path.is_file():
            continue
        variance = _json(variance_path)
        if str(variance.get("policy_delta") or "") == delta_rel:
            variance_bound = True
            break
    if not variance_bound:
        raise ValueError("architecture policy delta is not bound by an Architecture Variance")
    effective = apply_delta(base, delta)
    metadata.update({
        "mode": "baseline+approved-delta",
        "delta_path": delta_path.relative_to(workspace).as_posix(),
        "delta_id": delta.get("delta_id"),
        "change_id": delta.get("change_id"),
    })
    return effective, metadata


def promote_delta(
    workspace: Path,
    *,
    delta_path: Path,
    certification_evidence: list[Path],
    new_policy_id: str,
) -> Path:
    workspace = workspace.resolve()
    baseline_path = workspace / BASE_POLICY
    base = _json(baseline_path)
    errors = validate_baseline(base)
    if errors:
        raise ValueError("invalid architecture baseline: " + "; ".join(errors))
    delta_path = delta_path.resolve()
    delta = _json(delta_path)
    errors = validate_delta(delta, base_policy=base)
    if errors:
        raise ValueError("invalid architecture policy delta: " + "; ".join(errors))
    if delta.get("status") != "approved":
        raise ValueError("only an approved delta can be promoted")
    if not certification_evidence:
        raise ValueError("baseline promotion requires certification evidence")
    evidence_rows: list[dict[str, str]] = []
    accepted_verdict = False
    for path in certification_evidence:
        path = path.resolve()
        try:
            rel = path.relative_to(workspace).as_posix()
        except ValueError as exc:
            raise ValueError("certification evidence must stay inside workspace") from exc
        if not path.is_file():
            raise ValueError(f"certification evidence missing: {path}")
        try:
            payload = _json(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"certification evidence must be a JSON verdict: {path}") from exc
        verdict = str(payload.get("result") or payload.get("decision") or payload.get("status") or "")
        if verdict in {"CONVERGED", "PASS"}:
            accepted_verdict = True
        evidence_rows.append({"path": rel, "sha256": _sha256(path), "verdict": verdict})
    if not accepted_verdict:
        raise ValueError("baseline promotion requires at least one PASS or CONVERGED certification verdict")

    promoted = apply_delta(base, delta)
    promoted.pop("effective_policy", None)
    previous_id = str(base.get("policy_id") or "")
    promoted["policy_id"] = new_policy_id
    promoted["baseline_revision"] = int(base.get("baseline_revision") or 1) + 1
    promoted["supersedes_policy_id"] = previous_id
    promoted["last_promoted_change_id"] = delta.get("change_id")
    promoted["promoted_at"] = _now()
    baseline_path.write_text(json.dumps(promoted, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    record_dir = workspace / "governance" / "architecture-promotions"
    record_dir.mkdir(parents=True, exist_ok=True)
    record_path = record_dir / f"{delta.get('change_id')}.json"
    record = {
        "schema_version": 1,
        "change_id": delta.get("change_id"),
        "delta_id": delta.get("delta_id"),
        "previous_policy_id": previous_id,
        "new_policy_id": new_policy_id,
        "promoted_at": promoted["promoted_at"],
        "certification_evidence": evidence_rows,
        "baseline_sha256": _sha256(baseline_path),
    }
    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record_path
