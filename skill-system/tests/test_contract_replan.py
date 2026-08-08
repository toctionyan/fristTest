from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
sys.path.insert(0, str(CONTROLLER))

from contract import REQUIRED_PROFILES, SKILL_ONLY_ALLOWED, SKILL_ONLY_FORBIDDEN  # type: ignore
import contract_replan_cli  # type: ignore


class ContractReplanTest(unittest.TestCase):
    def _payload(self, *, status: str = "review") -> dict:
        return {
            "schema_version": 1,
            "change_id": "old-change",
            "target_kind": "repair",
            "goal": "repair safely",
            "profile": "skill-only",
            "allowed_paths": list(SKILL_ONLY_ALLOWED),
            "forbidden_paths": list(SKILL_ONLY_FORBIDDEN),
            "invariants": ["product code unchanged"],
            "required_profiles": list(REQUIRED_PROFILES["skill-only"]),
            "writer_role": "skill-implementer",
            "review_roles": ["adversarial-reviewer", "release-judge"],
            "review_attestations": [],
            "decision_record": None,
            "variance_records": [],
            "repair_governance": "governance/repair-cases/old-change",
            "repair_governance_permit_digest": "permit-digest",
            "repair_governance_consumed_at": None,
            "verification": None,
            "status": status,
            "result": "PENDING",
        }

    def _workspace(self, tmp: str, payload: dict | None = None) -> tuple[Path, Path]:
        workspace = Path(tmp)
        active = workspace / "governance/active-change.json"
        active.parent.mkdir(parents=True, exist_ok=True)
        active.write_text(json.dumps(payload or self._payload()) + "\n", encoding="utf-8")
        evidence = workspace / "governance/replan-blocker.json"
        evidence.write_text('{"status":"BLOCKED"}\n', encoding="utf-8")
        return workspace, evidence

    def test_replan_archives_and_releases_active_pointer(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace, evidence = self._workspace(tmp)
            args = SimpleNamespace(
                successor_change_id="successor",
                reason="frozen Target is invalid",
                evidence=str(evidence),
            )
            with patch.object(contract_replan_cli, "_workspace", return_value=workspace):
                self.assertEqual(contract_replan_cli.cmd_replan(args), 0)

            self.assertFalse((workspace / "governance/active-change.json").exists())
            history = workspace / "governance/change-history/old-change"
            replanned = json.loads((history / "contract-replanned.json").read_text())
            record = json.loads((history / "replan.json").read_text())
            pending = json.loads((workspace / "governance/pending-replan.json").read_text())
            self.assertEqual(replanned["status"], "rejected")
            self.assertEqual(replanned["result"], "ARCHITECTURE_REPLAN_REQUIRED")
            self.assertIsNone(replanned["verification"])
            self.assertIsNone(replanned["repair_governance_consumed_at"])
            self.assertEqual(record["successor_change_id"], "successor")
            self.assertIsNone(record["repair_governance_consumed_at"])
            self.assertEqual(pending["predecessor_change_id"], "old-change")

    def test_replan_refuses_release_judge_verification_or_consumed_permit(self):
        mutations = [
            {"review_attestations": [{"role": "release-judge", "decision": "PASS"}]},
            {"verification": {"path": "x"}},
            {"repair_governance_consumed_at": "2026-01-01T00:00:00+00:00"},
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                payload = self._payload()
                payload.update(mutation)
                workspace, evidence = self._workspace(tmp, payload)
                args = SimpleNamespace(successor_change_id="successor", reason="reason", evidence=str(evidence))
                with patch.object(contract_replan_cli, "_workspace", return_value=workspace):
                    with self.assertRaises(SystemExit):
                        contract_replan_cli.cmd_replan(args)
                self.assertTrue((workspace / "governance/active-change.json").exists())

    def test_replan_requires_preserved_governance_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _ = self._workspace(tmp)
            outside = workspace / "blocker.json"
            outside.write_text("{}\n")
            args = SimpleNamespace(successor_change_id="successor", reason="reason", evidence=str(outside))
            with patch.object(contract_replan_cli, "_workspace", return_value=workspace):
                with self.assertRaisesRegex(SystemExit, "preserved under governance"):
                    contract_replan_cli.cmd_replan(args)

    def test_init_successor_requires_pending_id_and_binds_predecessor(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace, evidence = self._workspace(tmp)
            with patch.object(contract_replan_cli, "_workspace", return_value=workspace):
                contract_replan_cli.cmd_replan(SimpleNamespace(
                    successor_change_id="successor",
                    reason="target defect",
                    evidence=str(evidence),
                ))
                common = dict(
                    profile="skill-only",
                    goal="successor",
                    target_kind="repair",
                    allow=[],
                    forbid=[],
                    affected_module=[],
                    invariant=[],
                    minimum_mode="static",
                    quality_target=None,
                    baseline_evidence=None,
                    decision_record=None,
                    variance=[],
                    architecture_policy_delta=None,
                    baseline_policy_id=None,
                    repair_governance=None,
                    approve=False,
                )
                with self.assertRaisesRegex(SystemExit, "pending replan requires successor change_id"):
                    contract_replan_cli.cmd_init_successor(SimpleNamespace(change_id="wrong", **common))
                self.assertEqual(
                    contract_replan_cli.cmd_init_successor(SimpleNamespace(change_id="successor", **common)),
                    0,
                )

            active = json.loads((workspace / "governance/active-change.json").read_text())
            self.assertEqual(active["predecessor_change_id"], "old-change")
            self.assertEqual(active["replan_record"], "governance/change-history/old-change/replan.json")
            self.assertFalse((workspace / "governance/pending-replan.json").exists())

    def test_replan_history_is_immutable(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace, evidence = self._workspace(tmp)
            history = workspace / "governance/change-history/old-change"
            history.mkdir(parents=True)
            args = SimpleNamespace(successor_change_id="successor", reason="reason", evidence=str(evidence))
            with patch.object(contract_replan_cli, "_workspace", return_value=workspace):
                with self.assertRaisesRegex(SystemExit, "refusing to overwrite"):
                    contract_replan_cli.cmd_replan(args)
            self.assertTrue((workspace / "governance/active-change.json").exists())


if __name__ == "__main__":
    unittest.main()
