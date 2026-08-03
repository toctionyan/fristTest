from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

LEDGER_RELATIVE_PATH = Path("governance/task-ledger.json")
SCHEMA_RELATIVE_PATH = Path("governance/task-ledger.schema.json")
STAGE_STATUSES = {
    "OPEN",
    "IN_PROGRESS",
    "BLOCKED",
    "CLOSED_VERIFIED",
    "DEFERRED",
    "CANCELLED",
    "SUPERSEDED",
}
WORK_PACKAGE_STATUSES = STAGE_STATUSES
TERMINAL_EXCEPTION_STATUSES = {"DEFERRED", "CANCELLED", "SUPERSEDED"}
REQUIRED_STAGE_IDS = tuple(f"STAGE-{index}" for index in range(1, 7))
REQUIRED_WORK_PACKAGE_IDS = tuple(f"WP-{index:02d}" for index in range(1, 10))


@dataclass(frozen=True)
class LedgerValidation:
    payload: dict[str, Any]
    errors: tuple[str, ...]

    @property
    def status(self) -> str:
        return "PASS" if not self.errors else "FAIL"


def _load_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"task ledger is missing: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"task ledger is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("task ledger must be a JSON object")
    return payload


def _nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_list(value: object) -> list[str] | None:
    if not isinstance(value, list) or any(not _nonempty_text(item) for item in value):
        return None
    return [str(item) for item in value]


def _check_existing_refs(workspace: Path, refs: object, label: str, errors: list[str]) -> None:
    values = _string_list(refs)
    if values is None or not values:
        errors.append(f"{label} requires at least one evidence_ref")
        return
    for raw in values:
        resolved = (workspace / raw).resolve()
        try:
            resolved.relative_to(workspace.resolve())
        except ValueError:
            errors.append(f"{label} evidence_ref escapes workspace: {raw}")
            continue
        if not resolved.exists():
            errors.append(f"{label} evidence_ref does not exist: {raw}")


def _validate_graph(nodes: dict[str, dict[str, Any]], dependency_key: str, label: str, errors: list[str]) -> None:
    graph: dict[str, set[str]] = {}
    known = set(nodes)
    for node_id, row in nodes.items():
        raw_dependencies = row.get(dependency_key, [])
        dependencies = _string_list(raw_dependencies)
        if dependencies is None:
            errors.append(f"{label} {node_id} has invalid {dependency_key}")
            dependencies = []
        unknown = sorted(set(dependencies) - known)
        if unknown:
            errors.append(f"{label} {node_id} depends on unknown ids: {unknown}")
        if node_id in dependencies:
            errors.append(f"{label} {node_id} depends on itself")
        graph[node_id] = set(dependencies) & known
    remaining = {key: set(value) for key, value in graph.items()}
    while remaining:
        ready = sorted(key for key, dependencies in remaining.items() if not dependencies)
        if not ready:
            errors.append(f"{label} dependency graph contains a cycle: {sorted(remaining)}")
            return
        for key in ready:
            remaining.pop(key, None)
        for dependencies in remaining.values():
            dependencies.difference_update(ready)


def _index_rows(rows: object, key: str, label: str, errors: list[str]) -> dict[str, dict[str, Any]]:
    if not isinstance(rows, list):
        errors.append(f"task ledger requires {label} array")
        return {}
    indexed: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"{label}[{index}] must be an object")
            continue
        node_id = row.get(key)
        if not _nonempty_text(node_id):
            errors.append(f"{label}[{index}] requires {key}")
            continue
        node_id = str(node_id)
        if node_id in indexed:
            errors.append(f"duplicate {label} id: {node_id}")
            continue
        indexed[node_id] = row
    return indexed


def _terminal_semantics(
    workspace: Path,
    row: dict[str, Any],
    *,
    node_id: str,
    status: str,
    must_close: bool,
    errors: list[str],
) -> None:
    if must_close and status in TERMINAL_EXCEPTION_STATUSES:
        errors.append(f"must-close item {node_id} cannot be {status}")
    if status == "CLOSED_VERIFIED":
        if row.get("blockers"):
            errors.append(f"closed item {node_id} cannot retain blockers")
        _check_existing_refs(workspace, row.get("evidence_refs"), node_id, errors)
    elif status == "BLOCKED":
        blockers = _string_list(row.get("blockers"))
        if blockers is None or not blockers:
            errors.append(f"blocked item {node_id} requires blockers")
    elif status in TERMINAL_EXCEPTION_STATUSES:
        decision_record = row.get("decision_record")
        if not _nonempty_text(decision_record):
            errors.append(f"{status} item {node_id} requires decision_record")
        else:
            _check_existing_refs(workspace, [decision_record], f"{node_id} decision", errors)


def validate_payload(workspace: Path, payload: dict[str, Any]) -> LedgerValidation:
    workspace = workspace.resolve()
    errors: list[str] = []
    if payload.get("schema_version") != 1:
        errors.append("task ledger schema_version must be 1")
    for field in ("ledger_id", "project", "frozen_at"):
        if not _nonempty_text(payload.get(field)):
            errors.append(f"task ledger requires {field}")
    if payload.get("active_stage_id") is not None and not _nonempty_text(payload.get("active_stage_id")):
        errors.append("active_stage_id must be a non-empty stage id or null")

    policy = payload.get("policy")
    if not isinstance(policy, dict):
        errors.append("task ledger requires policy")
        policy = {}
    required_stage_ids = tuple(str(value) for value in policy.get("required_stage_ids") or [])
    required_work_package_ids = tuple(str(value) for value in policy.get("required_work_package_ids") or [])
    if required_stage_ids != REQUIRED_STAGE_IDS:
        errors.append(f"policy required_stage_ids must be {list(REQUIRED_STAGE_IDS)}")
    if required_work_package_ids != REQUIRED_WORK_PACKAGE_IDS:
        errors.append(f"policy required_work_package_ids must be {list(REQUIRED_WORK_PACKAGE_IDS)}")
    if policy.get("terminal_states_require_evidence") is not True:
        errors.append("policy terminal_states_require_evidence must be true")
    if policy.get("max_loop_iterations_are_not_success") is not True:
        errors.append("policy max_loop_iterations_are_not_success must be true")

    stages = _index_rows(payload.get("stages"), "stage_id", "stages", errors)
    packages = _index_rows(payload.get("work_packages"), "work_package_id", "work_packages", errors)
    if tuple(sorted(stages)) != REQUIRED_STAGE_IDS:
        errors.append(f"ledger stage ids must be exactly {list(REQUIRED_STAGE_IDS)}")
    if tuple(sorted(packages)) != REQUIRED_WORK_PACKAGE_IDS:
        errors.append(f"ledger work-package ids must be exactly {list(REQUIRED_WORK_PACKAGE_IDS)}")

    _validate_graph(stages, "depends_on", "stage", errors)
    _validate_graph(packages, "depends_on", "work package", errors)

    raw_active_stage_id = payload.get("active_stage_id")
    active_stage_id = str(raw_active_stage_id) if raw_active_stage_id is not None else ""
    if active_stage_id and active_stage_id not in stages:
        errors.append(f"active_stage_id is unknown: {active_stage_id}")

    stage_package_ownership: dict[str, set[str]] = {stage_id: set() for stage_id in stages}
    for package_id, row in packages.items():
        stage_id = str(row.get("stage_id") or "")
        if stage_id not in stages:
            errors.append(f"work package {package_id} has unknown stage_id: {stage_id}")
        else:
            stage_package_ownership[stage_id].add(package_id)
        status = str(row.get("status") or "")
        if status not in WORK_PACKAGE_STATUSES:
            errors.append(f"work package {package_id} has invalid status: {status}")
        for field in ("title", "goal", "owner", "closure_contract"):
            if not _nonempty_text(row.get(field)):
                errors.append(f"work package {package_id} requires {field}")
        must_close = row.get("must_close") is True
        if row.get("must_close") not in {True, False}:
            errors.append(f"work package {package_id} requires boolean must_close")
        _terminal_semantics(workspace, row, node_id=package_id, status=status, must_close=must_close, errors=errors)
        dependencies = [str(value) for value in row.get("depends_on") or [] if isinstance(value, str)]
        if status in {"IN_PROGRESS", "CLOSED_VERIFIED"}:
            for dependency in dependencies:
                dep_status = str(packages.get(dependency, {}).get("status") or "")
                if dep_status != "CLOSED_VERIFIED":
                    errors.append(f"work package {package_id} is {status} before dependency {dependency} is CLOSED_VERIFIED")

    in_progress_stages: list[str] = []
    for stage_id, row in stages.items():
        status = str(row.get("status") or "")
        if status not in STAGE_STATUSES:
            errors.append(f"stage {stage_id} has invalid status: {status}")
        if status == "IN_PROGRESS":
            in_progress_stages.append(stage_id)
        for field in ("title", "goal", "closure_contract"):
            if not _nonempty_text(row.get(field)):
                errors.append(f"stage {stage_id} requires {field}")
        listed = _string_list(row.get("work_package_ids"))
        if listed is None:
            errors.append(f"stage {stage_id} has invalid work_package_ids")
            listed = []
        expected = stage_package_ownership.get(stage_id, set())
        if set(listed) != expected:
            errors.append(f"stage {stage_id} work_package_ids do not match owned packages")
        _terminal_semantics(workspace, row, node_id=stage_id, status=status, must_close=True, errors=errors)
        if status == "CLOSED_VERIFIED":
            incomplete = sorted(
                package_id
                for package_id in expected
                if str(packages.get(package_id, {}).get("status") or "") != "CLOSED_VERIFIED"
            )
            if incomplete:
                errors.append(f"closed stage {stage_id} has incomplete work packages: {incomplete}")
        dependencies = [str(value) for value in row.get("depends_on") or [] if isinstance(value, str)]
        if status in {"IN_PROGRESS", "CLOSED_VERIFIED"}:
            for dependency in dependencies:
                dep_status = str(stages.get(dependency, {}).get("status") or "")
                if dep_status != "CLOSED_VERIFIED":
                    errors.append(f"stage {stage_id} is {status} before dependency {dependency} is CLOSED_VERIFIED")

    if len(in_progress_stages) > 1:
        errors.append(f"only one stage may be IN_PROGRESS: {in_progress_stages}")
    if in_progress_stages and active_stage_id != in_progress_stages[0]:
        errors.append("active_stage_id must identify the single IN_PROGRESS stage")

    first_unclosed_stage = next(
        (stage_id for stage_id in REQUIRED_STAGE_IDS if str(stages.get(stage_id, {}).get("status") or "") != "CLOSED_VERIFIED"),
        None,
    )
    if first_unclosed_stage is None:
        if raw_active_stage_id is not None:
            errors.append("active_stage_id must be null after every required stage is CLOSED_VERIFIED")
    elif active_stage_id != first_unclosed_stage:
        errors.append(f"active_stage_id must identify the first unclosed stage: {first_unclosed_stage}")

    if not in_progress_stages and active_stage_id:
        active_status = str(stages.get(active_stage_id, {}).get("status") or "")
        if active_status not in {"OPEN", "BLOCKED"}:
            errors.append("active_stage_id without an IN_PROGRESS stage must reference OPEN or BLOCKED")

    issues = _index_rows(payload.get("known_issues", []), "issue_id", "known_issues", errors)
    for issue_id, row in issues.items():
        if str(row.get("status") or "") not in STAGE_STATUSES:
            errors.append(f"known issue {issue_id} has invalid status")
        owner = str(row.get("owner_work_package_id") or "")
        if owner not in packages:
            errors.append(f"known issue {issue_id} has unknown owner_work_package_id: {owner}")
        if not _nonempty_text(row.get("description")):
            errors.append(f"known issue {issue_id} requires description")
        refs = row.get("evidence_refs")
        if refs:
            _check_existing_refs(workspace, refs, f"known issue {issue_id}", errors)

    decisions = _index_rows(payload.get("scope_decisions", []), "decision_id", "scope_decisions", errors)
    for decision_id, row in decisions.items():
        status = str(row.get("status") or "")
        if status not in TERMINAL_EXCEPTION_STATUSES:
            errors.append(f"scope decision {decision_id} must be DEFERRED, CANCELLED or SUPERSEDED")
        for field in ("subject", "reason", "decision_record"):
            if not _nonempty_text(row.get(field)):
                errors.append(f"scope decision {decision_id} requires {field}")
        if _nonempty_text(row.get("decision_record")):
            _check_existing_refs(workspace, [row["decision_record"]], f"scope decision {decision_id}", errors)

    return LedgerValidation(payload=payload, errors=tuple(errors))


def validate(workspace: Path, path: Path | None = None) -> LedgerValidation:
    workspace = workspace.resolve()
    ledger_path = path.resolve() if path else workspace / LEDGER_RELATIVE_PATH
    try:
        payload = _load_object(ledger_path)
    except ValueError as exc:
        return LedgerValidation(payload={}, errors=(str(exc),))
    return validate_payload(workspace, payload)


def summary(payload: dict[str, Any]) -> dict[str, Any]:
    stages = payload.get("stages") if isinstance(payload.get("stages"), list) else []
    packages = payload.get("work_packages") if isinstance(payload.get("work_packages"), list) else []
    def counts(rows: Iterable[object]) -> dict[str, int]:
        result = {status: 0 for status in sorted(STAGE_STATUSES)}
        for row in rows:
            if isinstance(row, dict):
                status = str(row.get("status") or "")
                result[status] = result.get(status, 0) + 1
        return {key: value for key, value in result.items() if value}
    must_close_open = [
        str(row.get("work_package_id"))
        for row in packages
        if isinstance(row, dict)
        and row.get("must_close") is True
        and row.get("status") != "CLOSED_VERIFIED"
    ]
    return {
        "ledger_id": payload.get("ledger_id"),
        "active_stage_id": payload.get("active_stage_id"),
        "stage_counts": counts(stages),
        "work_package_counts": counts(packages),
        "remaining_must_close": must_close_open,
        "all_required_closed": not must_close_open,
    }
