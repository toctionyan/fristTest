from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

TASK_RUN_SCHEMA_VERSION = 1
RUN_STATUSES = {
    "CREATED",
    "PLANNED",
    "RUNNING",
    "WAITING_EXTERNAL_RESULT",
    "FAILED_RECOVERABLE",
    "REPAIRING",
    "VALIDATING",
    "COMPLETED",
    "BLOCKED",
    "CANCELLED",
}
TERMINAL_STATUSES = {"COMPLETED", "CANCELLED"}
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "CREATED": {"PLANNED", "RUNNING", "BLOCKED", "CANCELLED"},
    "PLANNED": {"RUNNING", "BLOCKED", "CANCELLED"},
    "RUNNING": {
        "RUNNING",
        "WAITING_EXTERNAL_RESULT",
        "FAILED_RECOVERABLE",
        "REPAIRING",
        "VALIDATING",
        "BLOCKED",
        "COMPLETED",
        "CANCELLED",
    },
    "WAITING_EXTERNAL_RESULT": {
        "RUNNING",
        "FAILED_RECOVERABLE",
        "VALIDATING",
        "BLOCKED",
        "CANCELLED",
    },
    "FAILED_RECOVERABLE": {"RUNNING", "REPAIRING", "VALIDATING", "BLOCKED", "CANCELLED"},
    "REPAIRING": {"RUNNING", "FAILED_RECOVERABLE", "VALIDATING", "BLOCKED", "CANCELLED"},
    "VALIDATING": {
        "RUNNING",
        "WAITING_EXTERNAL_RESULT",
        "FAILED_RECOVERABLE",
        "REPAIRING",
        "VALIDATING",
        "BLOCKED",
        "COMPLETED",
        "CANCELLED",
    },
    # BLOCKED is durable evidence, not a success state. A later invocation may
    # explicitly resume it after the external blocker has been addressed.
    "BLOCKED": {"RUNNING", "REPAIRING", "VALIDATING", "CANCELLED"},
    "COMPLETED": set(),
    "CANCELLED": set(),
}


class TaskRunError(RuntimeError):
    """Base error for durable task-run control failures."""


class TaskRunBindingError(TaskRunError):
    """Raised when a checkpoint belongs to another target or immutable input."""


class TaskRunDriftError(TaskRunError):
    """Raised when the governed workspace no longer matches the checkpoint."""


class TaskRunConflictError(TaskRunError):
    """Raised when another process advanced the durable task-run revision."""


class InvalidTaskTransitionError(TaskRunError):
    """Raised when the state machine is asked to perform an illegal transition."""


class PrematureCompletionError(TaskRunError):
    """Raised when success is requested without all required evidence."""


@dataclass(frozen=True)
class CompletionDecision:
    status: str
    missing_conditions: tuple[str, ...]
    invalid_conditions: tuple[str, ...]

    @property
    def eligible(self) -> bool:
        return self.status == "COMPLETED"


@dataclass(frozen=True)
class ActionPlan:
    decision: str
    action_key: str
    strategy: str | None
    attempted_strategies: tuple[str, ...]
    reason: str


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def stable_task_id(prefix: str, identity: Any) -> str:
    clean = "".join(char.lower() if char.isalnum() else "-" for char in str(prefix)).strip("-")
    clean = clean or "task"
    return f"{clean}-{fingerprint(identity)[:20]}"


def evaluate_completion(payload: dict[str, Any]) -> CompletionDecision:
    required = tuple(str(item) for item in payload.get("required_conditions") or [])
    conditions = payload.get("conditions") if isinstance(payload.get("conditions"), dict) else {}
    missing: list[str] = []
    invalid: list[str] = []
    for name in required:
        row = conditions.get(name)
        if not isinstance(row, dict) or row.get("satisfied") is not True:
            missing.append(name)
            continue
        evidence = row.get("evidence_refs")
        if not isinstance(evidence, list) or not evidence or any(not str(item).strip() for item in evidence):
            invalid.append(name)
    return CompletionDecision(
        status="COMPLETED" if not missing and not invalid else "RUNNING",
        missing_conditions=tuple(missing),
        invalid_conditions=tuple(invalid),
    )


def _validate_payload(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != TASK_RUN_SCHEMA_VERSION:
        raise TaskRunError("unsupported task-run schema_version")
    if not str(payload.get("task_id") or "").strip():
        raise TaskRunError("task-run requires task_id")
    status = str(payload.get("status") or "")
    if status not in RUN_STATUSES:
        raise TaskRunError(f"invalid task-run status: {status}")
    if not isinstance(payload.get("binding"), dict) or not payload["binding"]:
        raise TaskRunError("task-run requires immutable binding")
    required = payload.get("required_conditions")
    if not isinstance(required, list) or not required or len(set(map(str, required))) != len(required):
        raise TaskRunError("task-run requires unique required_conditions")
    if not isinstance(payload.get("conditions"), dict):
        raise TaskRunError("task-run requires conditions object")
    if not isinstance(payload.get("checkpoints"), list):
        raise TaskRunError("task-run requires checkpoints array")
    if not isinstance(payload.get("action_attempts"), list):
        raise TaskRunError("task-run requires action_attempts array")
    if not isinstance(payload.get("revision"), int) or int(payload["revision"]) < 0:
        raise TaskRunError("task-run requires a non-negative revision")


class TaskRunStore:
    """Atomic, resumable task-run ledger with completion and retry guards."""

    def __init__(self, path: Path, payload: dict[str, Any]) -> None:
        self.path = path.resolve()
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.payload = payload
        _validate_payload(self.payload)

    @classmethod
    def open_or_create(
        cls,
        path: Path,
        *,
        task_id: str,
        task_kind: str,
        binding: dict[str, Any],
        required_conditions: Iterable[str],
        current_workspace_fingerprint: str | None = None,
    ) -> "TaskRunStore":
        path = path.resolve()
        normalized_binding = json.loads(_canonical_json(binding))
        required = [str(item) for item in required_conditions]
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            _validate_payload(payload)
            if payload.get("task_id") != task_id:
                raise TaskRunBindingError("task-run task_id does not match the requested task")
            if payload.get("task_kind") != task_kind:
                raise TaskRunBindingError("task-run task_kind does not match the requested task")
            if payload.get("binding") != normalized_binding:
                raise TaskRunBindingError("task-run immutable binding changed")
            if list(payload.get("required_conditions") or []) != required:
                raise TaskRunBindingError("task-run completion contract changed")
            store = cls(path, payload)
            if current_workspace_fingerprint:
                store.assert_resume_fingerprint(current_workspace_fingerprint)
            return store

        now = _now()
        payload = {
            "schema_version": TASK_RUN_SCHEMA_VERSION,
            "task_id": task_id,
            "task_kind": task_kind,
            "status": "CREATED",
            "phase": "CREATED",
            "revision": 0,
            "binding": normalized_binding,
            "required_conditions": required,
            "conditions": {
                name: {"satisfied": False, "evidence_refs": [], "updated_at": None}
                for name in required
            },
            "metadata": {},
            "checkpoints": [],
            "action_attempts": [],
            "blockers": [],
            "created_at": now,
            "updated_at": now,
        }
        store = cls(path, payload)
        store.checkpoint(
            status="CREATED",
            phase="CREATED",
            workspace_fingerprint=current_workspace_fingerprint,
            evidence_refs=[],
            metadata={"event": "task-run-created"},
        )
        return store

    def _locked_write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            local_revision = int(self.payload.get("revision") or 0)
            if self.path.is_file():
                disk_payload = json.loads(self.path.read_text(encoding="utf-8"))
                disk_revision = int(disk_payload.get("revision") or 0)
                if disk_revision != local_revision:
                    raise TaskRunConflictError(
                        f"task-run revision changed concurrently: local={local_revision} disk={disk_revision}"
                    )
            self.payload["revision"] = local_revision + 1
            fd, temporary = tempfile.mkstemp(
                prefix=self.path.name + ".",
                suffix=".tmp",
                dir=str(self.path.parent),
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(self.payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    def reload(self) -> None:
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        _validate_payload(payload)
        self.payload = payload

    def assert_resume_fingerprint(self, current_workspace_fingerprint: str) -> None:
        checkpoints = self.payload.get("checkpoints") or []
        if not checkpoints:
            return
        latest = checkpoints[-1]
        expected = str(latest.get("workspace_fingerprint") or "")
        if not expected or expected == current_workspace_fingerprint:
            return
        # A fixer may have changed governed source immediately before the host
        # process was interrupted.  FIXER_RUNNING is the sole phase where such
        # drift is expected and must be reconciled from the immutable baseline
        # instead of rejected or blindly rerun.  Read-only validation phases are
        # never allowed to drift.
        if str(latest.get("phase") or "") == "FIXER_RUNNING":
            return
        raise TaskRunDriftError(
            "governed workspace fingerprint changed after the last durable checkpoint"
        )

    def checkpoint(
        self,
        *,
        status: str,
        phase: str,
        workspace_fingerprint: str | None,
        evidence_refs: Iterable[str] = (),
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if status not in RUN_STATUSES:
            raise InvalidTaskTransitionError(f"unknown task-run status: {status}")
        current = str(self.payload.get("status") or "CREATED")
        if status != current and status not in ALLOWED_TRANSITIONS.get(current, set()):
            raise InvalidTaskTransitionError(f"illegal task-run transition: {current} -> {status}")
        refs = [str(item) for item in evidence_refs if str(item).strip()]
        checkpoint = {
            "sequence": len(self.payload.get("checkpoints") or []) + 1,
            "status": status,
            "phase": str(phase),
            "workspace_fingerprint": str(workspace_fingerprint or ""),
            "evidence_refs": refs,
            "metadata": dict(metadata or {}),
            "created_at": _now(),
        }
        self.payload["status"] = status
        self.payload["phase"] = str(phase)
        self.payload.setdefault("checkpoints", []).append(checkpoint)
        self.payload["updated_at"] = checkpoint["created_at"]
        self._locked_write()
        return checkpoint

    def set_metadata(self, **values: Any) -> None:
        self.payload.setdefault("metadata", {}).update(values)
        self.payload["updated_at"] = _now()
        self._locked_write()

    def mark_condition(self, name: str, *, evidence_refs: Iterable[str]) -> None:
        conditions = self.payload.get("conditions") or {}
        if name not in conditions:
            raise TaskRunError(f"unknown completion condition: {name}")
        refs = [str(item) for item in evidence_refs if str(item).strip()]
        if not refs:
            raise TaskRunError(f"completion condition {name} requires evidence")
        conditions[name] = {
            "satisfied": True,
            "evidence_refs": refs,
            "updated_at": _now(),
        }
        self.payload["conditions"] = conditions
        self.payload["updated_at"] = _now()
        self._locked_write()

    def completion_decision(self) -> CompletionDecision:
        return evaluate_completion(self.payload)

    def plan_action(
        self,
        *,
        action_name: str,
        arguments: dict[str, Any],
        state_fingerprint: str,
        strategies: Iterable[str],
        max_attempts_per_strategy: int = 2,
    ) -> ActionPlan:
        if max_attempts_per_strategy < 1:
            raise ValueError("max_attempts_per_strategy must be positive")
        strategy_list = tuple(str(item) for item in strategies if str(item).strip())
        if not strategy_list:
            raise ValueError("at least one action strategy is required")
        action_key = fingerprint(
            {
                "action_name": action_name,
                "arguments": arguments,
                "state_fingerprint": state_fingerprint,
            }
        )
        attempts = [
            row
            for row in self.payload.get("action_attempts") or []
            if row.get("action_key") == action_key
        ]
        attempted: list[str] = []
        for index, strategy in enumerate(strategy_list):
            count = sum(1 for row in attempts if row.get("strategy") == strategy)
            if count:
                attempted.append(strategy)
            if count < max_attempts_per_strategy:
                return ActionPlan(
                    decision="RUN" if index == 0 else "SWITCH_FALLBACK",
                    action_key=action_key,
                    strategy=strategy,
                    attempted_strategies=tuple(attempted),
                    reason=(
                        "primary-strategy-available"
                        if index == 0
                        else "prior-strategy-budget-exhausted"
                    ),
                )
        return ActionPlan(
            decision="BLOCKED",
            action_key=action_key,
            strategy=None,
            attempted_strategies=tuple(strategy_list),
            reason="all-strategy-budgets-exhausted",
        )

    def record_action_result(
        self,
        plan: ActionPlan,
        *,
        result: Any,
        produced_new_evidence: bool,
        evidence_refs: Iterable[str] = (),
    ) -> dict[str, Any]:
        if plan.strategy is None:
            raise TaskRunError("cannot record a result for a blocked action plan")
        row = {
            "sequence": len(self.payload.get("action_attempts") or []) + 1,
            "action_key": plan.action_key,
            "strategy": plan.strategy,
            "decision": plan.decision,
            "result_fingerprint": fingerprint(result),
            "produced_new_evidence": bool(produced_new_evidence),
            "evidence_refs": [str(item) for item in evidence_refs if str(item).strip()],
            "created_at": _now(),
        }
        self.payload.setdefault("action_attempts", []).append(row)
        self.payload["updated_at"] = row["created_at"]
        self._locked_write()
        return row

    def block(
        self,
        *,
        code: str,
        reason: str,
        attempted_strategies: Iterable[str],
        next_action: str,
        workspace_fingerprint: str | None,
        evidence_refs: Iterable[str] = (),
    ) -> None:
        blocker = {
            "code": str(code),
            "reason": str(reason),
            "attempted_strategies": [str(item) for item in attempted_strategies],
            "next_action": str(next_action),
            "evidence_refs": [str(item) for item in evidence_refs if str(item).strip()],
            "created_at": _now(),
        }
        # Persist blocker + terminal control state in one atomic write.  A host
        # interruption must never leave a durable blocker attached to a still-
        # RUNNING checkpoint or vice versa.
        self.payload.setdefault("blockers", []).append(blocker)
        self.checkpoint(
            status="BLOCKED",
            phase=str(code),
            workspace_fingerprint=workspace_fingerprint,
            evidence_refs=blocker["evidence_refs"],
            metadata={"blocker": blocker},
        )

    def complete(
        self,
        *,
        workspace_fingerprint: str | None,
        evidence_refs: Iterable[str],
    ) -> None:
        decision = self.completion_decision()
        if not decision.eligible:
            raise PrematureCompletionError(
                "task-run cannot complete; "
                f"missing={list(decision.missing_conditions)} "
                f"invalid={list(decision.invalid_conditions)}"
            )
        self.checkpoint(
            status="COMPLETED",
            phase="COMPLETED",
            workspace_fingerprint=workspace_fingerprint,
            evidence_refs=evidence_refs,
            metadata={"completion_decision": decision.status},
        )
