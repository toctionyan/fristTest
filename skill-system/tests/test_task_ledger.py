from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = ROOT / "skill-system" / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from task_ledger import REQUIRED_STAGE_IDS, REQUIRED_WORK_PACKAGE_IDS, summary, validate, validate_payload  # type: ignore


class TaskLedgerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = json.loads((ROOT / "governance" / "task-ledger.json").read_text(encoding="utf-8"))

    def test_canonical_ledger_is_valid(self) -> None:
        result = validate(ROOT)
        self.assertEqual(result.errors, ())
        self.assertEqual(tuple(row["stage_id"] for row in result.payload["stages"]), REQUIRED_STAGE_IDS)
        self.assertEqual(tuple(row["work_package_id"] for row in result.payload["work_packages"]), REQUIRED_WORK_PACKAGE_IDS)
        expected_active = next(
            row["stage_id"] for row in result.payload["stages"] if row["status"] != "CLOSED_VERIFIED"
        )
        self.assertEqual(summary(result.payload)["active_stage_id"], expected_active)

    def test_active_stage_must_be_first_unclosed_stage(self) -> None:
        payload = copy.deepcopy(self.payload)
        first_unclosed = next(
            row["stage_id"] for row in payload["stages"] if row["status"] != "CLOSED_VERIFIED"
        )
        invalid_active = next(
            row["stage_id"] for row in payload["stages"] if row["stage_id"] != first_unclosed
        )
        payload["active_stage_id"] = invalid_active
        result = validate_payload(ROOT, payload)
        self.assertTrue(
            any(f"first unclosed stage: {first_unclosed}" in error for error in result.errors)
        )

    def test_all_closed_ledger_requires_null_active_stage(self) -> None:
        payload = copy.deepcopy(self.payload)
        evidence = "governance/task-ledger.json"
        for package in payload["work_packages"]:
            package["status"] = "CLOSED_VERIFIED"
            package["blockers"] = []
            package["evidence_refs"] = [evidence]
        for stage in payload["stages"]:
            stage["status"] = "CLOSED_VERIFIED"
            stage["blockers"] = []
            stage["evidence_refs"] = [evidence]
        payload["active_stage_id"] = None
        self.assertEqual(validate_payload(ROOT, payload).errors, ())
        payload["active_stage_id"] = "STAGE-6"
        result = validate_payload(ROOT, payload)
        self.assertTrue(any("must be null" in error for error in result.errors))

    def test_duplicate_work_package_is_rejected(self) -> None:
        payload = copy.deepcopy(self.payload)
        payload["work_packages"][1]["work_package_id"] = "WP-01"
        result = validate_payload(ROOT, payload)
        self.assertTrue(any("duplicate work_packages id" in error for error in result.errors))

    def test_dependency_cycle_is_rejected(self) -> None:
        payload = copy.deepcopy(self.payload)
        payload["work_packages"][0]["depends_on"] = ["WP-02"]
        payload["work_packages"][1]["depends_on"] = ["WP-01"]
        result = validate_payload(ROOT, payload)
        self.assertTrue(any("dependency graph contains a cycle" in error for error in result.errors))

    def test_closed_item_requires_real_evidence(self) -> None:
        payload = copy.deepcopy(self.payload)
        payload["work_packages"][0]["status"] = "CLOSED_VERIFIED"
        payload["work_packages"][0]["evidence_refs"] = ["missing-evidence.json"]
        result = validate_payload(ROOT, payload)
        self.assertTrue(any("does not exist" in error for error in result.errors))


    def test_archived_evidence_identity_is_accepted(self) -> None:
        payload = copy.deepcopy(self.payload)
        payload["work_packages"][0]["status"] = "CLOSED_VERIFIED"
        payload["work_packages"][0]["blockers"] = []
        payload["work_packages"][0]["evidence_refs"] = [
            "governance/repair-cases/migration-v20.17-b19-task-ledger-quality-controller-modularization/evidence/original-failure.json"
        ]
        result = validate_payload(ROOT, payload)
        self.assertEqual(result.errors, ())

    def test_unindexed_missing_evidence_is_rejected(self) -> None:
        payload = copy.deepcopy(self.payload)
        payload["work_packages"][0]["status"] = "CLOSED_VERIFIED"
        payload["work_packages"][0]["blockers"] = []
        payload["work_packages"][0]["evidence_refs"] = ["missing-evidence.json"]
        result = validate_payload(ROOT, payload)
        self.assertTrue(any("does not exist or have an archive identity" in error for error in result.errors))

    def test_present_evidence_must_match_archive_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "governance").mkdir(parents=True)
            archive = json.loads((ROOT / self.payload["evidence_archive"]).read_text(encoding="utf-8"))
            ref = next(iter(archive["entries"]))
            target = workspace / ref
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("tampered", encoding="utf-8")
            archive_path = workspace / "governance" / "task-ledger-evidence-archive.json"
            archive_path.write_text(json.dumps(archive), encoding="utf-8")
            payload = copy.deepcopy(self.payload)
            payload["evidence_archive"] = "governance/task-ledger-evidence-archive.json"
            payload["work_packages"][0]["status"] = "CLOSED_VERIFIED"
            payload["work_packages"][0]["blockers"] = []
            payload["work_packages"][0]["evidence_refs"] = [ref]
            result = validate_payload(workspace, payload)
            self.assertTrue(any("does not match archive identity" in error for error in result.errors))

    def test_must_close_item_cannot_be_deferred(self) -> None:
        payload = copy.deepcopy(self.payload)
        payload["work_packages"][2]["status"] = "DEFERRED"
        payload["work_packages"][2]["decision_record"] = "governance/task-ledger.json"
        result = validate_payload(ROOT, payload)
        self.assertTrue(any("must-close item WP-03 cannot be DEFERRED" in error for error in result.errors))

    def test_non_must_close_exception_requires_decision_record(self) -> None:
        payload = copy.deepcopy(self.payload)
        payload["work_packages"][2]["must_close"] = False
        payload["work_packages"][2]["status"] = "DEFERRED"
        payload["work_packages"][2].pop("decision_record", None)
        result = validate_payload(ROOT, payload)
        self.assertTrue(any("requires decision_record" in error for error in result.errors))

    def test_missing_required_stage_is_rejected(self) -> None:
        payload = copy.deepcopy(self.payload)
        payload["stages"].pop()
        result = validate_payload(ROOT, payload)
        self.assertTrue(any("stage ids must be exactly" in error for error in result.errors))

    def test_scope_decision_requires_terminal_status_and_record(self) -> None:
        payload = copy.deepcopy(self.payload)
        payload["scope_decisions"][0]["status"] = "OPEN"
        payload["scope_decisions"][0]["decision_record"] = "missing.json"
        result = validate_payload(ROOT, payload)
        self.assertTrue(any("must be DEFERRED" in error for error in result.errors))
        self.assertTrue(any("does not exist" in error for error in result.errors))

    def test_cli_can_validate_from_an_explicit_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            path.write_text(json.dumps(self.payload, ensure_ascii=False), encoding="utf-8")
            # Evidence paths resolve against ROOT, so the direct semantic validator is authoritative here.
            self.assertEqual(validate_payload(ROOT, json.loads(path.read_text())).errors, ())


if __name__ == "__main__":
    unittest.main()
