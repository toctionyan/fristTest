"""Record stage acceptance in the existing TaskRun completion contract.

P4.3 is intentionally a narrow write boundary.  It consumes an explicit
reducer decision and the P4.2 preview checkpoint, then satisfies the existing
``stage-accepted`` TaskRun condition.  It never writes governance files,
changes a ChangeContract, calls ``complete()``, or creates another authority.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from contract import validate_contract_payload
from durable_human_gate import validate_gate_contract, validate_human_decision
from stage_acceptance_reducer import (
    ACCEPTABLE_PREVIEW,
    TRUSTED_STAGE_ACCEPTANCE_DECISION_SCHEMA,
    validate_trusted_stage_acceptance_decision,
)
from stage_acceptance_taskrun import (
    STAGE_ACCEPTANCE_PREVIEW_PHASE,
)
from task_run import TERMINAL_STATUSES, TaskRunStore, evaluate_completion


STAGE_ACCEPTANCE_WRITE_SCHEMA = "stage-acceptance-write@1"
STAGE_ACCEPTED_CONDITION = "stage-accepted"
_BINDING_FIELDS = (
    "stage_id",
    "accepted_state_id",
    "product_source_ref",
    "protected_snapshot_digest",
    "control_plane_ref",
    "execution_repo_ref",
)


class StageAcceptanceWriteError(ValueError):
    """Raised when acceptance cannot be safely recorded."""


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise StageAcceptanceWriteError("value is not canonical JSON") from exc


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def contract_digest(contract: Mapping[str, Any]) -> str:
    """Return the stable identity of an explicitly supplied ChangeContract."""

    if not isinstance(contract, Mapping):
        raise StageAcceptanceWriteError("ChangeContract must be an object")
    return _digest(dict(contract))


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StageAcceptanceWriteError(f"{field} must be a non-empty string")
    return value.strip()


def _binding(expected: Mapping[str, Any], store: TaskRunStore) -> dict[str, str]:
    if not isinstance(expected, Mapping):
        raise StageAcceptanceWriteError("expected_binding must be an object")
    unknown = sorted(set(expected) - set(_BINDING_FIELDS))
    if unknown:
        raise StageAcceptanceWriteError(
            "expected_binding has unknown fields: " + ",".join(unknown)
        )
    missing = [field for field in _BINDING_FIELDS if field not in expected]
    if missing:
        raise StageAcceptanceWriteError(
            "expected_binding is missing: " + ",".join(missing)
        )
    normalized = {
        field: _text(expected[field], field=field) for field in _BINDING_FIELDS
    }
    task_binding = store.payload.get("binding")
    if not isinstance(task_binding, Mapping):
        raise StageAcceptanceWriteError("TaskRun immutable binding is missing")
    mismatches = [
        field for field in _BINDING_FIELDS if task_binding.get(field) != normalized[field]
    ]
    if mismatches:
        raise StageAcceptanceWriteError(
            "TaskRun binding mismatch: " + ",".join(mismatches)
        )
    return normalized


def _validate_contract(
    contract: Mapping[str, Any],
    *,
    expected_digest: str,
) -> tuple[dict[str, Any], str]:
    if not isinstance(contract, Mapping):
        raise StageAcceptanceWriteError("ChangeContract must be an object")
    payload = dict(contract)
    errors = validate_contract_payload(payload)
    if errors:
        raise StageAcceptanceWriteError(
            "ChangeContract is invalid: " + "; ".join(errors)
        )
    actual_digest = contract_digest(payload)
    if _text(expected_digest, field="contract_digest") != actual_digest:
        raise StageAcceptanceWriteError("ChangeContract digest mismatch")
    if payload.get("status") != "implementing":
        raise StageAcceptanceWriteError(
            "stage acceptance requires an implementing ChangeContract"
        )
    if payload.get("result") != "PENDING":
        raise StageAcceptanceWriteError(
            "stage acceptance requires a pending ChangeContract result"
        )
    if payload.get("production_closed") is True:
        raise StageAcceptanceWriteError("stage acceptance cannot reopen production")
    return payload, actual_digest


def _read_gate_artifact(workspace: Path, raw_path: str | Path, *, field: str) -> tuple[dict[str, Any], str]:
    path = Path(raw_path)
    if not path.is_absolute():
        path = workspace / path
    if path.is_symlink():
        raise StageAcceptanceWriteError(f"{field} is missing or unsafe")
    path = path.resolve()
    try:
        relative = path.relative_to(workspace.resolve()).as_posix()
    except ValueError as exc:
        raise StageAcceptanceWriteError(f"{field} must stay inside workspace") from exc
    if path.is_symlink() or not path.is_file():
        raise StageAcceptanceWriteError(f"{field} is missing or unsafe")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StageAcceptanceWriteError(f"{field} is unreadable") from exc
    if not isinstance(payload, dict):
        raise StageAcceptanceWriteError(f"{field} must be an object")
    return payload, relative


def _validate_human_acceptance(
    workspace: Path,
    *,
    gate_path: str | Path,
    decision_path: str | Path,
    task_id: str,
    expected_outcome: str,
) -> tuple[str, str]:
    raw_gate, gate_relative = _read_gate_artifact(workspace, gate_path, field="human_gate_path")
    raw_decision, decision_relative = _read_gate_artifact(
        workspace, decision_path, field="human_decision_path"
    )
    try:
        gate = validate_gate_contract(raw_gate)
        decision = validate_human_decision(raw_decision, gate=gate)
    except (TypeError, ValueError, RuntimeError) as exc:
        raise StageAcceptanceWriteError("human gate or decision is invalid") from exc
    if gate["task_id"] != task_id or decision["task_id"] != task_id:
        raise StageAcceptanceWriteError("human gate TaskRun mismatch")
    if gate["step_id"] != "stage2b1-acceptance":
        raise StageAcceptanceWriteError("human gate step is not stage2b1 acceptance")
    if gate.get("authority_effect") is not False or decision.get("authority_effect") is not False:
        raise StageAcceptanceWriteError("human gate cannot grant authority")
    expected = _text(expected_outcome, field="expected_outcome")
    if decision.get("selected_outcome") != expected:
        raise StageAcceptanceWriteError("human decision did not select stage acceptance")
    return f"file:{gate_relative}", f"file:{decision_relative}"


def _preview_checkpoint(
    store: TaskRunStore,
    *,
    decision: Mapping[str, Any],
    binding: Mapping[str, str],
) -> Mapping[str, Any]:
    checkpoints = store.payload.get("checkpoints")
    if not isinstance(checkpoints, list) or not checkpoints:
        raise StageAcceptanceWriteError("P4.2 acceptance preview checkpoint is missing")
    required_metadata = {
        "stage_acceptance_decision_id": decision["decision_id"],
        "stage_acceptance_input_digest": decision["input_digest"],
        **binding,
        "completion_authority": "TaskRun",
        "graph_can_complete_task": False,
        "completion_authority_changed": False,
        "stage_acceptance_write": False,
    }
    matching: list[Mapping[str, Any]] = []
    for checkpoint in checkpoints:
        if not isinstance(checkpoint, Mapping):
            continue
        metadata = checkpoint.get("metadata")
        if not isinstance(metadata, Mapping):
            continue
        if metadata.get("stage_acceptance_decision_id") != decision["decision_id"]:
            continue
        matching.append(checkpoint)
    if not matching:
        raise StageAcceptanceWriteError(
            "acceptance preview checkpoint matching decision is missing"
        )
    if len(matching) != 1:
        raise StageAcceptanceWriteError(
            "acceptance preview checkpoint matching decision is ambiguous"
        )
    checkpoint = matching[0]
    metadata = checkpoint["metadata"]
    if checkpoint.get("phase") != STAGE_ACCEPTANCE_PREVIEW_PHASE:
        raise StageAcceptanceWriteError("acceptance preview checkpoint phase is invalid")
    if checkpoint.get("status") not in {"RUNNING", "VALIDATING"}:
        raise StageAcceptanceWriteError("acceptance preview TaskRun status is invalid")
    mismatches = [
        field for field, value in required_metadata.items()
        if metadata.get(field) != value
    ]
    if mismatches:
        raise StageAcceptanceWriteError(
            "acceptance preview metadata mismatch: " + ",".join(mismatches)
        )
    refs = checkpoint.get("evidence_refs")
    if not isinstance(refs, list) or not refs or any(not isinstance(value, str) or not value.strip() for value in refs):
        raise StageAcceptanceWriteError("acceptance preview evidence is missing")
    if len(refs) != len(set(refs)):
        raise StageAcceptanceWriteError("acceptance preview evidence must be unique")
    return checkpoint


def _acceptance_refs(
    preview: Mapping[str, Any],
    *,
    decision_id: str,
    contract_digest_value: str,
    human_gate_ref: str,
    human_decision_ref: str,
) -> list[str]:
    refs = [str(value) for value in preview["evidence_refs"]]
    refs.extend(
        [
            "stage-acceptance-decision:" + decision_id,
            "change-contract:" + contract_digest_value,
            human_gate_ref,
            human_decision_ref,
        ]
    )
    if len(refs) != len(set(refs)):
        raise StageAcceptanceWriteError("acceptance evidence references collide")
    return refs


def _would_complete(store: TaskRunStore, *, condition: str) -> bool:
    payload = copy.deepcopy(store.payload)
    conditions = payload.get("conditions")
    if not isinstance(conditions, dict) or condition not in conditions:
        raise StageAcceptanceWriteError(
            f"TaskRun does not declare required condition: {condition}"
        )
    conditions[condition] = {
        "satisfied": True,
        "evidence_refs": ["preview"],
        "updated_at": "placeholder",
    }
    return evaluate_completion(payload).eligible


def write_stage_acceptance(
    store: TaskRunStore,
    decision: Mapping[str, Any],
    *,
    expected_binding: Mapping[str, Any],
    change_contract: Mapping[str, Any],
    change_contract_digest: str,
    workspace: Path,
    human_gate_path: str | Path,
    human_decision_path: str | Path,
    expected_human_outcome: str = "ACCEPT_STAGE2B1",
) -> dict[str, Any]:
    """Satisfy the existing TaskRun stage condition after strict validation.

    ``change_contract`` is an explicit snapshot and the human gate/decision
    paths are explicit inputs.  This function does not discover a contract,
    select a receipt, or mint authority.  The only durable mutation is the
    existing ``stage-accepted`` condition in the supplied TaskRun.
    """

    try:
        validated_decision = validate_trusted_stage_acceptance_decision(decision)
    except (TypeError, ValueError) as exc:
        raise StageAcceptanceWriteError("stage acceptance decision is invalid") from exc
    if validated_decision["status"] != ACCEPTABLE_PREVIEW:
        raise StageAcceptanceWriteError("only an acceptable reducer preview may be accepted")
    if store.payload.get("status") in TERMINAL_STATUSES:
        raise StageAcceptanceWriteError("terminal TaskRun cannot receive stage acceptance")

    binding = _binding(expected_binding, store)
    contract, digest = _validate_contract(
        change_contract,
        expected_digest=change_contract_digest,
    )
    change_id = _text(contract.get("change_id"), field="change_id")
    task_id = _text(store.payload.get("task_id"), field="task_id")
    human_gate_ref, human_decision_ref = _validate_human_acceptance(
        Path(workspace).resolve(),
        gate_path=human_gate_path,
        decision_path=human_decision_path,
        task_id=task_id,
        expected_outcome=expected_human_outcome,
    )
    preview = _preview_checkpoint(
        store,
        decision=validated_decision,
        binding=binding,
    )
    refs = _acceptance_refs(
        preview,
        decision_id=validated_decision["decision_id"],
        contract_digest_value=digest,
        human_gate_ref=human_gate_ref,
        human_decision_ref=human_decision_ref,
    )
    conditions = store.payload.get("conditions")
    if not isinstance(conditions, Mapping):
        raise StageAcceptanceWriteError("TaskRun conditions are invalid")
    current = conditions.get(STAGE_ACCEPTED_CONDITION)
    if not isinstance(current, Mapping):
        raise StageAcceptanceWriteError(
            f"TaskRun does not declare required condition: {STAGE_ACCEPTED_CONDITION}"
        )
    if current.get("satisfied") is True:
        if current.get("evidence_refs") != refs:
            raise StageAcceptanceWriteError(
                "stage acceptance was previously recorded with different evidence"
            )
        return {
            "schema": STAGE_ACCEPTANCE_WRITE_SCHEMA,
            "status": "RECORDED",
            "task_id": task_id,
            "change_id": change_id,
            "condition": STAGE_ACCEPTED_CONDITION,
            "decision_id": validated_decision["decision_id"],
            "contract_digest": digest,
            "evidence_refs": refs,
            "human_gate_ref": human_gate_ref,
            "human_decision_ref": human_decision_ref,
            "completion_authority": "TaskRun",
            "task_completed": store.payload.get("status") == "COMPLETED",
            "active_change_written": False,
            "governance_state_changed": False,
        }
    if current.get("satisfied") is not False:
        raise StageAcceptanceWriteError("stage acceptance condition has invalid state")
    if _would_complete(store, condition=STAGE_ACCEPTED_CONDITION):
        raise StageAcceptanceWriteError(
            "stage acceptance cannot be the final TaskRun completion condition"
        )

    store.mark_condition(STAGE_ACCEPTED_CONDITION, evidence_refs=refs)
    if store.payload.get("status") == "COMPLETED":
        raise StageAcceptanceWriteError("stage acceptance unexpectedly completed TaskRun")
    return {
        "schema": STAGE_ACCEPTANCE_WRITE_SCHEMA,
        "status": "RECORDED",
        "task_id": task_id,
        "change_id": change_id,
        "condition": STAGE_ACCEPTED_CONDITION,
        "decision_id": validated_decision["decision_id"],
        "contract_digest": digest,
        "evidence_refs": refs,
        "human_gate_ref": human_gate_ref,
        "human_decision_ref": human_decision_ref,
        "completion_authority": "TaskRun",
        "task_completed": False,
        "active_change_written": False,
        "governance_state_changed": False,
    }


__all__ = [
    "STAGE_ACCEPTANCE_WRITE_SCHEMA",
    "STAGE_ACCEPTED_CONDITION",
    "TRUSTED_STAGE_ACCEPTANCE_DECISION_SCHEMA",
    "StageAcceptanceWriteError",
    "contract_digest",
    "validate_trusted_stage_acceptance_decision",
    "write_stage_acceptance",
]
