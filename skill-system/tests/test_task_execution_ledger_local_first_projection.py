from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "skill-system" / "controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from local_first_governance import (  # noqa: E402
    begin_local_repair_round,
    create_local_first_task,
    record_local_gate,
)
from task_execution_ledger import projection_inputs  # noqa: E402


class TaskExecutionLedgerLocalFirstProjectionTests(unittest.TestCase):
    def test_older_local_first_task_reconstructs_failed_then_recovered_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = create_local_first_task(
                Path(temp) / "task-run.json",
                task_id="local-history",
                change_id="change-1",
                base_sha="a" * 40,
                branch="feature/local-history",
                patch_owner="patch-owner",
                allowed_paths=("services/agent-service/src/example.py",),
                target_fingerprint="target-1",
            )
            begin_local_repair_round(
                store,
                workspace_fingerprint="workspace-1",
                evidence_refs=("round:1",),
            )
            record_local_gate(
                store,
                gate="targeted",
                passed=False,
                evidence_refs=("gate:targeted:1",),
                workspace_fingerprint="workspace-1",
            )
            begin_local_repair_round(
                store,
                workspace_fingerprint="workspace-2",
                evidence_refs=("round:2",),
            )
            record_local_gate(
                store,
                gate="targeted",
                passed=True,
                evidence_refs=("gate:targeted:2",),
                workspace_fingerprint="workspace-2",
            )
            planned, attempts = projection_inputs(store.payload)

        targeted = [row for row in attempts if row["stage_id"] == "local-targeted"]
        self.assertEqual([row["status"] for row in targeted], ["FAIL", "PASS"])
        self.assertEqual([row["attempt"] for row in targeted], [1, 2])
        current = {row["id"]: row["status"] for row in planned}
        self.assertEqual(current["local-targeted"], "PASS")
        self.assertEqual(current["local-module"], "PENDING")
        self.assertEqual(current["ci-certification"], "PENDING")


if __name__ == "__main__":
    unittest.main()
