from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from langgraph_workflow_runtime import StepDispatchResult
from workflow_graph_contract import WorkflowStepSpec


HUMAN_GATE_CONTRACT_SCHEMA = "durable-human-gate@1"
HUMAN_GATE_DECISION_SCHEMA = "durable-human-decision@1"


class DurableHumanGateError(RuntimeError):
    """Raised when a gate or decision is missing, stale, or not exact."""


def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _digest(payload: Mapping[str, Any], *, field: str) -> str:
    unsigned = dict(payload)
    unsigned.pop(field, None)
    return hashlib.sha256(_canonical(unsigned)).hexdigest()


def _text(value: object) -> str:
    return str(value or "").strip()


def _safe_id(value: object, *, field: str) -> str:
    text = _text(value)
    if not text or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:-" for char in text):
        raise DurableHumanGateError(f"{field} must be a stable identifier")
    return text


def _relative(value: object, *, field: str) -> str:
    text = _text(value).replace("\\", "/")
    path = PurePosixPath(text)
    if (
        not text
        or text.startswith("/")
        or path.as_posix() != text
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise DurableHumanGateError(f"{field} must be a bounded relative path")
    return path.as_posix()


def _bounded(workspace: Path, relative: str, *, field: str) -> Path:
    path = workspace / relative
    current = workspace
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise DurableHumanGateError(f"{field} cannot traverse a symlink")
    try:
        path.resolve().relative_to(workspace)
    except ValueError as exc:
        raise DurableHumanGateError(f"{field} escapes the project workspace") from exc
    return path


def _closed(payload: Mapping[str, Any], fields: set[str], *, field: str) -> None:
    missing = sorted(fields - set(payload))
    unexpected = sorted(set(payload) - fields)
    if missing or unexpected:
        raise DurableHumanGateError(
            f"{field} fields are not closed: missing={missing} unexpected={unexpected}"
        )


def _read_object(path: Path, *, field: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise DurableHumanGateError(f"{field} is missing or unsafe")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DurableHumanGateError(f"{field} is unreadable") from exc
    if not isinstance(raw, dict):
        raise DurableHumanGateError(f"{field} must be an object")
    return raw


def _write_new(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(
            json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )
        handle.flush()
        os.fsync(handle.fileno())


def _write_or_verify(path: Path, payload: Mapping[str, Any], *, field: str) -> None:
    if path.exists() or path.is_symlink():
        if _read_object(path, field=field) != dict(payload):
            raise DurableHumanGateError(f"{field} identity drifted")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(
                json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True)
                + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if _read_object(path, field=field) != dict(payload):
                raise DurableHumanGateError(f"{field} identity drifted")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_gate_contract(raw: Mapping[str, Any]) -> dict[str, Any]:
    gate = dict(raw)
    _closed(
        gate,
        {
            "schema",
            "gate_id",
            "task_id",
            "workflow_id",
            "step_id",
            "question",
            "waiting_outcome",
            "options",
            "routes",
            "authority_effect",
            "gate_sha256",
        },
        field="Human Gate contract",
    )
    if gate.get("schema") != HUMAN_GATE_CONTRACT_SCHEMA:
        raise DurableHumanGateError("unsupported Human Gate contract schema")
    for field in ("gate_id", "task_id", "workflow_id", "step_id"):
        gate[field] = _safe_id(gate.get(field), field=field)
    if not _text(gate.get("question")):
        raise DurableHumanGateError("Human Gate question is required")
    options = gate.get("options")
    if (
        not isinstance(options, list)
        or not options
        or any(not isinstance(value, str) or not value.strip() for value in options)
        or len(options) != len(set(options))
    ):
        raise DurableHumanGateError("Human Gate options must be unique non-empty outcomes")
    routes = gate.get("routes")
    if not isinstance(routes, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in routes.items()
    ):
        raise DurableHumanGateError("Human Gate routes must be an outcome map")
    waiting = _text(gate.get("waiting_outcome"))
    if routes.get(waiting) != "HUMAN_GATE":
        raise DurableHumanGateError("waiting_outcome must route to HUMAN_GATE")
    if any(option not in routes or routes[option] == "HUMAN_GATE" for option in options):
        raise DurableHumanGateError("Human Gate options must be declared non-wait routes")
    if gate.get("authority_effect") is not False:
        raise DurableHumanGateError("Human Gate contract cannot carry authority")
    if gate.get("gate_sha256") != _digest(gate, field="gate_sha256"):
        raise DurableHumanGateError("Human Gate contract fingerprint mismatch")
    return gate


def validate_human_decision(
    raw: Mapping[str, Any], *, gate: Mapping[str, Any]
) -> dict[str, Any]:
    decision = dict(raw)
    _closed(
        decision,
        {
            "schema",
            "gate_id",
            "gate_sha256",
            "task_id",
            "workflow_id",
            "step_id",
            "selected_outcome",
            "actor",
            "decided_at",
            "authority_effect",
            "decision_sha256",
        },
        field="Human Gate decision",
    )
    if decision.get("schema") != HUMAN_GATE_DECISION_SCHEMA:
        raise DurableHumanGateError("unsupported Human Gate decision schema")
    for field in ("gate_id", "task_id", "workflow_id", "step_id", "actor"):
        decision[field] = _safe_id(decision.get(field), field=field)
    for field in ("gate_id", "gate_sha256", "task_id", "workflow_id", "step_id"):
        if decision.get(field) != gate.get(field):
            raise DurableHumanGateError(f"Human Gate decision {field} mismatch")
    selected = _text(decision.get("selected_outcome"))
    if selected not in gate["options"]:
        raise DurableHumanGateError("Human Gate decision selected_outcome is not allowed")
    decided_at = _text(decision.get("decided_at"))
    try:
        parsed = dt.datetime.fromisoformat(decided_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DurableHumanGateError("Human Gate decision decided_at is invalid") from exc
    if parsed.tzinfo is None:
        raise DurableHumanGateError("Human Gate decision decided_at must include timezone")
    if decision.get("authority_effect") is not False:
        raise DurableHumanGateError("Human Gate decision cannot grant authority")
    if decision.get("decision_sha256") != _digest(decision, field="decision_sha256"):
        raise DurableHumanGateError("Human Gate decision fingerprint mismatch")
    return decision


def seal_human_decision(
    gate: Mapping[str, Any],
    *,
    selected_outcome: str,
    actor: str,
    decided_at: str | None = None,
) -> dict[str, Any]:
    validated = validate_gate_contract(gate)
    payload = {
        "schema": HUMAN_GATE_DECISION_SCHEMA,
        "gate_id": validated["gate_id"],
        "gate_sha256": validated["gate_sha256"],
        "task_id": validated["task_id"],
        "workflow_id": validated["workflow_id"],
        "step_id": validated["step_id"],
        "selected_outcome": _text(selected_outcome),
        "actor": _safe_id(actor, field="actor"),
        "decided_at": decided_at
        or dt.datetime.now(dt.timezone.utc).isoformat(),
        "authority_effect": False,
    }
    payload["decision_sha256"] = _digest(payload, field="decision_sha256")
    return validate_human_decision(payload, gate=validated)


def write_human_decision(
    *,
    workspace: Path,
    gate_path: Path,
    decision_root: str,
    selected_outcome: str,
    actor: str,
    decided_at: str | None = None,
) -> dict[str, Any]:
    root = Path(workspace).resolve()
    try:
        relative_gate = Path(gate_path).resolve().relative_to(root).as_posix()
    except ValueError as exc:
        raise DurableHumanGateError("gate_path must stay inside the project workspace") from exc
    gate = validate_gate_contract(
        _read_object(_bounded(root, relative_gate, field="gate_path"), field="gate contract")
    )
    decision = seal_human_decision(
        gate,
        selected_outcome=selected_outcome,
        actor=actor,
        decided_at=decided_at,
    )
    relative_root = _relative(decision_root, field="decision_root")
    if not relative_root.startswith(".harness/"):
        raise DurableHumanGateError("decision_root must stay beneath .harness")
    relative = (
        f"{relative_root}/{decision['gate_id']}/{decision['decision_sha256']}.json"
    )
    path = _bounded(root, relative, field="decision path")
    _write_new(path, decision)
    return {
        "schema": "durable-human-decision-result@1",
        "status": "PASS",
        "decision": decision,
        "decision_ref": f"file:{relative}",
        "authority_effect": False,
    }


class DurableHumanGateAdapter:
    """Persist a verified Workflow gate and resume only its exact sealed decision."""

    def __init__(
        self,
        *,
        workspace: Path,
        gate_root: str = ".harness/runtime/human-gates",
        decision_root: str = ".harness/runtime/human-decisions",
    ) -> None:
        self.workspace = Path(workspace).resolve()
        if not self.workspace.is_dir():
            raise DurableHumanGateError("workspace must be an existing directory")
        self.gate_root = _relative(gate_root, field="gate_root")
        self.decision_root = _relative(decision_root, field="decision_root")
        if not self.gate_root.startswith(".harness/") or not self.decision_root.startswith(
            ".harness/"
        ):
            raise DurableHumanGateError("Human Gate roots must stay beneath .harness")

    def _gate(
        self, *, step: WorkflowStepSpec, state: Mapping[str, Any]
    ) -> tuple[dict[str, Any], str]:
        task_id = _safe_id(state.get("task_id"), field="task_id")
        workflow_id = _safe_id(state.get("workflow_id"), field="workflow_id")
        waiting = [outcome for outcome, target in step.routes.items() if target == "HUMAN_GATE"]
        if len(waiting) != 1:
            raise DurableHumanGateError(
                "concrete Human Gate requires exactly one outcome routed to HUMAN_GATE"
            )
        options = sorted(
            outcome for outcome, target in step.routes.items() if target != "HUMAN_GATE"
        )
        if not options:
            raise DurableHumanGateError("concrete Human Gate requires decision outcomes")
        identity = {
            "task_id": task_id,
            "workflow_id": workflow_id,
            "step_id": step.step_id,
            "routes": dict(sorted(step.routes.items())),
        }
        gate_id = "gate-" + hashlib.sha256(_canonical(identity)).hexdigest()[:24]
        gate = {
            "schema": HUMAN_GATE_CONTRACT_SCHEMA,
            "gate_id": gate_id,
            "task_id": task_id,
            "workflow_id": workflow_id,
            "step_id": step.step_id,
            "question": f"Select the verified outcome for {workflow_id}/{step.step_id}.",
            "waiting_outcome": waiting[0],
            "options": options,
            "routes": dict(step.routes),
            "authority_effect": False,
        }
        gate["gate_sha256"] = _digest(gate, field="gate_sha256")
        validated = validate_gate_contract(gate)
        relative = f"{self.gate_root}/{task_id}/{workflow_id}/{step.step_id}.json"
        path = _bounded(self.workspace, relative, field="gate path")
        _write_or_verify(path, validated, field="Human Gate contract")
        return validated, relative

    def invoke(
        self, *, step: WorkflowStepSpec, state: Mapping[str, Any]
    ) -> StepDispatchResult:
        gate, gate_relative = self._gate(step=step, state=state)
        raw_decision = state.get("human_decision")
        if not isinstance(raw_decision, Mapping) or raw_decision.get("gate_id") != gate["gate_id"]:
            return StepDispatchResult(
                outcome=gate["waiting_outcome"],
                evidence_refs=(f"file:{gate_relative}",),
                human_gate={
                    "gate_id": gate["gate_id"],
                    "gate_ref": f"file:{gate_relative}",
                    "gate_sha256": gate["gate_sha256"],
                    "question": gate["question"],
                    "options": list(gate["options"]),
                    "decision_command": "authoring human-decision",
                    "authority_effect": False,
                },
            )

        decision = validate_human_decision(raw_decision, gate=gate)
        relative = (
            f"{self.decision_root}/{gate['gate_id']}/{decision['decision_sha256']}.json"
        )
        persisted = validate_human_decision(
            _read_object(
                _bounded(self.workspace, relative, field="decision path"),
                field="Human Gate decision",
            ),
            gate=gate,
        )
        if persisted != decision:
            raise DurableHumanGateError("inline Human Gate decision differs from durable artifact")
        return StepDispatchResult(
            outcome=decision["selected_outcome"],
            evidence_refs=(f"file:{gate_relative}", f"file:{relative}"),
            payload={
                "gate_id": gate["gate_id"],
                "selected_outcome": decision["selected_outcome"],
                "actor": decision["actor"],
                "authority_effect": False,
                "completion_authority_changed": False,
                "merge_authority_changed": False,
            },
        )


__all__ = [
    "DurableHumanGateAdapter",
    "DurableHumanGateError",
    "HUMAN_GATE_CONTRACT_SCHEMA",
    "HUMAN_GATE_DECISION_SCHEMA",
    "seal_human_decision",
    "validate_gate_contract",
    "validate_human_decision",
    "write_human_decision",
]
