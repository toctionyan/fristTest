from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping


AUTONOMY_CONTINUATION_SCHEMA = "engineering-autonomy-continuation@1"
MAX_REPAIR_ROUNDS = 8
MAX_VALIDATION_RETRIES = 3


class AutonomyContinuationError(RuntimeError):
    """Raised when a persisted autonomy continuation envelope drifts or is malformed."""


def _text(value: object) -> str:
    return str(value or "").strip()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _positive_budget(value: object, *, name: str, maximum: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise AutonomyContinuationError(f"{name} must be an integer") from exc
    if result < 1 or result > maximum:
        raise AutonomyContinuationError(f"{name} must be within 1..{maximum}")
    return result


def build_autonomy_continuation(
    *,
    grant_id: object,
    grant_sha256: object,
    authorization_id: object,
    authorization_sha256: object,
    source_run_id: object,
    source_run_attempt: object,
    source_head_sha: object,
    failure_signature: object,
    max_repair_rounds: object,
    max_validation_retries: object,
) -> dict[str, Any]:
    payload = {
        "schema": AUTONOMY_CONTINUATION_SCHEMA,
        "grant_id": _text(grant_id),
        "grant_sha256": _text(grant_sha256),
        "authorization_id": _text(authorization_id),
        "authorization_sha256": _text(authorization_sha256),
        "source_run_id": _text(source_run_id),
        "source_run_attempt": _text(source_run_attempt),
        "source_head_sha": _text(source_head_sha).lower(),
        "failure_signature": _text(failure_signature),
        "max_repair_rounds": _positive_budget(
            max_repair_rounds,
            name="max_repair_rounds",
            maximum=MAX_REPAIR_ROUNDS,
        ),
        "max_validation_retries": _positive_budget(
            max_validation_retries,
            name="max_validation_retries",
            maximum=MAX_VALIDATION_RETRIES,
        ),
        "write_authority_effect": False,
        "test_authority_effect": False,
        "merge_allowed": False,
        "deploy_allowed": False,
        "production_closed": False,
    }
    if not payload["grant_id"] or not payload["authorization_id"]:
        raise AutonomyContinuationError("continuation requires grant and authorization ids")
    for field in ("grant_sha256", "authorization_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(payload[field])):
            raise AutonomyContinuationError(f"continuation {field} is malformed")
    if not payload["source_run_id"].isdigit() or int(payload["source_run_id"]) < 1:
        raise AutonomyContinuationError("continuation source_run_id is invalid")
    if not payload["source_run_attempt"].isdigit() or int(payload["source_run_attempt"]) < 1:
        raise AutonomyContinuationError("continuation source_run_attempt is invalid")
    if not re.fullmatch(r"[0-9a-f]{40}", str(payload["source_head_sha"])):
        raise AutonomyContinuationError("continuation source_head_sha is malformed")
    if not re.fullmatch(r"[0-9a-f]{64}", str(payload["failure_signature"])):
        raise AutonomyContinuationError("continuation failure_signature is malformed")
    payload["continuation_sha256"] = _digest(payload)
    return payload


def validate_autonomy_continuation(
    value: Mapping[str, Any],
    *,
    source_run_id: object,
    source_run_attempt: object,
    source_head_sha: object,
    failure_signature: object,
) -> dict[str, Any]:
    payload = dict(value)
    if payload.get("schema") != AUTONOMY_CONTINUATION_SCHEMA:
        raise AutonomyContinuationError("unsupported autonomy continuation schema")
    expected_digest = _text(payload.pop("continuation_sha256"))
    if not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
        raise AutonomyContinuationError("autonomy continuation digest is missing or malformed")
    if _digest(payload) != expected_digest:
        raise AutonomyContinuationError("autonomy continuation digest mismatch")
    for field in (
        "write_authority_effect",
        "test_authority_effect",
        "merge_allowed",
        "deploy_allowed",
        "production_closed",
    ):
        if payload.get(field) is not False:
            raise AutonomyContinuationError(
                f"autonomy continuation cannot acquire authority through {field}"
            )
    rebuilt = build_autonomy_continuation(
        grant_id=payload.get("grant_id"),
        grant_sha256=payload.get("grant_sha256"),
        authorization_id=payload.get("authorization_id"),
        authorization_sha256=payload.get("authorization_sha256"),
        source_run_id=payload.get("source_run_id"),
        source_run_attempt=payload.get("source_run_attempt"),
        source_head_sha=payload.get("source_head_sha"),
        failure_signature=payload.get("failure_signature"),
        max_repair_rounds=payload.get("max_repair_rounds"),
        max_validation_retries=payload.get("max_validation_retries"),
    )
    if rebuilt["continuation_sha256"] != expected_digest:
        raise AutonomyContinuationError("autonomy continuation canonical projection drifted")
    expected = {
        "source_run_id": _text(source_run_id),
        "source_run_attempt": _text(source_run_attempt),
        "source_head_sha": _text(source_head_sha).lower(),
        "failure_signature": _text(failure_signature),
    }
    for field, expected_value in expected.items():
        if _text(rebuilt.get(field)) != expected_value:
            raise AutonomyContinuationError(
                f"autonomy continuation source binding mismatch: {field}"
            )
    return rebuilt
