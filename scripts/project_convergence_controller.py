#!/usr/bin/env python3
"""Deterministic project-level convergence assessment over Quality evidence.

The project controller is intentionally thin.  It does not edit code, call a
model, parse free-form logs, or decide writable repair scope.  It consumes the
existing project requirement catalog and the structured Quality run summary,
turns unverified requirements into typed findings, and tells the outer system
whether to repair, retry an environment, replan evidence, or advance to the next
certification profile.

A clean Quick or Integration run is a progression checkpoint, not a completion
claim.  Only the project-release profile may yield CONVERGED, and CONVERGED does
not authorize a production release.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

STATE_SCHEMA = "project-convergence-state@1"
FINDINGS_SCHEMA = "project-convergence-findings@1"
CATALOG_SCHEMA_VERSION = 2
MIN_SUMMARY_SCHEMA_VERSION = 6
DEFAULT_REQUIREMENTS_PATH = "governance/requirements/project-quality-requirements.json"
RISK_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
PROFILE_STAGE = {
    "project-quick": ("ITERATION_CLEAN", "project-integration"),
    "project-integration": ("INTEGRATION_CLEAN", "project-release"),
    "project-product": ("PRODUCT_CLEAN", "project-release"),
    "project-release": ("CONVERGED", None),
}
CLEAN_STATUSES = {value[0] for value in PROFILE_STAGE.values()}
ENVIRONMENT_STATUSES = {"BLOCKED_BY_ENVIRONMENT", "ENVIRONMENT_BLOCKED"}


class ConvergenceError(RuntimeError):
    """Raised when convergence evidence is malformed, stale, or incomplete."""


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ConvergenceError(f"JSON object required: {path}")
    return payload


def _fingerprint(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _finding_key(kind: str, *, requirement_id: str = "", gate_id: str = "") -> str:
    return _fingerprint(
        {"kind": kind, "requirement_id": requirement_id, "gate_id": gate_id}
    )[:20]


def _risk(value: object) -> str:
    text = str(value or "P1").upper()
    return text if text in RISK_RANK else "P1"


def _gate_statuses(summary: dict[str, Any]) -> dict[str, str]:
    return {
        str(row.get("id")): str(row.get("status") or "UNKNOWN")
        for row in summary.get("results") or []
        if isinstance(row, dict) and str(row.get("id") or "").strip()
    }


def _validate_catalog(
    catalog: dict[str, Any], profile: str
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    if catalog.get("schema_version") != CATALOG_SCHEMA_VERSION:
        raise ConvergenceError("unsupported project requirement catalog schema")
    profiles = catalog.get("profiles")
    requirements = catalog.get("requirements")
    if not isinstance(profiles, dict) or not isinstance(requirements, list):
        raise ConvergenceError("project requirement catalog is incomplete")
    profile_ids = profiles.get(profile)
    if not isinstance(profile_ids, list) or not profile_ids:
        raise ConvergenceError(f"unknown or empty project requirement profile: {profile}")

    requirement_by_id: dict[str, dict[str, Any]] = {}
    for row in requirements:
        if not isinstance(row, dict):
            raise ConvergenceError("project requirement rows must be objects")
        requirement_id = str(row.get("id") or "").strip()
        if not requirement_id:
            raise ConvergenceError("project requirement is missing id")
        if requirement_id in requirement_by_id:
            raise ConvergenceError(f"duplicate project requirement id: {requirement_id}")
        requirement_by_id[requirement_id] = row

    normalized = [str(item) for item in profile_ids]
    if len(normalized) != len(set(normalized)):
        raise ConvergenceError(f"profile contains duplicate requirement ids: {profile}")
    missing = [item for item in normalized if item not in requirement_by_id]
    if missing:
        raise ConvergenceError(f"profile references missing requirements: {missing}")
    return normalized, requirement_by_id


def _validate_summary(
    summary: dict[str, Any], *, profile: str, expected_ref: str, catalog_path: str
) -> None:
    schema_version = summary.get("schema_version")
    if not isinstance(schema_version, int) or schema_version < MIN_SUMMARY_SCHEMA_VERSION:
        raise ConvergenceError("unsupported Quality run-summary schema")
    if summary.get("run_kind") != "verification":
        raise ConvergenceError("project convergence requires a Quality verification run")
    if str(summary.get("requirement_profile") or "") != profile:
        raise ConvergenceError(
            "Quality requirement profile mismatch: "
            f"expected={profile} actual={summary.get('requirement_profile')}"
        )
    if str(summary.get("requirement_catalog") or "") != catalog_path:
        raise ConvergenceError("Quality summary is bound to a different requirement catalog")
    target_identity = summary.get("target_identity")
    if not isinstance(target_identity, dict):
        raise ConvergenceError("Quality summary lacks target_identity")
    actual_ref = str(target_identity.get("change_ref") or "")
    if not expected_ref or actual_ref != expected_ref:
        raise ConvergenceError(
            f"stale or foreign Quality evidence: expected_ref={expected_ref} actual_ref={actual_ref}"
        )
    if not isinstance(summary.get("required_gate_ids"), list) or not summary.get("required_gate_ids"):
        raise ConvergenceError("Quality summary lacks required gate ids")
    if not isinstance(summary.get("results"), list):
        raise ConvergenceError("Quality summary lacks gate results")
    if not isinstance(summary.get("claim_results"), list):
        raise ConvergenceError("Quality summary lacks claim results")


def _requirement_findings(
    *,
    profile_ids: list[str],
    requirement_by_id: dict[str, dict[str, Any]],
    claim_by_id: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], set[str]]:
    findings: list[dict[str, Any]] = []
    referenced_failed_gates: set[str] = set()
    for requirement_id in profile_ids:
        requirement = requirement_by_id[requirement_id]
        claim = claim_by_id.get(requirement_id)
        if claim is None:
            key = _finding_key("REQUIREMENT_EVIDENCE_MISSING", requirement_id=requirement_id)
            findings.append(
                {
                    "id": f"PF-{key}",
                    "key": key,
                    "kind": "REQUIREMENT_EVIDENCE_MISSING",
                    "risk": _risk(requirement.get("risk")),
                    "requirement_id": requirement_id,
                    "statement": str(requirement.get("statement") or ""),
                    "owner": str(requirement.get("owner") or "unassigned"),
                    "failed_gates": [],
                    "evidence_refs": [],
                    "disposition": "REPLAN_REQUIRED",
                    "reason": "active project requirement has no current claim result",
                }
            )
            continue
        if str(claim.get("status") or "") == "VERIFIED":
            continue

        claim_gate_statuses = (
            claim.get("gate_statuses") if isinstance(claim.get("gate_statuses"), dict) else {}
        )
        failed_gates = [
            str(gate_id)
            for gate_id, status in claim_gate_statuses.items()
            if str(status) != "PASS"
        ]
        referenced_failed_gates.update(failed_gates)
        environment_blocked = [
            str(item) for item in claim.get("environment_blocked_gates") or []
        ]
        if environment_blocked:
            kind = "REQUIREMENT_ENVIRONMENT_BLOCKED"
            disposition = "RETRY_ENVIRONMENT"
            reason = "requirement evidence is blocked by an external environment prerequisite"
        elif failed_gates:
            kind = "REQUIREMENT_VERIFICATION_FAILED"
            disposition = "GOVERNED_REPAIR"
            reason = "one or more gates required by this project requirement did not pass"
        else:
            kind = "REQUIREMENT_UNVERIFIED"
            disposition = "REPLAN_REQUIRED"
            reason = "requirement is unverified without an explicit failing gate"
        key = _finding_key(kind, requirement_id=requirement_id)
        findings.append(
            {
                "id": f"PF-{key}",
                "key": key,
                "kind": kind,
                "risk": _risk(requirement.get("risk") or claim.get("risk")),
                "requirement_id": requirement_id,
                "statement": str(requirement.get("statement") or claim.get("statement") or ""),
                "owner": str(requirement.get("owner") or claim.get("owner") or "unassigned"),
                "failed_gates": failed_gates,
                "evidence_refs": [str(item) for item in claim.get("evidence_refs") or []],
                "disposition": disposition,
                "reason": reason,
            }
        )
    return findings, referenced_failed_gates


def _unmapped_gate_findings(
    *,
    summary: dict[str, Any],
    referenced_failed_gates: set[str],
) -> list[dict[str, Any]]:
    gate_status = _gate_statuses(summary)
    results_by_id = {
        str(row.get("id")): row
        for row in summary.get("results") or []
        if isinstance(row, dict) and str(row.get("id") or "").strip()
    }
    findings: list[dict[str, Any]] = []
    for gate_id in [str(item) for item in summary.get("required_gate_ids") or []]:
        status = gate_status.get(gate_id)
        if status == "PASS" or gate_id in referenced_failed_gates:
            continue
        result = results_by_id.get(gate_id, {})
        metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
        environment_blocked = bool(
            status in ENVIRONMENT_STATUSES
            or metadata.get("environment_blocked") is True
            or result.get("blocking_reason") == "environment"
        )
        kind = "GATE_ENVIRONMENT_BLOCKED" if environment_blocked else "UNMAPPED_REQUIRED_GATE_FAILURE"
        disposition = "RETRY_ENVIRONMENT" if environment_blocked else "GOVERNED_REPAIR"
        key = _finding_key(kind, gate_id=gate_id)
        findings.append(
            {
                "id": f"PF-{key}",
                "key": key,
                "kind": kind,
                "risk": "P1",
                "requirement_id": "",
                "statement": str(result.get("name") or gate_id),
                "owner": str(result.get("owner") or "unassigned"),
                "failed_gates": [gate_id],
                "evidence_refs": [f"gate-log:{gate_id}"],
                "disposition": disposition,
                "reason": (
                    "required gate is blocked by environment"
                    if environment_blocked
                    else "required gate failed outside the current requirement finding mapping"
                ),
            }
        )
    return findings


def assess(
    *,
    summary: dict[str, Any],
    catalog: dict[str, Any],
    profile: str,
    expected_ref: str,
    catalog_path: str = DEFAULT_REQUIREMENTS_PATH,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if profile not in PROFILE_STAGE:
        raise ConvergenceError(f"unsupported convergence profile: {profile}")
    profile_ids, requirement_by_id = _validate_catalog(catalog, profile)
    _validate_summary(summary, profile=profile, expected_ref=expected_ref, catalog_path=catalog_path)

    claim_by_id = {
        str(row.get("id")): row
        for row in summary.get("claim_results") or []
        if isinstance(row, dict) and str(row.get("id") or "").strip()
    }
    findings, referenced_failed_gates = _requirement_findings(
        profile_ids=profile_ids,
        requirement_by_id=requirement_by_id,
        claim_by_id=claim_by_id,
    )
    findings.extend(
        _unmapped_gate_findings(
            summary=summary,
            referenced_failed_gates=referenced_failed_gates,
        )
    )

    if str(summary.get("decision") or "") != "PASS" and not findings:
        key = _finding_key("QUALITY_DECISION_FAILED")
        findings.append(
            {
                "id": f"PF-{key}",
                "key": key,
                "kind": "QUALITY_DECISION_FAILED",
                "risk": "P0",
                "requirement_id": "",
                "statement": "Quality decision failed without a more specific project finding",
                "owner": "quality-controller",
                "failed_gates": [],
                "evidence_refs": [],
                "disposition": "REPLAN_REQUIRED",
                "reason": "fail-closed: unexplained Quality failure cannot converge",
            }
        )

    findings.sort(
        key=lambda item: (
            RISK_RANK.get(str(item.get("risk")), 9),
            0 if item.get("disposition") == "GOVERNED_REPAIR" else 1,
            str(item.get("requirement_id") or ""),
            str(item.get("failed_gates") or ""),
            str(item.get("kind") or ""),
        )
    )

    gate_status = _gate_statuses(summary)
    required_gate_ids = [str(item) for item in summary.get("required_gate_ids") or []]
    verified_count = sum(
        1
        for requirement_id in profile_ids
        if isinstance(claim_by_id.get(requirement_id), dict)
        and str(claim_by_id[requirement_id].get("status") or "") == "VERIFIED"
    )
    complete_gate_set = all(gate_status.get(gate_id) == "PASS" for gate_id in required_gate_ids)
    summary_clean = bool(
        summary.get("decision") == "PASS"
        and summary.get("loop_status") == "CI_VERIFIED"
        and summary.get("completion_eligible") is True
        and complete_gate_set
        and verified_count == len(profile_ids)
        and not findings
    )

    if summary_clean:
        status, next_profile = PROFILE_STAGE[profile]
        next_action = "DONE" if status == "CONVERGED" else "RUN_NEXT_PROFILE"
    elif any(item.get("disposition") == "REPLAN_REQUIRED" for item in findings):
        status = "REPLAN_REQUIRED"
        next_profile = profile
        next_action = "REPLAN_REQUIREMENTS"
    elif findings and all(item.get("disposition") == "RETRY_ENVIRONMENT" for item in findings):
        status = "BLOCKED_BY_ENVIRONMENT"
        next_profile = profile
        next_action = "RETRY_ASSESSMENT"
    else:
        status = "REPAIR_REQUIRED"
        next_profile = profile
        next_action = "WAIT_GOVERNED_REPAIR"

    next_finding = next(
        (item for item in findings if item.get("disposition") == "GOVERNED_REPAIR"),
        findings[0] if findings else None,
    )
    assessment_basis = {
        "source_ref": expected_ref,
        "profile": profile,
        "workspace_snapshot_fingerprint": summary.get("workspace_snapshot_fingerprint"),
        "claim_manifest_fingerprint": summary.get("claim_manifest_fingerprint"),
        "requirement_catalog_fingerprint": summary.get("requirement_catalog_fingerprint"),
        "finding_keys": [item["key"] for item in findings],
    }
    assessment_fingerprint = _fingerprint(assessment_basis)
    state = {
        "schema": STATE_SCHEMA,
        "source_ref": expected_ref,
        "profile": profile,
        "assessment_fingerprint": assessment_fingerprint,
        "status": status,
        "project_converged": status == "CONVERGED",
        "production_closed": False,
        "release_authorized": False,
        "required_requirement_count": len(profile_ids),
        "verified_requirement_count": verified_count,
        "required_gate_count": len(required_gate_ids),
        "passed_required_gate_count": sum(
            1 for gate_id in required_gate_ids if gate_status.get(gate_id) == "PASS"
        ),
        "open_finding_count": len(findings),
        "next_profile": next_profile,
        "next_action": next_action,
        "next_finding": next_finding,
        "quality": {
            "mode": summary.get("mode"),
            "decision": summary.get("decision"),
            "loop_status": summary.get("loop_status"),
            "completion_eligible": summary.get("completion_eligible") is True,
            "workspace_snapshot_fingerprint": summary.get("workspace_snapshot_fingerprint"),
        },
    }
    findings_payload = {
        "schema": FINDINGS_SCHEMA,
        "source_ref": expected_ref,
        "profile": profile,
        "assessment_fingerprint": assessment_fingerprint,
        "findings": findings,
    }
    return state, findings_payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--requirements", required=True)
    parser.add_argument("--requirements-path", default=DEFAULT_REQUIREMENTS_PATH)
    parser.add_argument("--profile", required=True, choices=sorted(PROFILE_STAGE))
    parser.add_argument("--expected-ref", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--enforce", action="store_true")
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = output_dir / "project-convergence-state.json"
    findings_path = output_dir / "project-findings.json"
    try:
        state, findings = assess(
            summary=_load(Path(args.summary)),
            catalog=_load(Path(args.requirements)),
            profile=str(args.profile),
            expected_ref=str(args.expected_ref),
            catalog_path=str(args.requirements_path),
        )
        _write_json(state_path, state)
        _write_json(findings_path, findings)
        print(
            json.dumps(
                {
                    "status": state["status"],
                    "profile": state["profile"],
                    "open_findings": state["open_finding_count"],
                    "next_action": state["next_action"],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        if args.enforce and state["status"] not in CLEAN_STATUSES:
            return 3
        return 0
    except (OSError, json.JSONDecodeError, ValueError, ConvergenceError) as exc:
        blocked = {
            "schema": STATE_SCHEMA,
            "source_ref": str(args.expected_ref),
            "profile": str(args.profile),
            "status": "BLOCKED_EVIDENCE",
            "project_converged": False,
            "production_closed": False,
            "release_authorized": False,
            "error": str(exc),
        }
        _write_json(state_path, blocked)
        print(json.dumps(blocked, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
