#!/usr/bin/env python3
from __future__ import annotations

"""Fail closed if the Stage-2 executor's local path filter drifts from authority.

The write-grant compiler consumes the canonical path policy directly, so the
executor cannot widen authority.  This verifier protects the converse: a stale
stricter executor projection must not create false repair failures and repeated
loops after the canonical policy changes.
"""

import json
from pathlib import Path
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import github_agent_fixer as fixer  # noqa: E402
from governed_repair_path_policy import (  # noqa: E402
    RepairPathPolicyError,
    path_policy_violation,
    policy_fingerprint,
    policy_payload,
    validate_automatic_repair_paths,
)


SAMPLES = {
    "services/agent-service/src/agent_core/example.py": True,
    "services/business-service/app/example.py": True,
    "web/src/example.ts": True,
    "contracts/example.json": True,
    "services/agent-service/tests/test_example.py": False,
    "services/agent-service/src/example.test.ts": False,
    "services/agent-service/.env": False,
    "services/agent-service/pyproject.toml": False,
    ".github/workflows/quality.yml": False,
    "scripts/github_repair_authority.py": False,
}


def verify() -> dict[str, Any]:
    policy = policy_payload()
    errors: list[str] = []
    equality_checks = {
        "max_write_paths": fixer.MAX_FILES == policy["max_write_paths"],
        "supported_suffixes": set(fixer.SUPPORTED_SUFFIXES) == set(policy["supported_suffixes"]),
        "automatic_source_roots": tuple(fixer.AUTOMATIC_SOURCE_ROOTS)
        == tuple(policy["automatic_source_roots"]),
        "forbidden_path_parts": set(fixer.FORBIDDEN_PATH_PARTS)
        == set(policy["forbidden_path_parts"]),
        "forbidden_basenames": set(fixer.FORBIDDEN_BASENAMES)
        == set(policy["forbidden_basenames"]),
        "protected_prefixes": tuple(fixer.PROTECTED_PREFIXES)
        == tuple(policy["protected_prefixes"]),
        "protected_exact": set(fixer.PROTECTED_EXACT) == set(policy["protected_exact"]),
    }
    for key, passed in equality_checks.items():
        if passed is not True:
            errors.append(f"fixer_path_policy_projection_drift:{key}")

    behavioral: dict[str, bool] = {}
    with tempfile.TemporaryDirectory(prefix="fixer-path-policy-proof-") as temp:
        workspace = Path(temp)
        for relative in SAMPLES:
            target = workspace / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("pass\n", encoding="utf-8")

        for relative, expected_allowed in SAMPLES.items():
            canonical_static = path_policy_violation(relative) is None
            try:
                canonical = validate_automatic_repair_paths([relative])
                canonical_allowed = True
            except RepairPathPolicyError:
                canonical = ()
                canonical_allowed = False
            try:
                projected = fixer.validate_allowed_paths(workspace, [relative])
                projected_allowed = True
            except fixer.FixerError:
                projected = ()
                projected_allowed = False
            passed = (
                canonical_static == expected_allowed
                and canonical_allowed == expected_allowed
                and projected_allowed == expected_allowed
                and (not expected_allowed or projected == canonical)
            )
            behavioral[relative] = passed
            if not passed:
                errors.append(f"fixer_path_policy_behavior_drift:{relative}")

    errors = list(dict.fromkeys(errors))
    return {
        "schema": "governed-repair-fixer-path-policy-projection@1",
        "status": "PASS" if not errors else "FAIL",
        "canonical_path_policy_sha256": policy_fingerprint(),
        "structural_equality": equality_checks,
        "behavioral_samples": behavioral,
        "errors": errors,
        "write_authority_effect": False,
        "production_closed": False,
    }


def main() -> int:
    result = verify()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
