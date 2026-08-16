from __future__ import annotations

"""Canonical structural lifecycle contract for governed repair.

This module defines state/gate names and protected authority facts only. It has no
source-edit, baseline, merge, deployment, or completion side effects. Runtime
controllers project these constants into their evidence; mechanical verification
rejects drift between the contract and those projections.
"""

import hashlib
import json
from typing import Any

CONTRACT_SCHEMA = "governed-repair-lifecycle-contract@1"
CONTRACT_ID = "customer-agent/governed-repair-lifecycle@1"

STATE_MACHINE = (
    "DETECTED",
    "EVIDENCE_FROZEN",
    "RCA_READ_ONLY",
    "INVARIANT_BOUND",
    "REPAIR_PLAN_FROZEN",
    "WRITE_GRANTED",
    "PATCHING",
    "LOCAL_VERIFICATION",
    "INDEPENDENT_REVIEW",
    "ANTI_DRIFT_PROOF",
    "PR_CERTIFICATION",
    "GOVERNANCE_CLOSED",
    "BASELINE_ACCEPTED",
    "EXACT_HEAD_CERTIFICATION",
    "READY_FOR_REVIEW",
)

PREWRITE_STATES = STATE_MACHINE[:6]

GATES = (
    "G0_SCOPE_AUTHORITY",
    "G1_CONTRACT_PROJECTION",
    "G2_SEMANTIC_INVARIANT",
    "G3_MUTATION",
    "G4_FINAL_AUTHORITY",
    "G5_INTEGRATION_CERTIFICATION",
    "G6_GOVERNANCE_EXACT_HEAD",
)

PROTECTED_AUTHORITY = {
    "scope_expansion_allowed": False,
    "tests_oracles_write_allowed": False,
    "workflow_write_allowed": False,
    "governance_write_allowed": False,
    "skill_control_plane_write_allowed": False,
    "baseline_write_allowed": False,
    "dependency_manifest_write_allowed": False,
    "secret_write_allowed": False,
    "merge_allowed": False,
    "deploy_allowed": False,
    "production_close_allowed": False,
}


def contract_payload() -> dict[str, Any]:
    return {
        "schema": CONTRACT_SCHEMA,
        "contract_id": CONTRACT_ID,
        "states": list(STATE_MACHINE),
        "gates": list(GATES),
        "protected_authority": dict(PROTECTED_AUTHORITY),
    }


def contract_fingerprint() -> str:
    canonical = json.dumps(
        contract_payload(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
