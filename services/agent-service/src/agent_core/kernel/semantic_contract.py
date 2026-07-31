from __future__ import annotations

"""Neutral integrity and read projection for a frozen semantic contract.

This module cannot create or mutate turn semantics. Lifecycle remains the sole
owner of normalization, freezing, state transitions and migration.
"""

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any

FROZEN_SEMANTIC_CONTRACT_VERSION = "frozen-turn-semantic-contract@1"


def _text(value: Any, *, limit: int = 2000) -> str:
    return str(value or "").strip()[:limit]


def _digest_payload(contract: dict[str, Any]) -> dict[str, Any]:
    payload = deepcopy(contract)
    payload.pop("semantic_digest", None)
    payload.pop("semantic_contract_id", None)
    return payload


def compute_semantic_digest(contract: dict[str, Any]) -> str:
    digest_source = json.dumps(
        _digest_payload(contract),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(digest_source.encode("utf-8")).hexdigest()


def semantic_contract_integrity(contract: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(contract, dict):
        return {"ok": False, "code": "SEMANTIC_CONTRACT_REQUIRED"}
    stored = _text(contract.get("semantic_digest"), limit=128)
    if not stored:
        return {"ok": False, "code": "SEMANTIC_CONTRACT_DIGEST_REQUIRED"}
    computed = compute_semantic_digest(contract)
    if stored != computed:
        return {
            "ok": False,
            "code": "SEMANTIC_CONTRACT_DIGEST_INVALID",
            "stored_digest": stored,
            "computed_digest": computed,
        }
    expected_id = f"semantic:{int(contract.get('turn') or 0)}:{computed[:20]}"
    if _text(contract.get("semantic_contract_id"), limit=300) != expected_id:
        return {
            "ok": False,
            "code": "SEMANTIC_CONTRACT_ID_INVALID",
            "expected_id": expected_id,
        }
    return {"ok": True, "code": "SEMANTIC_CONTRACT_INTEGRITY_OK", "computed_digest": computed}


def assert_semantic_contract_integrity(contract: dict[str, Any] | None) -> None:
    result = semantic_contract_integrity(contract)
    if not result.get("ok"):
        raise ValueError(str(result.get("code") or "SEMANTIC_CONTRACT_INTEGRITY_INVALID"))


def semantic_goals(state_or_contract: dict[str, Any]) -> list[dict[str, Any]]:
    contract = state_or_contract
    if "frozen_semantic_contract" in state_or_contract:
        contract = state_or_contract.get("frozen_semantic_contract") or {}
    if not isinstance(contract, dict) or contract.get("version") != FROZEN_SEMANTIC_CONTRACT_VERSION:
        return []
    if not semantic_contract_integrity(contract).get("ok"):
        return []
    return deepcopy([row for row in list(contract.get("goals") or []) if isinstance(row, dict)])


__all__ = [
    "FROZEN_SEMANTIC_CONTRACT_VERSION",
    "compute_semantic_digest",
    "semantic_contract_integrity",
    "assert_semantic_contract_integrity",
    "semantic_goals",
]
