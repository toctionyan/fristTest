from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

TRANSITION_KINDS = {"repair", "migration", "revert"}
CASE_FILES = {
    "failure": "failure-case.json",
    "root_cause": "root-cause-proof.json",
    "plan": "repair-plan.json",
    "plan_review": "plan-review.json",
    "baseline": "baseline-manifest.json",
    "permit": "change-permit.json",
    "diff_review": "diff-review.json",
    "closure": "closure-matrix.json",
}
MANDATORY_CLOSURE_DIMENSIONS = {
    "original_failure",
    "focused_tests",
    "counterexamples",
    "regression",
    "negative_paths",
    "runtime_trace",
    "authority_boundary",
    "diff_review",
}
FINAL_PASS_RESULT = "CONVERGED"
FINAL_PASS_DECISION = "CLOSED_VERIFIED"
IGNORED_PARTS = {".git", ".quality", "__pycache__", ".pytest_cache", ".venv", "node_modules"}
IGNORED_SUFFIXES = {".pyc", ".pyo"}
DYNAMIC_EXCLUSIONS = ("governance/repair-cases/**", "governance/active-change.json")
TARGET_CURRENT_ROUND_RE = re.compile(
    r"^(?P<prefix>\s*-\s*当前轮次\s*[:：]\s*)(?P<round>\d+)(?P<suffix>\s*)$",
    re.MULTILINE,
)
TARGET_MAX_ROUNDS_RE = re.compile(r"^\s*-\s*最大轮次\s*[:：]\s*(?P<round>\d+)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class GovernanceChain:
    case_dir: Path
    failure: dict[str, Any]
    root_cause: dict[str, Any]
    plan: dict[str, Any]
    plan_review: dict[str, Any]
    baseline: dict[str, Any]
    permit: dict[str, Any]
    diff_review: dict[str, Any] | None = None
    closure: dict[str, Any] | None = None

    @property
    def permit_digest(self) -> str:
        return str(self.permit.get("permit_digest") or "")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def payload_digest(value: dict[str, Any], *, exclude: Iterable[str] = ()) -> str:
    payload = {key: item for key, item in value.items() if key not in set(exclude)}
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _quality_target_round_record(
    workspace: Path,
    contract_payload: dict[str, Any],
) -> dict[str, Any] | None:
    raw = contract_payload.get("quality_target")
    if not isinstance(raw, str) or not raw.strip():
        return None
    path = _safe_relative(workspace, raw, label="quality_target")
    relative = path.relative_to(workspace.resolve()).as_posix()
    if not relative.startswith("governance/targets/") or path.suffix.lower() != ".md":
        raise ValueError("quality_target must be a workspace governance/targets/*.md file")
    body = path.read_text(encoding="utf-8")
    current_matches = list(TARGET_CURRENT_ROUND_RE.finditer(body))
    maximum_matches = list(TARGET_MAX_ROUNDS_RE.finditer(body))
    if len(current_matches) != 1 or len(maximum_matches) != 1:
        raise ValueError("quality_target must declare exactly one 当前轮次 and 最大轮次")
    current_round = int(current_matches[0].group("round"))
    max_rounds = int(maximum_matches[0].group("round"))
    normalized = TARGET_CURRENT_ROUND_RE.sub(
        lambda match: f"{match.group('prefix')}<round>{match.group('suffix')}",
        body,
        count=1,
    )
    return {
        "path": relative,
        "normalized_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        "current_round": current_round,
        "max_rounds": max_rounds,
    }


def _round_bookkeeping_changes(
    workspace: Path,
    contract_payload: dict[str, Any],
    baseline: dict[str, Any],
    changed_paths: Iterable[str],
) -> list[dict[str, Any]]:
    frozen = baseline.get("quality_target_round_bookkeeping")
    if not isinstance(frozen, dict):
        return []
    path = str(frozen.get("path") or "")
    if path not in set(changed_paths) or contract_payload.get("quality_target") != path:
        return []
    try:
        current = _quality_target_round_record(workspace, contract_payload)
    except (OSError, UnicodeError, ValueError):
        return []
    if current is None:
        return []
    baseline_round = frozen.get("current_round")
    current_round = current.get("current_round")
    max_rounds = current.get("max_rounds")
    if (
        current.get("path") != path
        or current.get("normalized_sha256") != frozen.get("normalized_sha256")
        or max_rounds != frozen.get("max_rounds")
        or not isinstance(baseline_round, int)
        or not isinstance(current_round, int)
        or not isinstance(max_rounds, int)
        or current_round <= baseline_round
        or not 1 <= current_round <= max_rounds
    ):
        return []
    return [{"path": path, "baseline_round": baseline_round, "current_round": current_round}]


def _load_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"required governance record is missing: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"governance record is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"governance record must be a JSON object: {path}")
    return payload


def _safe_relative(workspace: Path, raw: object, *, label: str, must_exist: bool = True) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{label} is required")
    path = (workspace / raw).resolve()
    try:
        path.relative_to(workspace.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} must stay inside the workspace") from exc
    if must_exist and not path.exists():
        raise ValueError(f"{label} does not exist: {path}")
    return path


def _record(path: Path, *, expected_type: str, change_id: str) -> dict[str, Any]:
    payload = _load_object(path)
    if payload.get("schema_version") != 1:
        raise ValueError(f"unsupported schema_version in {path}")
    if payload.get("record_type") != expected_type:
        raise ValueError(f"record_type mismatch in {path}: expected {expected_type}")
    if str(payload.get("change_id") or "") != change_id:
        raise ValueError(f"change_id mismatch in {path}")
    return payload


def _require_nonempty_list(payload: dict[str, Any], key: str, *, minimum: int = 1) -> list[Any]:
    value = payload.get(key)
    if not isinstance(value, list) or len(value) < minimum:
        raise ValueError(f"{payload.get('record_type')} requires {key} with at least {minimum} item(s)")
    return value


def _validate_evidence_refs(workspace: Path, refs: object, *, label: str) -> None:
    if not isinstance(refs, list) or not refs:
        raise ValueError(f"{label} requires evidence_refs")
    for raw in refs:
        _safe_relative(workspace, raw, label=f"{label} evidence")


def _matches(path: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def _is_workspace_file(path: Path, workspace: Path) -> bool:
    rel_path = path.relative_to(workspace)
    rel = rel_path.as_posix()
    if any(part in IGNORED_PARTS for part in rel_path.parts):
        return False
    if path.suffix in IGNORED_SUFFIXES:
        return False
    if _matches(rel, DYNAMIC_EXCLUSIONS):
        return False
    return path.is_file()


def _test_metrics(text: str) -> dict[str, int]:
    return {
        "test_definitions": len(re.findall(r"^\s*(?:async\s+)?def\s+test_|^\s*class\s+Test", text, re.MULTILINE)),
        "assertions": len(re.findall(r"\bassert\b|self\.assert[A-Z]", text)),
        "skip_markers": len(re.findall(r"pytest\.mark\.skip|pytest\.skip|unittest\.skip|@skip", text)),
        "mock_markers": len(re.findall(r"\bMock\b|\bMagicMock\b|\bpatch\(", text)),
    }


def _file_record(path: Path, rel: str) -> dict[str, Any]:
    data = path.read_bytes()
    result: dict[str, Any] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    if rel.startswith("tests/") or "/tests/" in rel or rel.startswith("skill-system/tests/"):
        result["test_metrics"] = _test_metrics(data.decode("utf-8", errors="ignore"))
    return result


def capture_workspace_manifest(workspace: Path) -> dict[str, dict[str, Any]]:
    workspace = workspace.resolve()
    return {
        path.relative_to(workspace).as_posix(): _file_record(path, path.relative_to(workspace).as_posix())
        for path in sorted(workspace.rglob("*"), key=lambda item: item.as_posix())
        if _is_workspace_file(path, workspace)
    }


def capture_allowed_manifest(
    workspace: Path,
    allowed_paths: Iterable[str],
    *,
    workspace_files: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    files = workspace_files if workspace_files is not None else capture_workspace_manifest(workspace)
    patterns = tuple(str(value) for value in allowed_paths)
    selected = {rel: value for rel, value in files.items() if _matches(rel, patterns)}
    for pattern in patterns:
        if not any(char in pattern for char in "*?[") and pattern not in selected:
            selected[pattern] = {"exists": False}
    return dict(sorted(selected.items()))


def manifest_fingerprint(files: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(files)).hexdigest()


def case_dir_from_contract(workspace: Path, contract_payload: dict[str, Any]) -> Path:
    return _safe_relative(
        workspace,
        contract_payload.get("repair_governance"),
        label="repair_governance case directory",
    )


def _validate_failure(workspace: Path, payload: dict[str, Any]) -> None:
    classification = str(payload.get("classification") or "")
    if classification not in {
        "implementation-defect",
        "architecture-defect",
        "skill-gap",
        "test-oracle-defect",
        "environment",
    }:
        raise ValueError("failure-case has invalid classification")
    reproduction = payload.get("reproduction")
    if not isinstance(reproduction, dict) or reproduction.get("status") not in {"REPRODUCED", "BLOCKED_BY_ENVIRONMENT"}:
        raise ValueError("failure-case requires a reproduced or environment-blocked reproduction")
    if reproduction.get("status") == "REPRODUCED":
        for key in ("expected", "actual"):
            if not str(reproduction.get(key) or "").strip():
                raise ValueError(f"failure-case reproduction requires {key}")
    _validate_evidence_refs(workspace, reproduction.get("evidence_refs"), label="failure-case")
    _require_nonempty_list(payload, "violated_invariants")
    _require_nonempty_list(payload, "affected_boundaries")


def _validate_root_cause(workspace: Path, payload: dict[str, Any], failure_path: Path) -> None:
    if payload.get("failure_case_sha256") != file_sha256(failure_path):
        raise ValueError("root-cause-proof is not bound to the current failure-case")
    if payload.get("decision") not in {"PROVEN", "UNPROVEN", "ORACLE_REVIEW_REQUIRED", "ENVIRONMENT_BLOCKED"}:
        raise ValueError("root-cause-proof has invalid decision")
    if not str(payload.get("root_cause") or "").strip():
        raise ValueError("root-cause-proof requires root_cause")
    _require_nonempty_list(payload, "causal_chain", minimum=2)
    _validate_evidence_refs(workspace, payload.get("evidence_refs"), label="root-cause-proof")
    _require_nonempty_list(payload, "rejected_hypotheses")
    _require_nonempty_list(payload, "affected_boundaries")


def _validate_plan(payload: dict[str, Any], root_path: Path) -> None:
    if payload.get("root_cause_proof_sha256") != file_sha256(root_path):
        raise ValueError("repair-plan is not bound to the current root-cause-proof")
    if payload.get("status") not in {"PROPOSED", "APPROVED", "REJECTED"}:
        raise ValueError("repair-plan has invalid status")
    if not str(payload.get("strategy") or "").strip():
        raise ValueError("repair-plan requires strategy")
    changes = _require_nonempty_list(payload, "changes")
    for row in changes:
        if not isinstance(row, dict) or not all(str(row.get(key) or "").strip() for key in ("path", "responsibility", "reason")):
            raise ValueError("repair-plan changes require path, responsibility and reason")
    for key in ("unchanged_boundaries", "forbidden_repairs", "required_invariants", "risks"):
        _require_nonempty_list(payload, key)
    tests = payload.get("required_tests")
    if not isinstance(tests, dict):
        raise ValueError("repair-plan requires required_tests")
    for key in ("focused", "counterexamples", "regression", "negative_path"):
        if not isinstance(tests.get(key), list) or not tests[key]:
            raise ValueError(f"repair-plan required_tests requires {key}")
    if not str(payload.get("rollback_plan") or "").strip():
        raise ValueError("repair-plan requires rollback_plan")


def _validate_plan_review(payload: dict[str, Any], plan_path: Path) -> None:
    if payload.get("repair_plan_sha256") != file_sha256(plan_path):
        raise ValueError("plan-review is not bound to the current repair-plan")
    if payload.get("reviewer_role") != "repair-plan-reviewer":
        raise ValueError("plan-review must be issued by repair-plan-reviewer")
    if payload.get("decision") not in {
        "APPROVED",
        "REJECTED_ROOT_CAUSE_UNPROVEN",
        "REJECTED_SKILL_VIOLATION",
        "REJECTED_PATCH_LIKE_FIX",
        "REJECTED_SCOPE_ERROR",
        "REJECTED_MISSING_COUNTEREXAMPLES",
        "REJECTED_DUAL_AUTHORITY",
    }:
        raise ValueError("plan-review has invalid decision")
    _require_nonempty_list(payload, "skill_rule_mappings")
    approved = payload.get("approved_paths")
    if payload.get("decision") == "APPROVED" and (not isinstance(approved, list) or not approved):
        raise ValueError("approved plan-review requires approved_paths")


def _validate_baseline(payload: dict[str, Any]) -> None:
    workspace_files = payload.get("workspace_files")
    allowed_files = payload.get("allowed_files")
    if not isinstance(workspace_files, dict) or not isinstance(allowed_files, dict):
        raise ValueError("baseline-manifest requires workspace_files and allowed_files")
    if payload.get("workspace_fingerprint") != manifest_fingerprint(workspace_files):
        raise ValueError("baseline workspace_fingerprint is invalid")
    if payload.get("source_fingerprint") != manifest_fingerprint(allowed_files):
        raise ValueError("baseline source_fingerprint is invalid")
    bookkeeping = payload.get("quality_target_round_bookkeeping")
    if bookkeeping is not None:
        if not isinstance(bookkeeping, dict):
            raise ValueError("baseline quality_target_round_bookkeeping must be an object")
        path = str(bookkeeping.get("path") or "")
        digest = str(bookkeeping.get("normalized_sha256") or "")
        current_round = bookkeeping.get("current_round")
        max_rounds = bookkeeping.get("max_rounds")
        if (
            not path.startswith("governance/targets/")
            or not path.endswith(".md")
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or not isinstance(current_round, int)
            or not isinstance(max_rounds, int)
            or not 1 <= current_round <= max_rounds
        ):
            raise ValueError("baseline quality_target_round_bookkeeping is invalid")


def _validate_permit(
    payload: dict[str, Any],
    *,
    plan_path: Path,
    review_path: Path,
    baseline_path: Path,
    plan: dict[str, Any],
    plan_review: dict[str, Any],
    baseline: dict[str, Any],
    contract_payload: dict[str, Any],
) -> None:
    if payload.get("repair_plan_sha256") != file_sha256(plan_path):
        raise ValueError("change-permit is not bound to the current repair-plan")
    if payload.get("plan_review_sha256") != file_sha256(review_path):
        raise ValueError("change-permit is not bound to the current plan-review")
    if payload.get("baseline_manifest_sha256") != file_sha256(baseline_path):
        raise ValueError("change-permit is not bound to the current baseline-manifest")
    if payload.get("permit_digest") != payload_digest(payload, exclude={"permit_digest"}):
        raise ValueError("change-permit digest is invalid")
    if payload.get("status") != "ACTIVE" or payload.get("expires_after") != "single-verification":
        raise ValueError("change-permit is not active for a single verification")
    if payload.get("issued_by") != "repair-plan-reviewer":
        raise ValueError("change-permit must be issued by repair-plan-reviewer")
    if plan_review.get("decision") != "APPROVED":
        raise ValueError("change-permit requires an approved plan-review")
    allowed = [str(value) for value in payload.get("allowed_paths") or []]
    approved = [str(value) for value in plan_review.get("approved_paths") or []]
    if allowed != approved:
        raise ValueError("change-permit scope differs from the approved plan scope")
    contract_allowed = [str(value) for value in contract_payload.get("allowed_paths") or []]
    for value in allowed:
        if not _matches(value, contract_allowed) and value not in contract_allowed:
            raise ValueError(f"change-permit path is outside the Change Contract: {value}")
    contract_forbidden = [str(value) for value in contract_payload.get("forbidden_paths") or []]
    permit_forbidden = [str(value) for value in payload.get("forbidden_paths") or []]
    for value in allowed:
        if _matches(value, contract_forbidden) or _matches(value, permit_forbidden):
            raise ValueError(f"change-permit allows a forbidden path: {value}")
    if payload.get("baseline_source_fingerprint") != baseline.get("source_fingerprint"):
        raise ValueError("change-permit baseline source fingerprint is invalid")
    if payload.get("baseline_workspace_fingerprint") != baseline.get("workspace_fingerprint"):
        raise ValueError("change-permit baseline workspace fingerprint is invalid")
    if payload.get("required_tests") != plan.get("required_tests"):
        raise ValueError("change-permit required tests differ from the approved repair-plan")


def load_chain(
    workspace: Path,
    contract_payload: dict[str, Any],
    *,
    include_diff: bool = False,
    include_closure: bool = False,
) -> GovernanceChain:
    workspace = workspace.resolve()
    change_id = str(contract_payload.get("change_id") or "")
    if str(contract_payload.get("target_kind") or "") not in TRANSITION_KINDS:
        raise ValueError("repair governance applies only to repair, migration or revert")
    case_dir = case_dir_from_contract(workspace, contract_payload)
    try:
        case_dir.relative_to(workspace / "governance" / "repair-cases")
    except ValueError as exc:
        raise ValueError("repair governance case must live under governance/repair-cases") from exc

    paths = {key: case_dir / filename for key, filename in CASE_FILES.items()}
    failure = _record(paths["failure"], expected_type="failure-case", change_id=change_id)
    root_cause = _record(paths["root_cause"], expected_type="root-cause-proof", change_id=change_id)
    plan = _record(paths["plan"], expected_type="repair-plan", change_id=change_id)
    plan_review = _record(paths["plan_review"], expected_type="plan-review", change_id=change_id)
    baseline = _record(paths["baseline"], expected_type="baseline-manifest", change_id=change_id)
    permit = _record(paths["permit"], expected_type="change-permit", change_id=change_id)

    _validate_failure(workspace, failure)
    _validate_root_cause(workspace, root_cause, paths["failure"])
    _validate_plan(plan, paths["root_cause"])
    _validate_plan_review(plan_review, paths["plan"])
    _validate_baseline(baseline)
    _validate_permit(
        permit,
        plan_path=paths["plan"],
        review_path=paths["plan_review"],
        baseline_path=paths["baseline"],
        plan=plan,
        plan_review=plan_review,
        baseline=baseline,
        contract_payload=contract_payload,
    )

    diff_review = None
    if include_diff:
        diff_review = _record(paths["diff_review"], expected_type="diff-review", change_id=change_id)
        validate_diff_review(workspace, contract_payload, permit, baseline, diff_review)
    closure = None
    if include_closure:
        if diff_review is None:
            diff_review = _record(paths["diff_review"], expected_type="diff-review", change_id=change_id)
            validate_diff_review(workspace, contract_payload, permit, baseline, diff_review)
        closure = _record(paths["closure"], expected_type="closure-matrix", change_id=change_id)
        validate_closure(workspace, contract_payload, permit, diff_review, closure)
    return GovernanceChain(case_dir, failure, root_cause, plan, plan_review, baseline, permit, diff_review, closure)


def validate_begin_ready(workspace: Path, contract_payload: dict[str, Any]) -> dict[str, Any]:
    chain = load_chain(workspace, contract_payload)
    reproduction = chain.failure.get("reproduction") or {}
    if reproduction.get("status") != "REPRODUCED":
        raise ValueError("writable transition requires a reproduced failure")
    if chain.root_cause.get("decision") != "PROVEN":
        raise ValueError("writable transition requires a proven root cause")
    if chain.plan_review.get("decision") != "APPROVED":
        raise ValueError("writable transition requires an approved repair plan")
    return {
        "status": "PASS",
        "change_id": contract_payload.get("change_id"),
        "permit_digest": chain.permit_digest,
        "allowed_paths": list(chain.permit.get("allowed_paths") or []),
    }


def permit_path_decision(
    workspace: Path,
    contract_payload: dict[str, Any],
    path: str,
) -> tuple[bool, str]:
    try:
        chain = load_chain(workspace, contract_payload)
    except ValueError as exc:
        return False, str(exc)
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    forbidden = [str(value) for value in chain.permit.get("forbidden_paths") or []]
    allowed = [str(value) for value in chain.permit.get("allowed_paths") or []]
    if _matches(normalized, forbidden):
        return False, f"path is forbidden by ChangePermit: {normalized}"
    if not _matches(normalized, allowed):
        return False, f"path is outside ChangePermit allowed_paths: {normalized}"
    return True, "allowed by active ChangePermit"


def create_permit(workspace: Path, contract_payload: dict[str, Any]) -> Path:
    workspace = workspace.resolve()
    change_id = str(contract_payload.get("change_id") or "")
    case_dir = case_dir_from_contract(workspace, contract_payload)
    paths = {key: case_dir / filename for key, filename in CASE_FILES.items()}
    failure = _record(paths["failure"], expected_type="failure-case", change_id=change_id)
    root_cause = _record(paths["root_cause"], expected_type="root-cause-proof", change_id=change_id)
    plan = _record(paths["plan"], expected_type="repair-plan", change_id=change_id)
    plan_review = _record(paths["plan_review"], expected_type="plan-review", change_id=change_id)
    _validate_failure(workspace, failure)
    _validate_root_cause(workspace, root_cause, paths["failure"])
    _validate_plan(plan, paths["root_cause"])
    _validate_plan_review(plan_review, paths["plan"])
    if root_cause.get("decision") != "PROVEN" or plan_review.get("decision") != "APPROVED":
        raise ValueError("permit issuance requires a proven root cause and approved plan")

    approved = [str(value) for value in plan_review.get("approved_paths") or []]
    contract_allowed = [str(value) for value in contract_payload.get("allowed_paths") or []]
    for value in approved:
        if not _matches(value, contract_allowed) and value not in contract_allowed:
            raise ValueError(f"approved path is outside the Change Contract: {value}")

    workspace_files = capture_workspace_manifest(workspace)
    allowed_files = capture_allowed_manifest(workspace, approved, workspace_files=workspace_files)
    baseline = {
        "schema_version": 1,
        "record_type": "baseline-manifest",
        "change_id": change_id,
        "workspace_files": workspace_files,
        "allowed_files": allowed_files,
        "source_fingerprint": manifest_fingerprint(allowed_files),
        "workspace_fingerprint": manifest_fingerprint(workspace_files),
    }
    target_round = _quality_target_round_record(workspace, contract_payload)
    if target_round is not None:
        baseline["quality_target_round_bookkeeping"] = target_round
    paths["baseline"].write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    permit = {
        "schema_version": 1,
        "record_type": "change-permit",
        "change_id": change_id,
        "repair_plan_sha256": file_sha256(paths["plan"]),
        "plan_review_sha256": file_sha256(paths["plan_review"]),
        "baseline_manifest_sha256": file_sha256(paths["baseline"]),
        "allowed_paths": approved,
        "forbidden_paths": list(dict.fromkeys(str(value) for value in contract_payload.get("forbidden_paths") or [])),
        "forbidden_patterns": plan.get("forbidden_patterns") or [],
        "required_tests": plan.get("required_tests"),
        "baseline_source_fingerprint": baseline["source_fingerprint"],
        "baseline_workspace_fingerprint": baseline["workspace_fingerprint"],
        "issued_by": "repair-plan-reviewer",
        "expires_after": "single-verification",
        "status": "ACTIVE",
    }
    permit["permit_digest"] = payload_digest(permit)
    paths["permit"].write_text(json.dumps(permit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    load_chain(workspace, contract_payload)
    return paths["permit"]


def _pattern_rows(raw: object) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, value in enumerate(raw if isinstance(raw, list) else []):
        if isinstance(value, str):
            rows.append({"id": f"pattern-{index + 1}", "pattern": value, "include": ["**"], "exclude": []})
        elif isinstance(value, dict) and str(value.get("pattern") or "").strip():
            rows.append({
                "id": str(value.get("id") or f"pattern-{index + 1}"),
                "pattern": str(value["pattern"]),
                "include": [str(item) for item in value.get("include") or ["**"]],
                "exclude": [str(item) for item in value.get("exclude") or []],
            })
    return rows


def _test_integrity_findings(
    baseline_files: dict[str, dict[str, Any]],
    current_files: dict[str, dict[str, Any]],
    changed_paths: Iterable[str],
) -> list[str]:
    findings: list[str] = []
    for rel in changed_paths:
        baseline = baseline_files.get(rel)
        current = current_files.get(rel)
        baseline_metrics = baseline.get("test_metrics") if isinstance(baseline, dict) else None
        current_metrics = current.get("test_metrics") if isinstance(current, dict) else None
        is_test = rel.startswith("tests/") or "/tests/" in rel or rel.startswith("skill-system/tests/")
        if not is_test:
            continue
        if baseline and not current:
            findings.append(f"deleted_test_file:{rel}")
            continue
        if current and not baseline:
            if not isinstance(current_metrics, dict) or current_metrics.get("test_definitions", 0) < 1 or current_metrics.get("assertions", 0) < 1:
                findings.append(f"new_test_without_test_and_assertion:{rel}")
            continue
        if not isinstance(baseline_metrics, dict) or not isinstance(current_metrics, dict):
            continue
        if current_metrics.get("test_definitions", 0) < baseline_metrics.get("test_definitions", 0):
            findings.append(f"test_definition_count_decreased:{rel}")
        if current_metrics.get("assertions", 0) < baseline_metrics.get("assertions", 0):
            findings.append(f"assertion_count_decreased:{rel}")
        if current_metrics.get("skip_markers", 0) > baseline_metrics.get("skip_markers", 0):
            findings.append(f"skip_markers_increased:{rel}")
        if current_metrics.get("mock_markers", 0) > baseline_metrics.get("mock_markers", 0):
            findings.append(f"mock_markers_increased:{rel}")
    return findings


def _forbidden_pattern_findings(
    workspace: Path,
    changed_paths: Iterable[str],
    rows: list[dict[str, Any]],
) -> list[str]:
    findings: list[str] = []
    for rel in changed_paths:
        path = workspace / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for row in rows:
            if not _matches(rel, row["include"]) or _matches(rel, row["exclude"]):
                continue
            try:
                matched = re.search(row["pattern"], text, re.IGNORECASE | re.DOTALL)
            except re.error as exc:
                findings.append(f"invalid_forbidden_pattern:{row['id']}:{exc}")
                continue
            if matched:
                findings.append(f"forbidden_pattern:{row['id']}:{rel}")
    return findings


def compute_diff_review(
    workspace: Path,
    contract_payload: dict[str, Any],
    *,
    requested_decision: str = "PASS",
    reviewer_findings: list[str] | None = None,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    chain = load_chain(workspace, contract_payload)
    baseline_files = chain.baseline.get("workspace_files") or {}
    current_files = capture_workspace_manifest(workspace)
    all_paths = sorted(set(baseline_files) | set(current_files))
    raw_changed = [path for path in all_paths if baseline_files.get(path) != current_files.get(path)]
    round_bookkeeping = _round_bookkeeping_changes(
        workspace,
        contract_payload,
        chain.baseline,
        raw_changed,
    )
    bookkeeping_paths = {str(row["path"]) for row in round_bookkeeping}
    changed = [path for path in raw_changed if path not in bookkeeping_paths]
    added = [path for path in changed if path not in baseline_files]
    deleted = [path for path in changed if path not in current_files]
    modified = [path for path in changed if path in baseline_files and path in current_files]
    allowed = [str(value) for value in chain.permit.get("allowed_paths") or []]
    forbidden = [str(value) for value in chain.permit.get("forbidden_paths") or []]
    out_of_scope = [path for path in changed if not _matches(path, allowed) or _matches(path, forbidden)]
    deterministic_findings = []
    deterministic_findings.extend(f"out_of_scope_change:{path}" for path in out_of_scope)
    deterministic_findings.extend(_test_integrity_findings(baseline_files, current_files, changed))
    deterministic_findings.extend(
        _forbidden_pattern_findings(workspace, changed, _pattern_rows(chain.permit.get("forbidden_patterns")))
    )
    current_allowed = capture_allowed_manifest(workspace, allowed, workspace_files=current_files)
    candidate_fingerprint = manifest_fingerprint(current_allowed)
    if candidate_fingerprint == chain.permit.get("baseline_source_fingerprint"):
        deterministic_findings.append("no_permitted_candidate_change")
    decision = "PASS" if requested_decision == "PASS" and not deterministic_findings else "REJECT"
    return {
        "schema_version": 1,
        "record_type": "diff-review",
        "change_id": contract_payload.get("change_id"),
        "permit_digest": chain.permit_digest,
        "reviewer_role": "diff-integrity-reviewer",
        "requested_decision": requested_decision,
        "decision": decision,
        "baseline_source_fingerprint": chain.permit.get("baseline_source_fingerprint"),
        "candidate_source_fingerprint": candidate_fingerprint,
        "changed_paths": changed,
        "added_paths": added,
        "modified_paths": modified,
        "deleted_paths": deleted,
        "out_of_scope_paths": out_of_scope,
        "test_integrity_findings": [item for item in deterministic_findings if any(token in item for token in ("test_", "assertion", "skip_", "mock_"))],
        "forbidden_pattern_findings": [item for item in deterministic_findings if item.startswith(("forbidden_pattern", "invalid_forbidden_pattern"))],
        "deterministic_findings": deterministic_findings,
        "round_bookkeeping": round_bookkeeping,
        "reviewer_findings": list(reviewer_findings or []),
    }


def write_diff_review(
    workspace: Path,
    contract_payload: dict[str, Any],
    *,
    requested_decision: str = "PASS",
    reviewer_findings: list[str] | None = None,
) -> Path:
    if requested_decision not in {"PASS", "REJECT"}:
        raise ValueError("diff review decision must be PASS or REJECT")
    chain = load_chain(workspace, contract_payload)
    result = compute_diff_review(
        workspace,
        contract_payload,
        requested_decision=requested_decision,
        reviewer_findings=reviewer_findings,
    )
    path = chain.case_dir / CASE_FILES["diff_review"]
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    validate_diff_review(workspace, contract_payload, chain.permit, chain.baseline, result)
    return path


def validate_diff_review(
    workspace: Path,
    contract_payload: dict[str, Any],
    permit: dict[str, Any],
    baseline: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    if payload.get("permit_digest") != permit.get("permit_digest"):
        raise ValueError("diff-review is not bound to the active ChangePermit")
    if payload.get("reviewer_role") != "diff-integrity-reviewer":
        raise ValueError("diff-review must be issued by diff-integrity-reviewer")
    recomputed = compute_diff_review(
        workspace,
        contract_payload,
        requested_decision=str(payload.get("requested_decision") or "PASS"),
        reviewer_findings=[str(value) for value in payload.get("reviewer_findings") or []],
    )
    deterministic_fields = (
        "decision",
        "baseline_source_fingerprint",
        "candidate_source_fingerprint",
        "changed_paths",
        "added_paths",
        "modified_paths",
        "deleted_paths",
        "out_of_scope_paths",
        "test_integrity_findings",
        "forbidden_pattern_findings",
        "deterministic_findings",
        "round_bookkeeping",
    )
    for field in deterministic_fields:
        if payload.get(field) != recomputed.get(field):
            raise ValueError(f"diff-review deterministic field is stale or forged: {field}")
    if payload.get("decision") != "PASS":
        raise ValueError("diff-review did not PASS")
    if baseline.get("source_fingerprint") != permit.get("baseline_source_fingerprint"):
        raise ValueError("diff-review baseline identity is invalid")


def write_closure_matrix(
    workspace: Path,
    contract_payload: dict[str, Any],
    *,
    result: str,
    evidence: dict[str, str],
    loop_outcome: str,
    residual_risks: list[str] | None = None,
) -> Path:
    chain = load_chain(workspace, contract_payload, include_diff=True)
    rows = []
    for dimension, raw in sorted(evidence.items()):
        path = _safe_relative(workspace, raw, label=f"closure evidence {dimension}")
        rows.append({
            "dimension": dimension,
            "status": "PASS",
            "evidence": path.relative_to(workspace).as_posix(),
            "evidence_sha256": file_sha256(path),
        })
    current_files = capture_workspace_manifest(workspace)
    allowed = [str(value) for value in chain.permit.get("allowed_paths") or []]
    current_allowed = capture_allowed_manifest(workspace, allowed, workspace_files=current_files)
    final_decision = FINAL_PASS_DECISION if result == FINAL_PASS_RESULT else (
        "BLOCKED" if result == "BLOCKED_BY_ENVIRONMENT" else "NOT_CLOSED"
    )
    payload = {
        "schema_version": 1,
        "record_type": "closure-matrix",
        "change_id": contract_payload.get("change_id"),
        "permit_digest": chain.permit_digest,
        "diff_review_sha256": file_sha256(chain.case_dir / CASE_FILES["diff_review"]),
        "reviewer_role": "closure-arbiter",
        "result": result,
        "final_decision": final_decision,
        "loop_outcome": loop_outcome,
        "candidate_source_fingerprint": manifest_fingerprint(current_allowed),
        "evidence": rows,
        "residual_risks": list(residual_risks or []),
    }
    path = chain.case_dir / CASE_FILES["closure"]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    validate_closure(workspace, contract_payload, chain.permit, chain.diff_review or {}, payload)
    return path


def validate_closure(
    workspace: Path,
    contract_payload: dict[str, Any],
    permit: dict[str, Any],
    diff_review: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    if payload.get("permit_digest") != permit.get("permit_digest"):
        raise ValueError("closure-matrix is not bound to the active ChangePermit")
    case_dir = case_dir_from_contract(workspace, contract_payload)
    diff_path = case_dir / CASE_FILES["diff_review"]
    if payload.get("diff_review_sha256") != file_sha256(diff_path):
        raise ValueError("closure-matrix is not bound to the current diff-review")
    if payload.get("reviewer_role") != "closure-arbiter":
        raise ValueError("closure-matrix must be issued by closure-arbiter")
    if diff_review.get("decision") != "PASS":
        raise ValueError("closure requires a PASS diff-review")
    result = str(payload.get("result") or "")
    final_decision = str(payload.get("final_decision") or "")
    if result == FINAL_PASS_RESULT and final_decision != FINAL_PASS_DECISION:
        raise ValueError("CONVERGED requires CLOSED_VERIFIED")
    if result != FINAL_PASS_RESULT and final_decision == FINAL_PASS_DECISION:
        raise ValueError("only CONVERGED may be CLOSED_VERIFIED")
    if result == FINAL_PASS_RESULT and payload.get("loop_outcome") in {"STOPPED_MAX_REPAIRS", "BLOCKED_BY_ENVIRONMENT"}:
        raise ValueError("repair budget exhaustion or environment blocking cannot imply convergence")
    rows = payload.get("evidence")
    if not isinstance(rows, list):
        raise ValueError("closure-matrix requires evidence rows")
    dimensions: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("closure evidence rows must be objects")
        dimension = str(row.get("dimension") or "")
        dimensions.add(dimension)
        if row.get("status") != "PASS":
            raise ValueError(f"closure evidence did not PASS: {dimension}")
        path = _safe_relative(workspace, row.get("evidence"), label=f"closure evidence {dimension}")
        if row.get("evidence_sha256") != file_sha256(path):
            raise ValueError(f"closure evidence is missing or changed: {dimension}")
    missing = sorted(MANDATORY_CLOSURE_DIMENSIONS.difference(dimensions))
    if result == FINAL_PASS_RESULT and missing:
        raise ValueError("closure-matrix missing mandatory dimensions: " + ",".join(missing))
    current_files = capture_workspace_manifest(workspace)
    current_allowed = capture_allowed_manifest(
        workspace,
        [str(value) for value in permit.get("allowed_paths") or []],
        workspace_files=current_files,
    )
    if payload.get("candidate_source_fingerprint") != manifest_fingerprint(current_allowed):
        raise ValueError("governed source changed after closure evidence was recorded")
    if payload.get("candidate_source_fingerprint") != diff_review.get("candidate_source_fingerprint"):
        raise ValueError("closure source fingerprint differs from diff-review")


def validate_verification_ready(
    workspace: Path,
    contract_payload: dict[str, Any],
    *,
    expected_result: str,
) -> dict[str, Any]:
    chain = load_chain(workspace, contract_payload, include_diff=True, include_closure=True)
    if chain.closure is None or chain.diff_review is None:
        raise ValueError("repair governance completion records are missing")
    if chain.closure.get("result") != expected_result:
        raise ValueError("contract verification result differs from closure-matrix")
    if expected_result == FINAL_PASS_RESULT and chain.closure.get("final_decision") != FINAL_PASS_DECISION:
        raise ValueError("CONVERGED is not backed by CLOSED_VERIFIED")
    return {
        "status": "PASS",
        "permit_digest": chain.permit_digest,
        "diff_review": (chain.case_dir / CASE_FILES["diff_review"]).relative_to(workspace).as_posix(),
        "closure_matrix": (chain.case_dir / CASE_FILES["closure"]).relative_to(workspace).as_posix(),
        "candidate_source_fingerprint": chain.diff_review.get("candidate_source_fingerprint"),
        "final_decision": chain.closure.get("final_decision"),
    }
