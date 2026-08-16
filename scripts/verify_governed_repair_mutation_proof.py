#!/usr/bin/env python3
from __future__ import annotations

"""Mutation proof for governed-repair architecture drift detection.

The proof first requires the live architecture to PASS. It then copies only the
mechanically verified architecture surface to an isolated temporary root, mutates
a secondary lifecycle projection (the write-grant lifecycle fingerprint field),
and requires the same architecture verifier to turn RED with the expected drift
class. The repository workspace is hashed before/after and must remain unchanged.
"""

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any

import verify_governed_repair_architecture as architecture

ROOT = Path(__file__).resolve().parents[1]
MUTATED_PATH = "scripts/github_repair_authority.py"
EXPECTED_ERROR = 'authority_marker_missing:"lifecycle_contract_sha256"'


def _surface_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for relative in sorted(set(architecture.REQUIRED_FILES)):
        path = root / relative
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        if path.is_file():
            digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def verify(root: Path = ROOT) -> dict[str, Any]:
    root = root.resolve()
    before = _surface_fingerprint(root)
    baseline = architecture.verify(root)
    if baseline.get("status") != "PASS":
        return {
            "schema": "governed-repair-mutation-proof@1",
            "status": "FAIL",
            "reason": "baseline_architecture_not_green",
            "baseline_errors": baseline.get("errors") or [],
            "workspace_unchanged": _surface_fingerprint(root) == before,
        }

    with tempfile.TemporaryDirectory(prefix="governed-repair-mutation-") as temp:
        isolated = Path(temp)
        for relative in architecture.REQUIRED_FILES:
            source = root / relative
            if not source.is_file():
                return {
                    "schema": "governed-repair-mutation-proof@1",
                    "status": "FAIL",
                    "reason": f"required_surface_missing:{relative}",
                    "workspace_unchanged": _surface_fingerprint(root) == before,
                }
            target = isolated / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

        mutated = isolated / MUTATED_PATH
        text = mutated.read_text(encoding="utf-8")
        needle = '"lifecycle_contract_sha256"'
        if needle not in text:
            return {
                "schema": "governed-repair-mutation-proof@1",
                "status": "FAIL",
                "reason": "mutation_target_missing",
                "workspace_unchanged": _surface_fingerprint(root) == before,
            }
        mutated.write_text(
            text.replace(needle, '"lifecycle_contract_sha256_DRIFTED"'),
            encoding="utf-8",
        )
        killed = architecture.verify(isolated)

    errors = [str(item) for item in killed.get("errors") or []]
    workspace_unchanged = _surface_fingerprint(root) == before
    mutation_killed = killed.get("status") == "FAIL" and EXPECTED_ERROR in errors
    return {
        "schema": "governed-repair-mutation-proof@1",
        "status": "PASS" if mutation_killed and workspace_unchanged else "FAIL",
        "mutation": "write_grant_lifecycle_projection_drift",
        "mutated_path": MUTATED_PATH,
        "expected_error": EXPECTED_ERROR,
        "observed_errors": errors,
        "mutation_killed": mutation_killed,
        "workspace_unchanged": workspace_unchanged,
        "production_closed": False,
    }


def main() -> int:
    result = verify()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
