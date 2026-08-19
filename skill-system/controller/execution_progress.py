from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


EXECUTION_PROGRESS_SCHEMA = "execution-progress@1"

STAGE_STATUSES = {
    "PENDING",
    "RUNNING",
    "PASS",
    "FAIL",
    "SKIPPED",
    "BLOCKED",
}

_TERMINAL_SUCCESS = {"success", "neutral"}
_TERMINAL_FAILURE = {"failure", "timed_out", "cancelled", "action_required", "startup_failure"}
_TERMINAL_SKIPPED = {"skipped"}
_FAILURE_STATUSES = {"FAIL", "BLOCKED"}
_ACTIVE_STATUSES = {"RUNNING", "PENDING"}


class ExecutionProgressError(ValueError):
    """Raised when progress evidence is malformed or internally inconsistent."""


@dataclass(frozen=True)
class StageProjection:
    stage_id: str
    label: str
    status: str
    source: str
    detail: str | None = None
    evidence_ref: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.stage_id,
            "label": self.label,
            "status": self.status,
            "source": self.source,
        }
        if self.detail:
            payload["detail"] = self.detail
        if self.evidence_ref:
            payload["evidence_ref"] = self.evidence_ref
        return payload


def _text(value: object) -> str:
    return str(value or "").strip()


def _normalize_status(value: object, *, conclusion: object = None) -> str:
    status = _text(value).casefold()
    terminal = _text(conclusion).casefold()
    if status in {"queued", "waiting", "pending", "requested"}:
        return "PENDING"
    if status in {"in_progress", "running"}:
        return "RUNNING"
    if status == "completed":
        if terminal in _TERMINAL_SUCCESS:
            return "PASS"
        if terminal in _TERMINAL_SKIPPED:
            return "SKIPPED"
        if terminal in _TERMINAL_FAILURE or terminal:
            return "FAIL"
        return "BLOCKED"
    if status.upper() in STAGE_STATUSES:
        return status.upper()
    if terminal in _TERMINAL_SUCCESS:
        return "PASS"
    if terminal in _TERMINAL_SKIPPED:
        return "SKIPPED"
    if terminal in _TERMINAL_FAILURE:
        return "FAIL"
    return "BLOCKED"


def _stage_id(value: object, *, fallback: str) -> str:
    raw = _text(value) or fallback
    normalized = []
    for char in raw.casefold():
        if char.isalnum():
            normalized.append(char)
        elif normalized and normalized[-1] != "-":
            normalized.append("-")
    result = "".join(normalized).strip("-")
    return result or fallback


def project_github_jobs(jobs: Iterable[Mapping[str, Any]]) -> list[StageProjection]:
    projections: list[StageProjection] = []
    for index, job in enumerate(jobs, start=1):
        label = _text(job.get("name")) or f"GitHub job {index}"
        projections.append(
            StageProjection(
                stage_id=_stage_id(job.get("name"), fallback=f"github-job-{index}"),
                label=label,
                status=_normalize_status(job.get("status"), conclusion=job.get("conclusion")),
                source="github-job",
                detail=_text(job.get("conclusion")) or None,
                evidence_ref=(f"github-job:{job.get('id')}" if job.get("id") else None),
            )
        )
    return projections


def project_github_steps(steps: Iterable[Mapping[str, Any]], *, job_name: str) -> list[StageProjection]:
    projections: list[StageProjection] = []
    for index, step in enumerate(steps, start=1):
        label = _text(step.get("name")) or f"Step {index}"
        projections.append(
            StageProjection(
                stage_id=_stage_id(f"{job_name}-{label}", fallback=f"github-step-{index}"),
                label=label,
                status=_normalize_status(step.get("status"), conclusion=step.get("conclusion")),
                source="github-step",
                detail=f"job={job_name}",
                evidence_ref=(
                    f"github-step:{job_name}:{step.get('number')}"
                    if step.get("number") is not None
                    else None
                ),
            )
        )
    return projections


def project_quality_results(results: Iterable[Mapping[str, Any]]) -> list[StageProjection]:
    projections: list[StageProjection] = []
    for index, result in enumerate(results, start=1):
        raw_status = _text(result.get("status")).upper()
        status = {
            "PASS": "PASS",
            "FAIL": "FAIL",
            "UPSTREAM_SKIPPED": "SKIPPED",
            "SKIPPED": "SKIPPED",
            "BLOCKED": "BLOCKED",
            "RUNNING": "RUNNING",
            "PENDING": "PENDING",
        }.get(raw_status, "BLOCKED")
        label = _text(result.get("name")) or _text(result.get("id")) or f"Quality gate {index}"
        projections.append(
            StageProjection(
                stage_id=_stage_id(result.get("id"), fallback=f"quality-gate-{index}"),
                label=label,
                status=status,
                source="quality-gate",
                detail=(_text(result.get("stderr"))[:500] or None) if status == "FAIL" else None,
                evidence_ref=(f"quality-gate:{result.get('id')}" if result.get("id") else None),
            )
        )
    return projections


def project_task_conditions(task: Mapping[str, Any]) -> list[StageProjection]:
    required = task.get("required_conditions") if isinstance(task.get("required_conditions"), list) else []
    conditions = task.get("conditions") if isinstance(task.get("conditions"), Mapping) else {}
    rows: list[StageProjection] = []
    for index, raw_name in enumerate(required, start=1):
        name = _text(raw_name)
        condition = conditions.get(name) if isinstance(conditions.get(name), Mapping) else {}
        satisfied = condition.get("satisfied") is True
        refs = condition.get("evidence_refs") if isinstance(condition.get("evidence_refs"), list) else []
        rows.append(
            StageProjection(
                stage_id=_stage_id(f"condition-{name}", fallback=f"task-condition-{index}"),
                label=name,
                status="PASS" if satisfied and refs else "PENDING",
                source="task-condition",
                detail=None if satisfied and refs else "required completion condition not yet satisfied",
                evidence_ref=(str(refs[0]) if satisfied and refs else None),
            )
        )
    return rows


def project_planned_stages(
    task: Mapping[str, Any],
    explicit: Iterable[Mapping[str, Any]] = (),
) -> list[StageProjection]:
    metadata = task.get("metadata") if isinstance(task.get("metadata"), Mapping) else {}
    plan = metadata.get("execution_plan") if isinstance(metadata.get("execution_plan"), Mapping) else {}
    configured = plan.get("stages") if isinstance(plan.get("stages"), list) else []
    source_rows = list(explicit) or [row for row in configured if isinstance(row, Mapping)]
    result: list[StageProjection] = []
    seen: set[str] = set()
    for index, row in enumerate(source_rows, start=1):
        stage_id = _stage_id(row.get("id") or row.get("label"), fallback=f"planned-stage-{index}")
        if stage_id in seen:
            raise ExecutionProgressError(f"duplicate planned stage id: {stage_id}")
        seen.add(stage_id)
        result.append(
            StageProjection(
                stage_id=stage_id,
                label=_text(row.get("label")) or stage_id,
                status=_normalize_status(row.get("status") or "PENDING"),
                source="task-plan",
                detail=_text(row.get("detail")) or None,
                evidence_ref=_text(row.get("evidence_ref")) or None,
            )
        )
    return result


def _merge_stage_projections(
    planned: Iterable[StageProjection],
    observed: Iterable[StageProjection],
) -> list[StageProjection]:
    result: list[StageProjection] = []
    positions: dict[str, int] = {}
    for stage in planned:
        positions[stage.stage_id] = len(result)
        result.append(stage)
    for stage in observed:
        position = positions.get(stage.stage_id)
        if position is None:
            positions[stage.stage_id] = len(result)
            result.append(stage)
            continue
        planned_stage = result[position]
        result[position] = StageProjection(
            stage_id=planned_stage.stage_id,
            label=planned_stage.label,
            status=stage.status,
            source=stage.source,
            detail=stage.detail,
            evidence_ref=stage.evidence_ref,
        )
    return result


def _first_failure(stages: Iterable[StageProjection]) -> dict[str, Any] | None:
    for stage in stages:
        if stage.status in _FAILURE_STATUSES:
            payload = {
                "stage_id": stage.stage_id,
                "label": stage.label,
                "status": stage.status,
                "source": stage.source,
            }
            if stage.detail:
                payload["detail"] = stage.detail
            if stage.evidence_ref:
                payload["evidence_ref"] = stage.evidence_ref
            return payload
    return None


def _current_stage(stages: Iterable[StageProjection]) -> str | None:
    rows = list(stages)
    for stage in rows:
        if stage.status == "RUNNING":
            return stage.stage_id
    for stage in rows:
        if stage.status == "PENDING":
            return stage.stage_id
    return None


def _overall(stages: Iterable[StageProjection]) -> str:
    statuses = [stage.status for stage in stages]
    if any(status == "FAIL" for status in statuses):
        return "FAILED"
    if any(status == "BLOCKED" for status in statuses):
        return "BLOCKED"
    if any(status == "RUNNING" for status in statuses):
        return "RUNNING"
    if any(status == "PENDING" for status in statuses):
        return "PENDING"
    if statuses and all(status == "SKIPPED" for status in statuses):
        return "SKIPPED"
    if any(status == "PASS" for status in statuses) and all(
        status in {"PASS", "SKIPPED"} for status in statuses
    ):
        return "COMPLETED"
    return "UNKNOWN"


def _verdict(stages: Iterable[StageProjection], *, sources: set[str]) -> str:
    rows = [stage for stage in stages if stage.source in sources]
    if not rows:
        return "UNKNOWN"
    if any(stage.status in _FAILURE_STATUSES for stage in rows):
        return "FAIL"
    if any(stage.status in _ACTIVE_STATUSES for stage in rows):
        return "RUNNING"
    if any(stage.status == "PASS" for stage in rows):
        return "PASS"
    if all(stage.status == "SKIPPED" for stage in rows):
        return "NOT_RUN"
    return "UNKNOWN"


def _product_verdict(stages: Iterable[StageProjection]) -> str:
    return _verdict(stages, sources={"quality-gate"})


def _transport_verdict(stages: Iterable[StageProjection]) -> str:
    return _verdict(stages, sources={"github-job", "github-step"})


def _normalize_attempt_history(attempts: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(attempts, start=1):
        stage_id = _stage_id(raw.get("stage_id") or raw.get("stage") or raw.get("label"), fallback=f"attempt-stage-{index}")
        status = _normalize_status(raw.get("status"), conclusion=raw.get("conclusion"))
        rows.append(
            {
                "sequence": int(raw.get("sequence") or index),
                "stage_id": stage_id,
                "label": _text(raw.get("label")) or stage_id,
                "attempt": int(raw.get("attempt") or index),
                "status": status,
                "detail": _text(raw.get("detail")) or None,
                "evidence_ref": _text(raw.get("evidence_ref")) or None,
                "human_required": raw.get("human_required") is True,
                "recoverable": raw.get("recoverable") is not False,
            }
        )
    rows.sort(key=lambda row: row["sequence"])
    return rows


def _attempt_failure_views(attempts: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in attempts:
        grouped.setdefault(row["stage_id"], []).append(row)
    recovered: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for stage_id, rows in grouped.items():
        failed = [row for row in rows if row["status"] in _FAILURE_STATUSES]
        if not failed:
            continue
        latest = rows[-1]
        summary = {
            "stage_id": stage_id,
            "label": latest["label"],
            "failed_attempts": [row["attempt"] for row in failed],
            "latest_attempt": latest["attempt"],
            "latest_status": latest["status"],
            "last_failure_detail": failed[-1].get("detail"),
            "last_failure_evidence_ref": failed[-1].get("evidence_ref"),
        }
        if latest["status"] in {"PASS", "SKIPPED"}:
            recovered.append(summary)
        else:
            unresolved.append(summary)
    return recovered, unresolved


def _latest_reconcile_outcome(task: Mapping[str, Any]) -> dict[str, Any] | None:
    metadata = task.get("metadata") if isinstance(task.get("metadata"), Mapping) else {}
    reconciler = metadata.get("engineering_reconciler") if isinstance(metadata.get("engineering_reconciler"), Mapping) else {}
    decisions = reconciler.get("decisions") if isinstance(reconciler.get("decisions"), Mapping) else {}
    key = _text(reconciler.get("last_delivery_key"))
    entry = decisions.get(key) if key and isinstance(decisions.get(key), Mapping) else None
    if entry is None and decisions:
        candidate = list(decisions.values())[-1]
        entry = candidate if isinstance(candidate, Mapping) else None
    if not isinstance(entry, Mapping):
        return None
    outcome = entry.get("outcome")
    return dict(outcome) if isinstance(outcome, Mapping) else None


def _task_completion(task: Mapping[str, Any]) -> tuple[bool | None, list[str]]:
    required = task.get("required_conditions") if isinstance(task.get("required_conditions"), list) else []
    if not required:
        return None, []
    conditions = task.get("conditions") if isinstance(task.get("conditions"), Mapping) else {}
    missing: list[str] = []
    for raw in required:
        name = _text(raw)
        row = conditions.get(name) if isinstance(conditions.get(name), Mapping) else {}
        refs = row.get("evidence_refs") if isinstance(row.get("evidence_refs"), list) else []
        if row.get("satisfied") is not True or not refs:
            missing.append(name)
    return not missing, missing


def build_execution_progress(
    *,
    task: Mapping[str, Any] | None = None,
    github_jobs: Iterable[Mapping[str, Any]] = (),
    github_steps: Iterable[Mapping[str, Any]] = (),
    github_step_job_name: str = "github",
    quality_results: Iterable[Mapping[str, Any]] = (),
    planned_stages: Iterable[Mapping[str, Any]] = (),
    attempt_history: Iterable[Mapping[str, Any]] = (),
    run_id: int | None = None,
    workflow: str | None = None,
    head_sha: str | None = None,
) -> dict[str, Any]:
    """Project durable task, attempt, Quality, and GitHub evidence without control authority.

    The projection is intentionally read-only. It keeps historical failed attempts
    visible even after recovery, distinguishes an automatically recoverable RED from
    a true human stop, and never declares the whole task complete while a required
    completion condition is still missing.
    """

    task_payload = dict(task or {})
    planned = [
        *project_planned_stages(task_payload, planned_stages),
        *project_task_conditions(task_payload),
    ]
    observed = [
        *project_quality_results(quality_results),
        *project_github_jobs(github_jobs),
        *project_github_steps(github_steps, job_name=github_step_job_name),
    ]
    stages = _merge_stage_projections(planned, observed)
    attempts = _normalize_attempt_history(attempt_history)
    recovered_failures, unresolved_attempt_failures = _attempt_failure_views(attempts)

    task_status = _text(task_payload.get("status")) or None
    task_phase = _text(task_payload.get("phase")) or None
    task_id = _text(task_payload.get("task_id")) or None
    metadata = task_payload.get("metadata") if isinstance(task_payload.get("metadata"), Mapping) else {}
    local_first = metadata.get("local_first") if isinstance(metadata.get("local_first"), Mapping) else {}
    counters = local_first.get("counters") if isinstance(local_first.get("counters"), Mapping) else {}
    budgets = local_first.get("budgets") if isinstance(local_first.get("budgets"), Mapping) else {}
    latest_outcome = _latest_reconcile_outcome(task_payload)

    stage_failure = _first_failure(stages)
    unresolved_failures = list(unresolved_attempt_failures)
    if stage_failure and not any(row.get("stage_id") == stage_failure["stage_id"] for row in unresolved_failures):
        unresolved_failures.append(dict(stage_failure))

    auto_recovery_active = bool(
        latest_outcome
        and latest_outcome.get("allowed") is True
        and latest_outcome.get("human_required") is not True
        and _text(latest_outcome.get("action"))
        and unresolved_failures
    )
    human_required = bool(
        task_status == "BLOCKED"
        or (
            latest_outcome
            and latest_outcome.get("human_required") is True
            and latest_outcome.get("allowed") is not True
        )
    )

    overall = _overall(stages)
    if human_required:
        overall = "BLOCKED"
    elif auto_recovery_active:
        overall = "RECOVERING"

    completion_eligible, missing_conditions = _task_completion(task_payload)
    if completion_eligible is False and overall == "COMPLETED":
        overall = "PENDING"
    if completion_eligible is True and any(stage.status in _ACTIVE_STATUSES | _FAILURE_STATUSES for stage in stages):
        completion_eligible = False
    if completion_eligible is None:
        completion_eligible = overall == "COMPLETED"

    task_plan_basis = [stage for stage in stages if stage.source == "task-plan"]
    condition_basis = [stage for stage in stages if stage.source == "task-condition"]
    progress_basis = task_plan_basis or condition_basis or stages
    completed_steps = sum(1 for stage in progress_basis if stage.status in {"PASS", "SKIPPED"})
    total_steps = len(progress_basis)

    blockers = task_payload.get("blockers") if isinstance(task_payload.get("blockers"), list) else []
    current_blocker = blockers[-1] if blockers and isinstance(blockers[-1], Mapping) and task_status == "BLOCKED" else None

    progress: dict[str, Any] = {
        "schema": EXECUTION_PROGRESS_SCHEMA,
        "authority_effect": False,
        "task": {
            "task_id": task_id,
            "status": task_status,
            "phase": task_phase,
        },
        "overall": overall,
        "completion_eligible": bool(completion_eligible),
        "missing_completion_conditions": missing_conditions,
        "product_verdict": _product_verdict(stages),
        "transport_verdict": _transport_verdict(stages),
        "current_stage": _current_stage(stages),
        "first_failure": stage_failure,
        "stages": [stage.as_dict() for stage in stages],
        "attempt_history": attempts,
        "recovered_failures": recovered_failures,
        "unresolved_failures": unresolved_failures,
        "recovery": {
            "active": auto_recovery_active,
            "latest_decision": dict(latest_outcome) if latest_outcome else None,
        },
        "human": {
            "required": human_required,
            "current_blocker": dict(current_blocker) if isinstance(current_blocker, Mapping) else None,
        },
        "summary": {
            "completed_steps": completed_steps,
            "total_steps": total_steps,
            "recovered_failure_count": len(recovered_failures),
            "unresolved_failure_count": len(unresolved_failures),
            "needs_user_action": human_required,
        },
        "loop": {
            "repair_round": counters.get("local_repair_rounds"),
            "max_repair_rounds": budgets.get("local_repair_rounds"),
            "verification_round": counters.get("local_verification_rounds"),
            "max_verification_rounds": budgets.get("local_verification_rounds"),
        },
        "github": {
            "run_id": run_id,
            "workflow": workflow,
            "head_sha": head_sha,
        },
    }
    return progress


def render_progress_text(progress: Mapping[str, Any]) -> str:
    """Render the full task projection deterministically for chat/CLI/GitHub summaries."""

    icon = {
        "PASS": "✅",
        "FAIL": "❌",
        "RUNNING": "🟡",
        "PENDING": "⬜",
        "SKIPPED": "⏭️",
        "BLOCKED": "⛔",
    }
    overall_icon = {
        "COMPLETED": "✅",
        "RUNNING": "🔄",
        "PENDING": "⏳",
        "RECOVERING": "🔧",
        "FAILED": "❌",
        "BLOCKED": "⛔",
        "SKIPPED": "⏭️",
        "UNKNOWN": "❔",
    }
    overall = _text(progress.get("overall")) or "UNKNOWN"
    summary = progress.get("summary") if isinstance(progress.get("summary"), Mapping) else {}
    lines: list[str] = [f"整体状态：{overall_icon.get(overall, '❔')} {overall}"]
    total = summary.get("total_steps")
    completed = summary.get("completed_steps")
    if isinstance(total, int) and total > 0 and isinstance(completed, int):
        lines.append(f"整体进度：{completed}/{total}")
    for row in progress.get("stages") or []:
        if not isinstance(row, Mapping):
            continue
        status = _text(row.get("status")).upper()
        label = _text(row.get("label")) or _text(row.get("id")) or "unnamed stage"
        lines.append(f"{icon.get(status, '❔')} {label}")
    current = _text(progress.get("current_stage"))
    if current:
        lines.append(f"当前阶段：{current}")

    recovered = progress.get("recovered_failures") if isinstance(progress.get("recovered_failures"), list) else []
    if recovered:
        lines.append(f"已自动恢复失败：{len(recovered)}")
        for row in recovered:
            if isinstance(row, Mapping):
                attempts = ",".join(str(item) for item in row.get("failed_attempts") or [])
                lines.append(f"↳ {(_text(row.get('label')) or _text(row.get('stage_id')))}：失败尝试 {attempts}，后续已恢复")

    unresolved = progress.get("unresolved_failures") if isinstance(progress.get("unresolved_failures"), list) else []
    if unresolved:
        lines.append(f"当前未解决失败：{len(unresolved)}")
        for row in unresolved:
            if isinstance(row, Mapping):
                lines.append(f"↳ {(_text(row.get('label')) or _text(row.get('stage_id')))}")

    recovery = progress.get("recovery") if isinstance(progress.get("recovery"), Mapping) else {}
    if recovery.get("active") is True:
        decision = recovery.get("latest_decision") if isinstance(recovery.get("latest_decision"), Mapping) else {}
        lines.append(f"自动恢复：进行中（{_text(decision.get('action')) or _text(decision.get('decision')) or 'bounded recovery'}）")

    human = progress.get("human") if isinstance(progress.get("human"), Mapping) else {}
    lines.append(f"需要你介入：{'是' if human.get('required') is True else '否'}")
    if human.get("required") is True and isinstance(human.get("current_blocker"), Mapping):
        blocker = human["current_blocker"]
        lines.append(f"阻塞原因：{_text(blocker.get('reason')) or _text(blocker.get('code'))}")

    lines.append(f"产品判定：{_text(progress.get('product_verdict')) or 'UNKNOWN'}")
    lines.append(f"执行/传输判定：{_text(progress.get('transport_verdict')) or 'UNKNOWN'}")
    return "\n".join(lines)
