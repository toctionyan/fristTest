from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = ROOT / "skill-system/controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from product_source_baseline_policy import (  # type: ignore
    BaselineMode,
    SnapshotSource,
    baseline_mode_for_authority,
    detect_snapshot_source,
    evaluate_binding,
    load_baseline_document,
    snapshot_protected_source,
    validate_baseline_document,
)


class ProductSourceBaselineBindingTests(unittest.TestCase):
    def test_baseline_contract_and_accepted_snapshot_binding(self) -> None:
        document = load_baseline_document(ROOT)
        mode = baseline_mode_for_authority("historical-registry-baseline")
        result = evaluate_binding(
            ROOT,
            expected=document.files,
            protected_roots=document.protected_roots,
            mode=mode,
            source=detect_snapshot_source(ROOT),
        )
        self.assertEqual(result.errors, ())

    def test_pr_candidate_protected_delta_does_not_promote_the_accepted_baseline(self) -> None:
        document = load_baseline_document(ROOT)
        candidate = dict(document.files)
        candidate["contracts/generated/unaccepted-candidate-proof.json"] = "a" * 64

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative, digest in document.files.items():
                if relative != next(iter(document.files)):
                    continue
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("synthetic\n", encoding="utf-8")
                current = {relative: "b" * 64}
                break
            else:
                self.fail("baseline must contain at least one file")

        self.assertNotEqual(candidate, document.files)
        self.assertEqual(
            validate_baseline_document(document.payload),
            [],
        )
        self.assertEqual(BaselineMode.PR_CANDIDATE.value, "pr_candidate")
        self.assertNotEqual(current, candidate)

    def test_same_synthetic_drift_fails_when_bound_as_accepted_ref(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "services/app.py"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("VALUE = 2\n", encoding="utf-8")
            result = evaluate_binding(
                root,
                expected={"services/app.py": "0" * 64},
                protected_roots=("services",),
                mode=BaselineMode.ACCEPTED_REF,
                source=SnapshotSource.OFFLINE_PACKAGE,
            )
        self.assertIn("protected_baseline_drift", result.errors)

    def test_pr_candidate_mode_still_fails_closed_on_baseline_document_tampering(self) -> None:
        document = load_baseline_document(ROOT)
        tampered = dict(document.payload)
        tampered["file_count"] = len(document.files) + 1
        errors = validate_baseline_document(tampered)
        self.assertIn("baseline_file_count_mismatch", errors)

    def test_machine_local_runtime_state_is_not_source_authority(self) -> None:
        machine_local = {
            "services/agent-service/runtime/sqlite/document_index_jobs.db",
            "services/agent-service/runtime/vector-store/vector_store.db",
        }
        document = load_baseline_document(ROOT)
        self.assertTrue(machine_local.isdisjoint(document.files))
        snapshot = snapshot_protected_source(
            ROOT,
            document.protected_roots,
            source=detect_snapshot_source(ROOT),
        )
        self.assertTrue(machine_local.isdisjoint(snapshot))

    def test_offline_workspace_fallback_excludes_runtime_state_but_keeps_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "services/agent-service/src/example.py"
            runtime_db = root / "services/agent-service/runtime/sqlite/local.db"
            source.parent.mkdir(parents=True, exist_ok=True)
            runtime_db.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("VALUE = 1\n", encoding="utf-8")
            runtime_db.write_bytes(b"machine-local")

            snapshot = snapshot_protected_source(
                root,
                ("services",),
                source=SnapshotSource.OFFLINE_PACKAGE,
            )

        self.assertIn("services/agent-service/src/example.py", snapshot)
        self.assertNotIn("services/agent-service/runtime/sqlite/local.db", snapshot)

    def test_baseline_does_not_claim_production_closure(self) -> None:
        task_ledger = json.loads(
            (ROOT / "governance/task-ledger.json").read_text(encoding="utf-8")
        )
        serialized = json.dumps(task_ledger, ensure_ascii=False)
        self.assertNotIn('"production_closed": true', serialized)


if __name__ == "__main__":
    unittest.main()
