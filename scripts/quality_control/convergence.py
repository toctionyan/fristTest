from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CONTROL_PLANE_DIR = ROOT / "skill-system" / "controller"
if str(CONTROL_PLANE_DIR) not in sys.path:
    sys.path.insert(0, str(CONTROL_PLANE_DIR))
from progress import evaluate_progress  # type: ignore

from .common import _sha256_text
from .constants import BLOCKED, FAIL, PASS, UPSTREAM_SKIPPED

def _failure_classification(result: dict[str, Any]) -> str:
    if result["status"] == BLOCKED:
        if result.get("metadata", {}).get("failure_kind") == "dependency_closure":
            return "dependency_closure"
        return "environment"
    if result.get("exit_code") == 124:
        return "timeout"
    category = str(result.get("category") or "")
    if "test" in category or "coverage" in category or "regression" in category:
        return "test_or_contract"
    if category in {"architecture", "skill", "syntax", "release"}:
        return "configuration_or_architecture"
    return "verification"

def _decision(results: list[dict[str, Any]]) -> str:
    # A real code or contract failure must never be hidden behind a concurrent
    # environment block.  Pure environment unavailability remains BLOCKED; an
    # unexplained upstream skip without a blocked root remains FAIL.
    if any(item["status"] == FAIL for item in results):
        return FAIL
    if any(item["status"] == BLOCKED for item in results):
        return BLOCKED
    if any(item["status"] == UPSTREAM_SKIPPED for item in results):
        return FAIL
    return PASS

def _failure_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    # Convergence must measure root failures, not their downstream fan-out.
    # UPSTREAM_SKIPPED remains diagnostic evidence but does not make one root
    # defect look like several independent regressions.
    failed = [item for item in results if item.get("status") == FAIL]
    upstream_skipped = [item for item in results if item.get("status") == UPSTREAM_SKIPPED]
    gate_ids = sorted(str(item.get("id") or "unknown") for item in failed)
    skipped_ids = sorted(str(item.get("id") or "unknown") for item in upstream_skipped)
    signature_rows = [
        {
            "id": str(item.get("id") or "unknown"),
            "status": str(item.get("status") or "unknown"),
            "failure_kind": _failure_classification(item),
        }
        for item in failed
    ]
    signature = _sha256_text(json.dumps(signature_rows, ensure_ascii=False, sort_keys=True))
    return {
        "failure_count": len(failed),
        "failed_gate_ids": gate_ids,
        "upstream_skipped_gate_ids": skipped_ids,
        "failure_signature": signature,
    }

def _advance_convergence_state(
    state: dict[str, Any],
    *,
    current_round: int,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    metrics = _failure_metrics(results)
    progress = evaluate_progress(state, metrics)
    improved = bool(progress["improved"])
    stagnant_rounds = 0 if improved else int(state.get("stagnant_rounds") or 0) + 1
    history = list(state.get("round_history") or [])
    history.append(
        {
            "round": current_round,
            **metrics,
            **progress,
            "stagnant_rounds": stagnant_rounds,
        }
    )
    state.update(
        {
            **metrics,
            "last_failure_count": metrics["failure_count"],
            "stagnant_rounds": stagnant_rounds,
            "round_history": history,
        }
    )
    return {**metrics, **progress, "stagnant_rounds": stagnant_rounds}

