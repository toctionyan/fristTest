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
from typing import Any, Mapping

from contract import validate_contract_payload
from stage_acceptance_reducer import (
    ACCEPTABLE_PREVIEW,
    TrustedStageAcceptanceDecision,
    TrustedStageAcceptanceVerificationInputs,
    TRUSTED_STAGE_ACCEPTANCE_DECISION_SCHEMA,
    require_reducer_stage_acceptance_decision,
    reverify_trusted_stage_acceptance_decision,
)
from stage_acceptance_taskrun import (
    STAGE_ACCEPTANCE_PREVIEW_PHASE,
    decision_evidence_refs,
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
    if refs != decision_evidence_refs(decision):
        raise StageAcceptanceWriteError("acceptance preview evidence does not match decision")
    return checkpoint


def _acceptance_refs(
    preview: Mapping[str, Any],
    *,
    contract_digest_value: str,
    protected_approval_ref: str,
) -> list[str]:
    refs = [str(value) for value in preview["evidence_refs"]]
    for reference in (
        "change-contract:" + contract_digest_value,
        protected_approval_ref,
    ):
        if reference not in refs:
            refs.append(reference)
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
    decision: TrustedStageAcceptanceDecision,
    *,
    expected_binding: Mapping[str, Any],
    change_contract: Mapping[str, Any],
    change_contract_digest: str,
    verification: TrustedStageAcceptanceVerificationInputs,
) -> dict[str, Any]:
    """Satisfy the existing TaskRun stage condition after strict validation.

    ``change_contract`` and ``protected_approval`` are explicit snapshots.
    This function does not discover a contract, select a receipt, or mint
    authority. The only durable mutation is the existing ``stage-accepted``
    condition in the supplied TaskRun.
    """

    try:
        validated_decision = require_reducer_stage_acceptance_decision(decision)
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
    try:
        validated_decision = reverify_trusted_stage_acceptance_decision(
            validated_decision,
            verification=verification,
            common_binding=binding,
        )
    except (TypeError, ValueError, OSError) as exc:
        raise StageAcceptanceWriteError(
            "trusted stage acceptance decision could not be independently reverified"
        ) from exc
    decision_binding = validated_decision.get("binding")
    if not isinstance(decision_binding, Mapping) or decision_binding.get("task_id") != task_id:
        raise StageAcceptanceWriteError("stage acceptance decision TaskRun binding mismatch")
    protected_approval_ref = next(
        value for value in validated_decision["proof_refs"]
        if value.startswith("protected-approval:")
    )
    preview = _preview_checkpoint(
        store,
        decision=validated_decision,
        binding=binding,
    )
    refs = _acceptance_refs(
        preview,
        contract_digest_value=digest,
        protected_approval_ref=protected_approval_ref,
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
            "protected_approval_ref": protected_approval_ref,
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
        "protected_approval_ref": protected_approval_ref,
        "completion_authority": "TaskRun",
        "task_completed": False,
        "active_change_written": False,
        "governance_state_changed": False,
    }


__all__ = [
    "STAGE_ACCEPTANCE_WRITE_SCHEMA",
    "STAGE_ACCEPTED_CONDITION",
    "StageAcceptanceWriteError",
    "contract_digest",
    "write_stage_acceptance",
]
