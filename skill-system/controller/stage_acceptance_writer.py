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
from typing import Any, Iterable, Mapping

from contract import validate_contract_payload
from stage_acceptance_reducer import (
    ACCEPTABLE_PREVIEW,
    validate_stage_acceptance_decision,
)
from stage_acceptance_taskrun import STAGE_ACCEPTANCE_PREVIEW_PHASE
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
_AUTHORIZATION_FIELDS = frozenset(
    {
        "authority",
        "authorization_ref",
        "effect",
        "change_id",
        "task_id",
        "decision_id",
        "contract_digest",
        "accepted",
    }
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


def _validate_authorization(
    authorization: Mapping[str, Any],
    *,
    change_id: str,
    task_id: str,
    decision_id: str,
    contract_digest_value: str,
) -> str:
    if not isinstance(authorization, Mapping):
        raise StageAcceptanceWriteError("human_authorization must be an object")
    unknown = sorted(set(authorization) - _AUTHORIZATION_FIELDS)
    missing = sorted(_AUTHORIZATION_FIELDS - set(authorization))
    if unknown:
        raise StageAcceptanceWriteError(
            "human_authorization has unknown fields: " + ",".join(unknown)
        )
    if missing:
        raise StageAcceptanceWriteError(
            "human_authorization is missing: " + ",".join(missing)
        )
    if authorization.get("authority") != "human-review":
        raise StageAcceptanceWriteError("stage acceptance requires human-review authority")
    if authorization.get("effect") != "stage_acceptance_condition_only":
        raise StageAcceptanceWriteError("human authorization effect is too broad")
    if authorization.get("accepted") is not True:
        raise StageAcceptanceWriteError("human authorization must explicitly accept")
    if authorization.get("change_id") != change_id:
        raise StageAcceptanceWriteError("human authorization ChangeContract mismatch")
    if authorization.get("task_id") != task_id:
        raise StageAcceptanceWriteError("human authorization TaskRun mismatch")
    if authorization.get("decision_id") != decision_id:
        raise StageAcceptanceWriteError("human authorization decision mismatch")
    if authorization.get("contract_digest") != contract_digest_value:
        raise StageAcceptanceWriteError("human authorization contract mismatch")
    return _text(authorization.get("authorization_ref"), field="authorization_ref")


def _preview_checkpoint(
    store: TaskRunStore,
    *,
    decision: Mapping[str, Any],
    binding: Mapping[str, str],
) -> Mapping[str, Any]:
    checkpoints = store.payload.get("checkpoints")
    if not isinstance(checkpoints, list) or not checkpoints:
        raise StageAcceptanceWriteError("P4.2 acceptance preview checkpoint is missing")
    checkpoint = checkpoints[-1]
    if not isinstance(checkpoint, Mapping):
        raise StageAcceptanceWriteError("latest TaskRun checkpoint is invalid")
    metadata = checkpoint.get("metadata")
    if not isinstance(metadata, Mapping):
        raise StageAcceptanceWriteError("acceptance preview metadata is missing")
    if checkpoint.get("phase") != STAGE_ACCEPTANCE_PREVIEW_PHASE:
        raise StageAcceptanceWriteError("latest TaskRun checkpoint is not acceptance preview")
    if checkpoint.get("status") not in {"RUNNING", "VALIDATING"}:
        raise StageAcceptanceWriteError("acceptance preview TaskRun status is invalid")
    required_metadata = {
        "stage_acceptance_decision_id": decision["decision_id"],
        "stage_acceptance_input_digest": decision["input_digest"],
        **binding,
        "completion_authority": "TaskRun",
        "graph_can_complete_task": False,
        "completion_authority_changed": False,
        "stage_acceptance_write": False,
    }
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
    authorization_ref: str,
) -> list[str]:
    refs = [str(value) for value in preview["evidence_refs"]]
    refs.extend(
        [
            "stage-acceptance-decision:" + decision_id,
            "change-contract:" + contract_digest_value,
            "human-authorization:" + authorization_ref,
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
    human_authorization: Mapping[str, Any],
) -> dict[str, Any]:
    """Satisfy the existing TaskRun stage condition after strict validation.

    ``change_contract`` and ``human_authorization`` are caller-supplied
    snapshots.  This function does not discover a contract, select a receipt,
    or mint authority.  The only durable mutation is the existing
    ``stage-accepted`` condition in the supplied TaskRun.
    """

    try:
        validated_decision = validate_stage_acceptance_decision(decision)
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
    authorization_ref = _validate_authorization(
        human_authorization,
        change_id=change_id,
        task_id=task_id,
        decision_id=validated_decision["decision_id"],
        contract_digest_value=digest,
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
        authorization_ref=authorization_ref,
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
