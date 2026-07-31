from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tests.support.paths import workspace_root


CHANGE_ID = "migration-v20.17-b12b-transaction-runtime-boundary"
EXPECTED_SCOPE_HASH = "20a5231e81d90ca39ca17fbc0e68aa8378b8702a5770f0039cec46b0a3a806f2"
EXPECTED_ADVERSARIAL_HASH = "94a8e83098df9b4a498d7d14508ecf9c37f857f6574427db5e11cad7e563f48c"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_b12_closed_review_evidence_is_honestly_recovered() -> None:
    root = workspace_root(__file__)
    base = root / "docs/architecture/evidence-recovery" / CHANGE_ID
    prior_path = base / "prior-closed-change.json"
    recovery_path = base / "recovery.json"
    scope_path = base / "scope-planner.md"
    adversarial_path = base / "adversarial-reviewer.md"

    assert prior_path.is_file()
    assert recovery_path.is_file()
    assert scope_path.is_file()
    assert adversarial_path.is_file()

    prior = json.loads(prior_path.read_text(encoding="utf-8"))
    assert prior["change_id"] == CHANGE_ID
    assert prior["status"] == "closed"
    assert prior["result"] == "CONVERGED"
    recorded = {row["role"]: row for row in prior["review_attestations"]}
    assert recorded["scope-planner"]["evidence_sha256"] == EXPECTED_SCOPE_HASH
    assert recorded["adversarial-reviewer"]["evidence_sha256"] == EXPECTED_ADVERSARIAL_HASH

    recovery = json.loads(recovery_path.read_text(encoding="utf-8"))
    assert recovery["schema_version"] == 1
    assert recovery["change_id"] == CHANGE_ID
    assert recovery["incident"] == "closed_review_files_deleted_after_attestation"
    assert recovery["historical_missing_hashes"] == {
        "scope-planner": EXPECTED_SCOPE_HASH,
        "adversarial-reviewer": EXPECTED_ADVERSARIAL_HASH,
    }
    assert recovery["replacement_reviews"] == {
        "scope-planner": {"path": str(scope_path.relative_to(root)), "sha256": _sha(scope_path)},
        "adversarial-reviewer": {"path": str(adversarial_path.relative_to(root)), "sha256": _sha(adversarial_path)},
    }
    assert recovery["historical_hashes_reused_for_replacement"] is False
    assert recovery["product_implementation_changed"] is False
