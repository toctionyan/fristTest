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
                detail=_text(result.get("stderr"))[:500] or None if status == "FAIL" else None,
                evidence_ref=(f"quality-gate:{result.get('id')}" if result.get("id") else None),
            )
        )
    return projections


def _first_failure(stages: Iterable[StageProjection]) -> dict[str, Any] | None:
    for stage in stages:
        if stage.status in {"FAIL", "BLOCKED"}:
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
    if any(stage.status in {"FAIL", "BLOCKED"} for stage in rows):
        return "FAIL"
    if any(stage.status in {"RUNNING", "PENDING"} for stage in rows):
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


def build_execution_progress(
    *,
    task: Mapping[str, Any] | None = None,
    github_jobs: Iterable[Mapping[str, Any]] = (),
    github_steps: Iterable[Mapping[str, Any]] = (),
    github_step_job_name: str = "github",
    quality_results: Iterable[Mapping[str, Any]] = (),
    run_id: int | None = None,
    workflow: str | None = None,
    head_sha: str | None = None,
) -> dict[str, Any]:
    """Project durable execution facts without acquiring any control authority.

    This function is intentionally read-only. It may summarize TaskRun, Quality,
    and GitHub evidence, but it cannot mutate a task, authorize a repair, dispatch
    a workflow, reinterpret a failed gate as PASS, or declare product completion.
    """

    stages = [
        *project_quality_results(quality_results),
        *project_github_jobs(github_jobs),
        *project_github_steps(github_steps, job_name=github_step_job_name),
    ]

    task_payload = dict(task or {})
    task_status = _text(task_payload.get("status")) or None
    task_phase = _text(task_payload.get("phase")) or None
    task_id = _text(task_payload.get("task_id")) or None
    metadata = task_payload.get("metadata") if isinstance(task_payload.get("metadata"), Mapping) else {}
    local_first = metadata.get("local_first") if isinstance(metadata.get("local_first"), Mapping) else {}
    counters = local_first.get("counters") if isinstance(local_first.get("counters"), Mapping) else {}
    budgets = local_first.get("budgets") if isinstance(local_first.get("budgets"), Mapping) else {}

    progress: dict[str, Any] = {
        "schema": EXECUTION_PROGRESS_SCHEMA,
        "authority_effect": False,
        "task": {
            "task_id": task_id,
            "status": task_status,
            "phase": task_phase,
        },
        "overall": _overall(stages),
        "product_verdict": _product_verdict(stages),
        "transport_verdict": _transport_verdict(stages),
        "current_stage": _current_stage(stages),
        "first_failure": _first_failure(stages),
        "stages": [stage.as_dict() for stage in stages],
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
    """Render the projection deterministically for chat/CLI/GitHub summaries."""

    icon = {
        "PASS": "✅",
        "FAIL": "❌",
        "RUNNING": "🟡",
        "PENDING": "⬜",
        "SKIPPED": "⏭️",
        "BLOCKED": "⛔",
    }
    lines: list[str] = []
    for row in progress.get("stages") or []:
        if not isinstance(row, Mapping):
            continue
        status = _text(row.get("status")).upper()
        label = _text(row.get("label")) or _text(row.get("id")) or "unnamed stage"
        lines.append(f"{icon.get(status, '❔')} {label}")
    current = _text(progress.get("current_stage"))
    if current:
        lines.append(f"当前阶段：{current}")
    failure = progress.get("first_failure")
    if isinstance(failure, Mapping):
        lines.append(f"首次失败：{_text(failure.get('label')) or _text(failure.get('stage_id'))}")
    lines.append(f"产品判定：{_text(progress.get('product_verdict')) or 'UNKNOWN'}")
    lines.append(f"执行/传输判定：{_text(progress.get('transport_verdict')) or 'UNKNOWN'}")
    return "\n".join(lines)
