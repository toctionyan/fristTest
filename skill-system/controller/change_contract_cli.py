from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

CONTROLLER = Path(__file__).resolve().parent
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from contract import (  # type: ignore
    ACTIVE_CONTRACT,
    PRODUCT_CONTROL_FORBIDDEN,
    REQUIRED_PROFILES,
    SKILL_ONLY_ALLOWED,
    SKILL_ONLY_FORBIDDEN,
    load_contract,
    validate_contract_payload,
)
from product_scope import (  # type: ignore
    PRODUCT_PROFILES,
    profile_for_target,
    required_profiles_for_product,
)
from profile_runner import run as run_profile  # type: ignore
from verification import source_fingerprint  # type: ignore
from architecture_policy import (  # type: ignore
    BASE_POLICY,
    load_effective_policy,
    promote_delta,
    validate_delta,
)
from repair_governance import (  # type: ignore
    validate_begin_ready,
    validate_verification_ready,
)

FINAL_RESULTS = (
    "CONVERGED",
    "NO_CODE_CHANGE_REQUIRED",
    "BLOCKED_BY_ENVIRONMENT",
    "ORACLE_REVIEW_REQUIRED",
    "ARCHITECTURE_REPLAN_REQUIRED",
    "REVERT_RECOMMENDED",
    "STOPPED_MAX_REPAIRS",
)
TARGET_KINDS = ("diagnosis", "design", "oracle-review", "repair", "migration", "revert", "certification")
READ_ONLY_KINDS = {"diagnosis", "design", "oracle-review", "certification"}
TRANSITION_KINDS = {"repair", "migration", "revert"}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _workspace() -> Path:
    return Path.cwd().resolve()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_contract(path: Path, payload: dict[str, Any]) -> None:
    errors = validate_contract_payload(payload)
    if errors:
        raise SystemExit("refusing to write invalid contract: " + "; ".join(errors))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip().strip("`") for value in values if value.strip().strip("`")))


def _product_payload(args: argparse.Namespace, workspace: Path) -> dict[str, Any]:
    profile = profile_for_target(args.target_kind)
    allowed = _dedupe(args.allow or [])
    if not allowed:
        raise SystemExit("product-code contract requires one or more explicit --allow paths")
    forbidden = _dedupe(list(PRODUCT_CONTROL_FORBIDDEN) + list(args.forbid or []))
    writer_role = "none" if args.target_kind in READ_ONLY_KINDS else "product-implementer"
    required_profiles = required_profiles_for_product(args.target_kind, args.minimum_mode)
    fingerprint, file_count = source_fingerprint(workspace, allowed)
    review_roles = ["scope-planner", "adversarial-reviewer", "release-judge"]
    if args.target_kind == "oracle-review":
        review_roles.insert(1, "oracle-reviewer")
    return {
        "schema_version": 1,
        "change_id": args.change_id,
        "target_kind": args.target_kind,
        "goal": args.goal,
        "profile": profile,
        "allowed_paths": allowed,
        "forbidden_paths": forbidden,
        "invariants": _dedupe(args.invariant or [
            "the approved product scope is the only writable product scope",
            "quality Target, Claims, baseline, Judge and evidence are not modified to make a candidate pass",
            "the final result is bound to current source and current product Quality Loop evidence",
            "one writable implementer owns the candidate worktree",
        ]),
        "required_profiles": list(required_profiles),
        "writer_role": writer_role,
        "review_roles": review_roles,
        "review_attestations": [],
        "affected_modules": _dedupe(args.affected_module or []),
        "minimum_quality_mode": args.minimum_mode,
        "quality_target": args.quality_target,
        "baseline_evidence": args.baseline_evidence,
        "initial_source_fingerprint": fingerprint,
        "initial_source_file_count": file_count,
        "product_validation": None,
        "decision_record": args.decision_record,
        "variance_records": _dedupe(args.variance or []),
        "architecture_policy_delta": args.architecture_policy_delta,
        "baseline_policy_id": args.baseline_policy_id,
        "verification": None,
        "repair_governance": args.repair_governance,
        "repair_governance_consumed_at": None,
        "created_at": _now(),
        "status": "approved" if args.approve else "draft",
        "result": "PENDING",
    }


def _skill_payload(args: argparse.Namespace) -> dict[str, Any]:
    writer_role = "none" if args.target_kind in READ_ONLY_KINDS else "skill-implementer"
    return {
        "schema_version": 1,
        "change_id": args.change_id,
        "target_kind": args.target_kind,
        "goal": args.goal,
        "profile": "skill-only",
        "allowed_paths": list(SKILL_ONLY_ALLOWED),
        "forbidden_paths": list(SKILL_ONLY_FORBIDDEN),
        "invariants": [
            "customer-agent product source remains unchanged",
            "trusted Judge inputs are not modified by the implementer",
            "current evidence is required for completion",
        ],
        "required_profiles": list(REQUIRED_PROFILES["skill-only"]),
        "writer_role": writer_role,
        "review_roles": ["scope-planner", "adversarial-reviewer", "release-judge"],
        "review_attestations": [],
        "decision_record": args.decision_record,
        "variance_records": _dedupe(args.variance or []),
        "architecture_policy_delta": args.architecture_policy_delta,
        "baseline_policy_id": args.baseline_policy_id,
        "verification": None,
        "repair_governance": args.repair_governance,
        "repair_governance_consumed_at": None,
        "created_at": _now(),
        "status": "approved" if args.approve else "draft",
        "result": "PENDING",
    }


def cmd_init(args: argparse.Namespace) -> int:
    workspace = _workspace()
    path = workspace / ACTIVE_CONTRACT
    if path.exists() and not args.force:
        raise SystemExit(f"active contract already exists: {path}; close it or use --force")
    payload = _skill_payload(args) if args.profile == "skill-only" else _product_payload(args, workspace)
    _write_contract(path, payload)
    print(path)
    return 0


def cmd_validate(_args: argparse.Namespace) -> int:
    contract = load_contract(_workspace(), require_approved=False)
    print(json.dumps({
        "status": "PASS",
        "change_id": contract.change_id,
        "target_kind": contract.target_kind.value,
        "profile": contract.profile,
        "contract": str(contract.path),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_show(_args: argparse.Namespace) -> int:
    path = _workspace() / ACTIVE_CONTRACT
    print(path.read_text(encoding="utf-8"))
    return 0


def cmd_approve(_args: argparse.Namespace) -> int:
    contract = load_contract(_workspace(), require_approved=False)
    if contract.status != "draft":
        raise SystemExit(f"only a draft contract can be approved: {contract.status}")
    payload = dict(contract.payload)
    payload["status"] = "approved"
    _write_contract(contract.path, payload)
    print(contract.path)
    return 0


def cmd_configure(args: argparse.Namespace) -> int:
    contract = load_contract(_workspace(), require_approved=False)
    if contract.status not in {"draft", "approved"}:
        raise SystemExit("contract configuration is frozen after implementation begins")
    payload = dict(contract.payload)
    if args.quality_target is not None:
        payload["quality_target"] = args.quality_target
    if args.baseline_evidence is not None:
        payload["baseline_evidence"] = args.baseline_evidence
    if args.decision_record is not None:
        payload["decision_record"] = args.decision_record
    if args.architecture_policy_delta is not None:
        payload["architecture_policy_delta"] = args.architecture_policy_delta
    if args.baseline_policy_id is not None:
        payload["baseline_policy_id"] = args.baseline_policy_id
    if args.repair_governance is not None:
        payload["repair_governance"] = args.repair_governance
    if args.variance:
        payload["variance_records"] = _dedupe(list(payload.get("variance_records") or []) + list(args.variance))
    _write_contract(contract.path, payload)
    print(contract.path)
    return 0


def cmd_begin(_args: argparse.Namespace) -> int:
    workspace = _workspace()
    contract = load_contract(workspace)
    payload = dict(contract.payload)
    if payload.get("status") not in {"approved", "review"}:
        raise SystemExit(f"cannot begin contract from status {payload.get('status')}")
    if contract.target_kind.value in TRANSITION_KINDS and contract.profile in PRODUCT_PROFILES:
        if not str(payload.get("baseline_evidence") or "").strip():
            raise SystemExit("product transition must run product-baseline before begin")
    if contract.target_kind.value in TRANSITION_KINDS:
        # Migration/repair prerequisites must fail before the contract enters a writable state.
        # In particular, a migration cannot postpone its three-option architecture decision
        # until final verification after implementation and tests have already run.
        _validate_architecture_inputs(workspace, payload)
        try:
            governance = validate_begin_ready(workspace, payload)
        except ValueError as exc:
            raise SystemExit(f"repair governance is not ready: {exc}") from exc
        payload["repair_governance_permit_digest"] = governance["permit_digest"]
    payload["status"] = "implementing"
    payload["verification"] = None
    payload["result"] = "PENDING"
    _write_contract(contract.path, payload)
    print(contract.path)
    return 0


def cmd_attest_review(args: argparse.Namespace) -> int:
    workspace = _workspace()
    contract = load_contract(workspace, require_approved=False)
    if args.role not in contract.payload.get("review_roles", []):
        raise SystemExit(f"role is not declared by the contract: {args.role}")
    evidence = Path(args.evidence).expanduser().resolve()
    try:
        relative = evidence.relative_to(workspace)
    except ValueError as exc:
        raise SystemExit("review evidence must be inside the workspace") from exc
    if not evidence.is_file():
        raise SystemExit(f"review evidence does not exist: {evidence}")
    payload = dict(contract.payload)
    rows = [
        row for row in payload.get("review_attestations", [])
        if isinstance(row, dict) and row.get("role") != args.role
    ]
    rows.append({
        "role": args.role,
        "decision": args.decision,
        "evidence": relative.as_posix(),
        "evidence_sha256": _sha256(evidence),
        "recorded_at": _now(),
    })
    payload["review_attestations"] = rows
    payload["status"] = "rejected" if args.decision == "REJECT" else "review"
    payload["verification"] = None
    _write_contract(contract.path, payload)
    print(contract.path)
    return 0


def _load_governance_record(workspace: Path, raw: object, *, label: str) -> tuple[Path, dict[str, object]]:
    if not isinstance(raw, str) or not raw.strip():
        raise SystemExit(f"{label} is required")
    path = (workspace / raw).resolve()
    try:
        path.relative_to(workspace)
    except ValueError as exc:
        raise SystemExit(f"{label} must stay inside the workspace") from exc
    if not path.is_file():
        raise SystemExit(f"{label} does not exist: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise SystemExit(f"{label} has an invalid schema version: {path}")
    return path, payload


def _validate_architecture_inputs(workspace: Path, contract_payload: dict[str, object]) -> None:
    target_kind = str(contract_payload.get("target_kind") or "")
    decision: dict[str, object] | None = None
    if target_kind in {"design", "migration"}:
        _path, decision = _load_governance_record(
            workspace, contract_payload.get("decision_record"), label="architecture decision record"
        )
        decision_change = str(decision.get("change_id") or "")
        contract_change = str(contract_payload.get("change_id") or "")
        if decision_change and decision_change != contract_change:
            raise SystemExit("architecture decision change_id must match the Change Contract")
        options = decision.get("options")
        strategies = {
            str(row.get("strategy") or "")
            for row in options if isinstance(row, dict)
        } if isinstance(options, list) else set()
        if strategies != {"conservative", "evolutionary", "redesign"}:
            raise SystemExit("architecture decision must compare conservative, evolutionary and redesign options")
        selected = str(decision.get("selected_option") or "")
        ids = {str(row.get("id") or "") for row in options if isinstance(row, dict)}
        if not selected or selected not in ids:
            raise SystemExit("architecture decision selected_option must reference one declared option")
        list_fields = ("selected_responsibilities", "preserved_hard_invariants", "acceptance_claims")
        for field in list_fields:
            value = decision.get(field)
            if not isinstance(value, list) or not value:
                raise SystemExit(f"architecture decision missing {field}")
        if not str(decision.get("cutover_strategy") or "").strip():
            raise SystemExit("architecture decision missing cutover_strategy")

    variance_payloads: list[tuple[Path, dict[str, object]]] = []
    for raw in contract_payload.get("variance_records", []) or []:
        path, variance = _load_governance_record(workspace, raw, label="architecture variance record")
        if not str(variance.get("affected_rule") or ""):
            raise SystemExit("architecture variance record must name affected_rule")
        if not str(variance.get("current_problem") or ""):
            raise SystemExit("architecture variance record must explain current_problem")
        variance_payloads.append((path, variance))

    raw_delta = str(contract_payload.get("architecture_policy_delta") or "").strip()
    if raw_delta:
        delta_path, delta = _load_governance_record(workspace, raw_delta, label="architecture policy delta")
        base_path = workspace / BASE_POLICY
        base = json.loads(base_path.read_text(encoding="utf-8"))
        errors = validate_delta(delta, base_policy=base, contract=dict(contract_payload))
        if errors:
            raise SystemExit("invalid architecture policy delta: " + "; ".join(errors))
        if str(contract_payload.get("baseline_policy_id") or "") != str(base.get("policy_id") or ""):
            raise SystemExit("Change Contract baseline_policy_id does not match current project baseline")
        if decision is None:
            raise SystemExit("architecture policy delta requires an Architecture Decision")
        delta_rel = delta_path.relative_to(workspace).as_posix()
        bound = any(str(variance.get("policy_delta") or "") == delta_rel for _path, variance in variance_payloads)
        if not bound:
            raise SystemExit("architecture policy delta must be bound by an Architecture Variance record")


def _latest_review(payload: dict[str, object], role: str) -> dict[str, object] | None:
    rows = [
        row for row in payload.get("review_attestations", [])
        if isinstance(row, dict) and row.get("role") == role
    ]
    return rows[-1] if rows else None


def _require_review(workspace: Path, payload: dict[str, Any], role: str) -> dict[str, object]:
    review = _latest_review(payload, role)
    if not review or review.get("decision") != "PASS":
        raise SystemExit(f"a PASS attestation from {role} is required")
    evidence_path = workspace / str(review.get("evidence") or "")
    if not evidence_path.is_file() or _sha256(evidence_path) != review.get("evidence_sha256"):
        raise SystemExit(f"{role} evidence is missing or changed")
    return review


def cmd_verify(args: argparse.Namespace) -> int:
    workspace = _workspace()
    contract = load_contract(workspace, require_approved=False)
    if contract.status not in {"review", "implementing", "approved"}:
        raise SystemExit(f"cannot verify contract from status {contract.status}")
    _validate_architecture_inputs(workspace, contract.payload)
    repair_governance: dict[str, object] | None = None
    if contract.target_kind.value in TRANSITION_KINDS:
        try:
            repair_governance = validate_verification_ready(
                workspace, contract.payload, expected_result=args.result
            )
        except ValueError as exc:
            raise SystemExit(f"repair governance verification failed: {exc}") from exc
    reviews: dict[str, dict[str, object]] = {
        "adversarial-reviewer": _require_review(workspace, contract.payload, "adversarial-reviewer")
    }
    if contract.profile in PRODUCT_PROFILES:
        reviews["scope-planner"] = _require_review(workspace, contract.payload, "scope-planner")
    if contract.target_kind.value == "oracle-review":
        reviews["oracle-reviewer"] = _require_review(workspace, contract.payload, "oracle-reviewer")

    profile_results: list[dict[str, object]] = []
    for profile in contract.payload.get("required_profiles", []):
        result = run_profile(str(profile))
        profile_results.append(result)
        if result.get("status") != "PASS":
            raise SystemExit(f"required profile failed: {profile}")

    fingerprint, file_count = source_fingerprint(workspace, contract.allowed_paths)
    initial_fingerprint = contract.payload.get("initial_source_fingerprint")
    if contract.profile in PRODUCT_PROFILES and isinstance(initial_fingerprint, str):
        if contract.target_kind.requires_candidate_change and fingerprint == initial_fingerprint:
            raise SystemExit("product transition requires an actual in-scope candidate change")
        if not contract.target_kind.requires_candidate_change and fingerprint != initial_fingerprint:
            raise SystemExit("read-only product target changed governed source")

    evidence_root = "product-control-plane" if contract.profile in PRODUCT_PROFILES else "skill-control-plane"
    output = workspace / ".quality" / evidence_root / contract.change_id / "verification.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    _effective_policy, architecture_policy_meta = load_effective_policy(workspace)
    record = {
        "schema_version": 1,
        "change_id": contract.change_id,
        "profile": contract.profile,
        "target_kind": contract.target_kind.value,
        "verified_at": _now(),
        "result": args.result,
        "source_fingerprint": fingerprint,
        "source_file_count": file_count,
        "initial_source_fingerprint": initial_fingerprint,
        "required_profiles": list(contract.payload.get("required_profiles", [])),
        "profile_results": profile_results,
        "review_evidence": reviews,
        "architecture_policy": architecture_policy_meta,
        "repair_governance": repair_governance,
    }
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    payload = dict(contract.payload)
    payload["status"] = "verified"
    payload["result"] = args.result
    payload["verification"] = {
        "path": output.relative_to(workspace).as_posix(),
        "sha256": _sha256(output),
        "source_fingerprint": fingerprint,
        "source_file_count": file_count,
        "verified_at": record["verified_at"],
    }
    rows = [
        row for row in payload.get("review_attestations", [])
        if isinstance(row, dict) and row.get("role") != "release-judge"
    ]
    rows.append({
        "role": "release-judge",
        "decision": "PASS",
        "evidence": output.relative_to(workspace).as_posix(),
        "evidence_sha256": _sha256(output),
        "recorded_at": _now(),
    })
    payload["review_attestations"] = rows
    _write_contract(contract.path, payload)
    print(output)
    return 0


def cmd_close(args: argparse.Namespace) -> int:
    workspace = _workspace()
    contract = load_contract(workspace, require_approved=False)
    if contract.status != "verified":
        raise SystemExit("a contract can be closed only after deterministic verification")
    if contract.payload.get("result") != args.result:
        raise SystemExit("close result must match the verified result")
    verification = contract.payload.get("verification")
    if not isinstance(verification, dict):
        raise SystemExit("verification identity is missing")
    evidence = workspace / str(verification.get("path") or "")
    if not evidence.is_file() or _sha256(evidence) != verification.get("sha256"):
        raise SystemExit("verification evidence is missing or changed")
    fingerprint, _count = source_fingerprint(workspace, contract.allowed_paths)
    if fingerprint != verification.get("source_fingerprint"):
        raise SystemExit("governed source changed after verification")
    payload = dict(contract.payload)
    if contract.target_kind.value in TRANSITION_KINDS:
        try:
            validate_verification_ready(workspace, contract.payload, expected_result=args.result)
        except ValueError as exc:
            raise SystemExit(f"repair governance changed after verification: {exc}") from exc
        payload["repair_governance_consumed_at"] = _now()
    payload["status"] = "closed"
    payload["closed_at"] = _now()
    _write_contract(contract.path, payload)
    print(contract.path)
    return 0



def cmd_architecture_preview(_args: argparse.Namespace) -> int:
    workspace = _workspace()
    policy, metadata = load_effective_policy(workspace)
    print(json.dumps({
        "status": "PASS",
        "architecture_policy": metadata,
        "effective_policy": policy,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_architecture_promote(args: argparse.Namespace) -> int:
    workspace = _workspace()
    contract = load_contract(workspace, require_approved=False)
    if contract.profile != "skill-only" or contract.target_kind.value != "migration":
        raise SystemExit("architecture baseline promotion requires a skill-only migration contract")
    if contract.status != "implementing":
        raise SystemExit("architecture baseline promotion requires an implementing contract")
    _validate_architecture_inputs(workspace, contract.payload)
    _require_review(workspace, contract.payload, "scope-planner")
    _require_review(workspace, contract.payload, "adversarial-reviewer")
    raw_delta = str(contract.payload.get("architecture_policy_delta") or "").strip()
    if not raw_delta:
        raise SystemExit("active contract does not bind an architecture policy delta")
    evidence = [Path(value).expanduser() for value in args.certification_evidence]
    record = promote_delta(
        workspace,
        delta_path=(workspace / raw_delta),
        certification_evidence=evidence,
        new_policy_id=args.new_policy_id,
    )
    print(record)
    return 0

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--profile", choices=["skill-only", "product-code"], default="skill-only")
    init.add_argument("--change-id", required=True)
    init.add_argument("--goal", required=True)
    init.add_argument("--target-kind", choices=TARGET_KINDS, default="repair")
    init.add_argument("--allow", action="append", default=[])
    init.add_argument("--forbid", action="append", default=[])
    init.add_argument("--affected-module", action="append", default=[])
    init.add_argument("--invariant", action="append", default=[])
    init.add_argument("--minimum-mode", choices=["static", "quick", "integration", "release"], default="static")
    init.add_argument("--quality-target")
    init.add_argument("--baseline-evidence")
    init.add_argument("--decision-record")
    init.add_argument("--variance", action="append", default=[])
    init.add_argument("--architecture-policy-delta")
    init.add_argument("--baseline-policy-id")
    init.add_argument("--repair-governance")
    init.add_argument("--approve", action="store_true")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init)
    validate = sub.add_parser("validate"); validate.set_defaults(func=cmd_validate)
    show = sub.add_parser("show"); show.set_defaults(func=cmd_show)
    approve = sub.add_parser("approve"); approve.set_defaults(func=cmd_approve)
    configure = sub.add_parser("configure")
    configure.add_argument("--quality-target")
    configure.add_argument("--baseline-evidence")
    configure.add_argument("--decision-record")
    configure.add_argument("--variance", action="append", default=[])
    configure.add_argument("--architecture-policy-delta")
    configure.add_argument("--baseline-policy-id")
    configure.add_argument("--repair-governance")
    configure.set_defaults(func=cmd_configure)
    begin = sub.add_parser("begin"); begin.set_defaults(func=cmd_begin)
    attest = sub.add_parser("attest-review")
    attest.add_argument("--role", choices=["scope-planner", "oracle-reviewer", "adversarial-reviewer"], required=True)
    attest.add_argument("--decision", choices=["PASS", "REJECT"], required=True)
    attest.add_argument("--evidence", required=True)
    attest.set_defaults(func=cmd_attest_review)
    verify = sub.add_parser("verify")
    verify.add_argument("--result", choices=FINAL_RESULTS, default="CONVERGED")
    verify.set_defaults(func=cmd_verify)
    close = sub.add_parser("close")
    close.add_argument("--result", required=True, choices=FINAL_RESULTS)
    close.set_defaults(func=cmd_close)
    preview = sub.add_parser("architecture-preview")
    preview.set_defaults(func=cmd_architecture_preview)
    promote = sub.add_parser("architecture-promote")
    promote.add_argument("--new-policy-id", required=True)
    promote.add_argument("--certification-evidence", action="append", default=[], required=True)
    promote.set_defaults(func=cmd_architecture_promote)
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
