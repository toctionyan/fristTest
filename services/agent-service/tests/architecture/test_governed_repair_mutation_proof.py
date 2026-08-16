from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import verify_governed_repair_mutation_proof as mutation_proof  # noqa: E402


def test_governed_repair_secondary_projection_drift_is_machine_blocked() -> None:
    result = mutation_proof.verify(ROOT)
    assert result.get("status") == "PASS", result
    assert result.get("mutation_killed") is True
    assert result.get("workspace_unchanged") is True
    assert result.get("mutation") == "write_grant_lifecycle_projection_drift"
