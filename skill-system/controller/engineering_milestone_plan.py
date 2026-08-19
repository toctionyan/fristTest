from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable, Mapping


PLAN_SCHEMA = "engineering-milestone-plan@1"
BINDING_SCHEMA = "engineering-milestone-plan-binding@1"
EVIDENCE_SCHEMA = "engineering-milestone-case-evidence@1"
CERTIFICATION_SCHEMA = "engineering-milestone-certification@1"
_ALLOWED_CASE_STATUS = {"PASS", "FAIL"}
_ALLOWED_VERDICT = {"PASS", "FAIL", "UNKNOWN"}


class EngineeringMilestoneError(RuntimeError):
    """Raised when milestone identity or certification evidence drifts."""


def _text(value: object) -> str:
    return str(value or "").strip()


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha(value: object, *, name: str) -> str:
    text = _text(value).lower()
    if not re.fullmatch(r"[0-9a-f]{40}", text):
        raise EngineeringMilestoneError(f"{name} must be an exact 40-hex commit SHA")
    return text


def _positive_int(value: object, *, name: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise EngineeringMilestoneError(f"{name} must be numeric") from exc
    if result < 1:
        raise EngineeringMilestoneError(f"{name} must be positive")
    return result


def validate_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    if plan.get("schema") != PLAN_SCHEMA:
        raise EngineeringMilestoneError("unsupported milestone plan schema")
    repository = _text(plan.get("repository"))
    source_branch = _text(plan.get("source_branch"))
    if not repository or "/" not in repository:
        raise EngineeringMilestoneError("milestone plan repository is required")
    if not source_branch:
        raise EngineeringMilestoneError("milestone plan source_branch is required")
    issue_number = _positive_int(plan.get("issue_number"), name="issue_number")
    source_pr = _positive_int(plan.get("source_pr"), name="source_pr")
    milestones = plan.get("milestones")
    if not isinstance(milestones, list) or not milestones:
        raise EngineeringMilestoneError("milestone plan requires a non-empty milestones list")

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(milestones, start=1):
        if not isinstance(raw, Mapping):
            raise EngineeringMilestoneError("each milestone must be an object")
        case_id = _text(raw.get("id"))
        if not case_id or case_id in seen:
            raise EngineeringMilestoneError("milestone ids must be non-empty and unique")
        seen.add(case_id)
        order = _positive_int(raw.get("order"), name=f"milestone {case_id} order")
        if order != index:
            raise EngineeringMilestoneError(
                "milestone order must be contiguous and match list order"
            )
        oracle = _text(raw.get("oracle_evidence"))
        runtime_test = _text(raw.get("runtime_test"))
        test_name = _text(raw.get("test_name"))
        if not oracle or not runtime_test or not test_name:
            raise EngineeringMilestoneError(
                f"milestone {case_id} is missing executable evidence coordinates"
            )
        normalized.append(
            {
                "id": case_id,
                "order": order,
                "oracle_evidence": oracle,
                "runtime_test": runtime_test,
                "test_name": test_name,
            }
        )

    normalized_plan = {
        "schema": PLAN_SCHEMA,
        "plan_id": _text(plan.get("plan_id")) or f"issue-{issue_number}-pack-a",
        "repository": repository,
        "issue_number": issue_number,
        "source_pr": source_pr,
        "source_branch": source_branch,
        "milestones": normalized,
        "required_product_gate": _text(plan.get("required_product_gate"))
        or "quality-quick-execution",
        "required_transport_gate": _text(plan.get("required_transport_gate"))
        or "quality-quick-required-status",
        "authority_effect": "none",
        "merge_allowed": False,
        "deploy_allowed": False,
        "production_closed": False,
    }
    normalized_plan["plan_sha256"] = _digest(normalized_plan)
    return normalized_plan


def bind_plan(
    plan: Mapping[str, Any],
    *,
    candidate_head_sha: str,
    quality_run_id: int,
    quality_run_attempt: int,
) -> dict[str, Any]:
    normalized = validate_plan(plan)
    binding = {
        "schema": BINDING_SCHEMA,
        "plan_id": normalized["plan_id"],
        "plan_sha256": normalized["plan_sha256"],
        "repository": normalized["repository"],
        "issue_number": normalized["issue_number"],
        "source_pr": normalized["source_pr"],
        "source_branch": normalized["source_branch"],
        "candidate_head_sha": _sha(candidate_head_sha, name="candidate_head_sha"),
        "quality_run_id": _positive_int(quality_run_id, name="quality_run_id"),
        "quality_run_attempt": _positive_int(
            quality_run_attempt, name="quality_run_attempt"
        ),
        "milestone_ids": [item["id"] for item in normalized["milestones"]],
        "required_product_gate": normalized["required_product_gate"],
        "required_transport_gate": normalized["required_transport_gate"],
        "authority_effect": "none",
        "merge_allowed": False,
        "deploy_allowed": False,
        "production_closed": False,
    }
    binding["binding_sha256"] = _digest(binding)
    return binding


def validate_binding(
    binding: Mapping[str, Any],
    *,
    current_head_sha: str | None = None,
) -> dict[str, Any]:
    if binding.get("schema") != BINDING_SCHEMA:
        raise EngineeringMilestoneError("unsupported milestone binding schema")
    supplied = dict(binding)
    digest = _text(supplied.pop("binding_sha256", None))
    if not re.fullmatch(r"[0-9a-f]{64}", digest) or digest != _digest(supplied):
        raise EngineeringMilestoneError("milestone binding digest mismatch")
    head = _sha(supplied.get("candidate_head_sha"), name="candidate_head_sha")
    _positive_int(supplied.get("quality_run_id"), name="quality_run_id")
    _positive_int(supplied.get("quality_run_attempt"), name="quality_run_attempt")
    if supplied.get("merge_allowed") is not False or supplied.get("deploy_allowed") is not False:
        raise EngineeringMilestoneError(
            "milestone binding cannot grant merge or deploy authority"
        )
    if (
        supplied.get("production_closed") is not False
        or supplied.get("authority_effect") != "none"
    ):
        raise EngineeringMilestoneError("milestone binding authority boundary drifted")
    ids = supplied.get("milestone_ids")
    if not isinstance(ids, list) or not ids or len(ids) != len(set(map(str, ids))):
        raise EngineeringMilestoneError("milestone binding ids are malformed")
    if current_head_sha is not None and _sha(
        current_head_sha, name="current_head_sha"
    ) != head:
        raise EngineeringMilestoneError(
            "candidate head drifted from bound milestone plan"
        )
    result = dict(supplied)
    result["binding_sha256"] = digest
    return result


def build_case_evidence(
    binding: Mapping[str, Any],
    *,
    case_id: str,
    status: str,
    evidence_ref: str,
    test_name: str,
) -> dict[str, Any]:
    bound = validate_binding(binding)
    case_id = _text(case_id)
    if case_id not in bound["milestone_ids"]:
        raise EngineeringMilestoneError(f"unknown milestone case: {case_id}")
    normalized_status = _text(status).upper()
    if normalized_status not in _ALLOWED_CASE_STATUS:
        raise EngineeringMilestoneError(
            "case evidence must be an executed PASS or FAIL; "
            "skipped/pending is not certification"
        )
    if not _text(evidence_ref) or not _text(test_name):
        raise EngineeringMilestoneError(
            "case evidence requires durable evidence_ref and test_name"
        )
    payload = {
        "schema": EVIDENCE_SCHEMA,
        "binding_sha256": bound["binding_sha256"],
        "candidate_head_sha": bound["candidate_head_sha"],
        "quality_run_id": bound["quality_run_id"],
        "quality_run_attempt": bound["quality_run_attempt"],
        "case_id": case_id,
        "status": normalized_status,
        "evidence_ref": _text(evidence_ref),
        "test_name": _text(test_name),
        "authority_effect": "none",
        "production_closed": False,
    }
    payload["evidence_sha256"] = _digest(payload)
    return payload


def _validate_case_evidence(
    binding: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    bound = validate_binding(binding)
    if evidence.get("schema") != EVIDENCE_SCHEMA:
        raise EngineeringMilestoneError("unsupported case evidence schema")
    raw = dict(evidence)
    digest = _text(raw.pop("evidence_sha256", None))
    if not re.fullmatch(r"[0-9a-f]{64}", digest) or digest != _digest(raw):
        raise EngineeringMilestoneError("case evidence digest mismatch")
    exact = {
        "binding_sha256": bound["binding_sha256"],
        "candidate_head_sha": bound["candidate_head_sha"],
        "quality_run_id": bound["quality_run_id"],
        "quality_run_attempt": bound["quality_run_attempt"],
    }
    for key, expected in exact.items():
        if str(raw.get(key)) != str(expected):
            raise EngineeringMilestoneError(f"case evidence binding mismatch: {key}")
    if raw.get("authority_effect") != "none" or raw.get("production_closed") is not False:
        raise EngineeringMilestoneError("case evidence cannot carry lifecycle authority")
    if _text(raw.get("status")).upper() not in _ALLOWED_CASE_STATUS:
        raise EngineeringMilestoneError(
            "case evidence status is not terminal executable evidence"
        )
    result = dict(raw)
    result["evidence_sha256"] = digest
    return result


def certify_plan(
    binding: Mapping[str, Any],
    case_evidence: Iterable[Mapping[str, Any]],
    *,
    product_verdict: str,
    transport_verdict: str,
    product_evidence_ref: str,
    transport_evidence_ref: str,
) -> dict[str, Any]:
    bound = validate_binding(binding)
    product = _text(product_verdict).upper() or "UNKNOWN"
    transport = _text(transport_verdict).upper() or "UNKNOWN"
    if product not in _ALLOWED_VERDICT or transport not in _ALLOWED_VERDICT:
        raise EngineeringMilestoneError("unsupported product or transport verdict")
    if not _text(product_evidence_ref) or not _text(transport_evidence_ref):
        raise EngineeringMilestoneError(
            "certification requires product and transport evidence refs"
        )

    observed: dict[str, dict[str, Any]] = {}
    for raw in case_evidence:
        item = _validate_case_evidence(bound, raw)
        case_id = _text(item.get("case_id"))
        if case_id in observed:
            raise EngineeringMilestoneError(
                f"duplicate milestone evidence: {case_id}"
            )
        if case_id not in bound["milestone_ids"]:
            raise EngineeringMilestoneError(
                f"unexpected milestone evidence: {case_id}"
            )
        observed[case_id] = item

    missing = [
        case_id for case_id in bound["milestone_ids"] if case_id not in observed
    ]
    failed = [
        case_id
        for case_id in bound["milestone_ids"]
        if observed.get(case_id, {}).get("status") == "FAIL"
    ]

    if missing:
        decision = "INCOMPLETE_EVIDENCE"
        reason = (
            "one or more required real-lifecycle milestones have no exact-head "
            "executable evidence"
        )
    elif failed or product == "FAIL":
        decision = "PRODUCT_RED"
        reason = (
            "real-lifecycle product evidence is red; transport state must not mask "
            "or rewrite product failure"
        )
    elif product == "UNKNOWN":
        decision = "PRODUCT_UNKNOWN"
        reason = "product evidence is not authoritative enough for certification"
    elif transport != "PASS":
        decision = "WAIT_TRANSPORT"
        reason = (
            "all product milestones are green, but exact-head transport/publication "
            "certification is not green"
        )
    else:
        decision = "CERTIFIED"
        reason = (
            "all required product milestones and exact-head transport certification "
            "are green"
        )

    product_certified = not missing and not failed and product == "PASS"
    final_certified = product_certified and transport == "PASS"
    result = {
        "schema": CERTIFICATION_SCHEMA,
        "binding_sha256": bound["binding_sha256"],
        "candidate_head_sha": bound["candidate_head_sha"],
        "quality_run_id": bound["quality_run_id"],
        "quality_run_attempt": bound["quality_run_attempt"],
        "decision": decision,
        "reason": reason,
        "product_verdict": product,
        "transport_verdict": transport,
        "product_certified": product_certified,
        "final_certified": final_certified,
        "milestone_count": len(bound["milestone_ids"]),
        "passed_milestone_count": sum(
            1 for item in observed.values() if item.get("status") == "PASS"
        ),
        "missing_milestones": missing,
        "failed_milestones": failed,
        "first_failure": failed[0] if failed else None,
        "product_repair_authorized": False,
        "authority_effect": "none",
        "merge_allowed": False,
        "deploy_allowed": False,
        "production_closed": False,
        "evidence_refs": [
            _text(product_evidence_ref),
            _text(transport_evidence_ref),
        ],
    }
    result["certification_sha256"] = _digest(result)
    return result
