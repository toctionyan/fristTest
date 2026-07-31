#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from common import parse_hook_input, workspace_from

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from contract import load_contract  # type: ignore
from verification import source_fingerprint  # type: ignore


def _deny(reason: str, detail: str | None = None) -> int:
    payload = {"continue": False, "stopReason": reason}
    if detail:
        payload["systemMessage"] = detail[-3000:]
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    try:
        payload = parse_hook_input(sys.stdin.read())
    except Exception:
        payload = {}
    workspace = workspace_from(payload)
    try:
        contract = load_contract(workspace, require_approved=False)
    except ValueError:
        # Read-only diagnosis and explanation sessions do not require a writable contract.
        return 0
    if contract.status not in {"verified", "closed"}:
        return _deny(f"change contract {contract.change_id} is {contract.status}, not verified/closed")
    verification = contract.payload.get("verification")
    if not isinstance(verification, dict):
        return _deny("completion verification identity is missing")
    evidence = workspace / str(verification.get("path") or "")
    if not evidence.is_file() or _sha256(evidence) != verification.get("sha256"):
        return _deny("completion verification evidence is missing or changed")
    fingerprint, file_count = source_fingerprint(workspace, contract.allowed_paths)
    if fingerprint != verification.get("source_fingerprint") or file_count != verification.get("source_file_count"):
        return _deny("governed source changed after deterministic verification")
    attestations = {
        str(row.get("role")): str(row.get("decision"))
        for row in contract.payload.get("review_attestations", [])
        if isinstance(row, dict)
    }
    if contract.profile.startswith("product-") and attestations.get("scope-planner") != "PASS":
        return _deny("scope planner PASS attestation is missing")
    if contract.target_kind.value == "oracle-review" and attestations.get("oracle-reviewer") != "PASS":
        return _deny("Oracle reviewer PASS attestation is missing")
    if attestations.get("adversarial-reviewer") != "PASS":
        return _deny("adversarial reviewer PASS attestation is missing")
    if attestations.get("release-judge") != "PASS":
        return _deny("release Judge PASS attestation is missing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
